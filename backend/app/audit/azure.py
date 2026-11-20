"""The audit's door to Azure OpenAI: the Responses API, paced under each deployment's limits, retried with backoff.

Agents call tools, and on Azure the GPT-6 deployments take function tools only through `/openai/v1/responses`, so
this is separate from the one-shot gateway in `services/llm.py`. Every call is paced so a minute's requests and
tokens stay under a share of the deployment's limit, and every failure that can pass (a rate limit, a server
error, a dropped connection, a timeout) is retried with exponential backoff and full jitter, honouring any
`retry-after` the service sends. Each call's tokens and cost are written to the run's usage log.

Agents ask for a role, not a model. Each role is an ordered list of deployments, across two Azure resources: a call
moves to the next deployment when the provider's safety filter refuses it, when it is larger than a deployment's whole
minute, or when a deployment keeps answering "too many requests". One refused or throttled model is a detour, not a
failed agent.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("whitehats.audit.azure")

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 12
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 90.0
CONNECT_TIMEOUT_S = 15.0
MAX_DEADLINE_FACTOR = 3
HEADROOM = 0.8  # use at most this share of a minute's limit, leaving room for anything else on the deployment
WINDOW_S = 60.0


@dataclass(frozen=True)
class Model:
    """One Azure deployment and what it costs. Prices are per million tokens, from the listed Azure prices."""

    key: str
    deployment: str
    price_in: float
    price_cached: float
    price_out: float
    tokens_per_minute: int
    requests_per_minute: int
    # Longest wait for an answer. A request past it is treated like a dropped connection and retried: a healthy
    # answer comes well inside it, and a dead connection otherwise holds an agent for as long as the timeout.
    read_timeout_s: float = 300.0
    resource: str = ""  # "" for AZURE_OPENAI_ENDPOINT, "_2" for AZURE_OPENAI_ENDPOINT_2
    reasoning: bool = True  # takes `reasoning.effort` and returns encrypted reasoning (the GPT deployments)

    def cost(self, prompt: int, cached: int, output: int) -> float:
        return ((prompt - cached) * self.price_in + cached * self.price_cached + output * self.price_out) / 1_000_000


_TERRA = os.environ.get("WH_AUDIT_TERRA", "gpt-6.1-terra")
_LUNA = os.environ.get("WH_AUDIT_LUNA", "gpt-6-luna")
MODELS = {
    "terra": Model("terra", _TERRA, 1.20, 0.12, 6.00, 500_000, 500, 300.0),
    "luna": Model("luna", _LUNA, 0.10, 0.01, 0.50, 125_000, 125, 150.0),
    # DeepSeek V4 Pro on the second resource (Azure AI Foundry prices). The minute limits here are a starting guess;
    # each answer's rate-limit headers replace them with the deployment's real limits.
    "pro": Model("pro", os.environ.get("WH_AUDIT_PRO", "DeepSeek-V4-Pro"), 1.74, 0.145, 3.48, 20_000, 20, 300.0,
                 resource="_2", reasoning=False),
    "terra2": Model("terra2", _TERRA, 1.20, 0.12, 6.00, 500_000, 500, 300.0, resource="_2"),
    "luna2": Model("luna2", _LUNA, 0.10, 0.01, 0.50, 125_000, 125, 150.0, resource="_2"),
}
# Strong work (planning, hunting, verifying, proofs) starts on DeepSeek, which does not refuse defensive security work;
# cheap work (mapping, compaction notes) starts on Luna. Override with WH_AUDIT_STRONG / WH_AUDIT_CHEAP, comma-separated.
ROUTES = {
    "strong": os.environ.get("WH_AUDIT_STRONG", "pro,terra,terra2").split(","),
    "cheap": os.environ.get("WH_AUDIT_CHEAP", "luna,luna2,pro").split(","),
}
SPILL_AFTER = 3  # rate-limit answers from one deployment before a call moves on to the next one in its route


class ModelError(RuntimeError):
    """A call that cannot succeed by trying again: a bad request, a refusal, or every retry spent."""


class ContextTooLong(ModelError):
    pass


class DeadlineExceeded(RuntimeError):
    """A call that did not finish within its hard deadline; retried like a dropped connection."""


class PolicyBlocked(ModelError):
    """The provider's safety filter refused the request (Azure's `cyber_policy`). Retrying the same text will not pass."""


class Unavailable(ModelError):
    """This deployment cannot take the call now (too large for its minute, or throttled); the next one in the route may."""


@dataclass
class Pace:
    """A sliding minute of requests and tokens for one deployment, shared by every agent thread."""

    model: Model
    lock: threading.Lock = field(default_factory=threading.Lock)
    sent: deque = field(default_factory=deque)  # (time, tokens)
    paused_until: float = 0.0
    tokens_per_minute: int = 0  # the deployment's real limits, once an answer has told us; until then the model's
    requests_per_minute: int = 0

    def __post_init__(self) -> None:
        self.tokens_per_minute = self.tokens_per_minute or self.model.tokens_per_minute
        self.requests_per_minute = self.requests_per_minute or self.model.requests_per_minute

    @property
    def token_cap(self) -> int:
        return int(self.tokens_per_minute * HEADROOM)

    def _trim(self, now: float) -> None:
        while self.sent and now - self.sent[0][0] > WINDOW_S:
            self.sent.popleft()

    def acquire(self, tokens: int) -> list:
        """Wait until this request fits in the minute, then book it. Returns the booking to settle later."""
        token_cap = self.token_cap
        request_cap = max(1, int(self.requests_per_minute * HEADROOM))
        tokens = min(tokens, token_cap)  # one request larger than the cap still has to be able to go alone
        while True:
            with self.lock:
                now = time.monotonic()
                self._trim(now)
                used = sum(t for _, t in self.sent)
                if now >= self.paused_until and used + tokens <= token_cap and len(self.sent) < request_cap:
                    booking = [now, tokens]
                    self.sent.append(booking)
                    return booking
                wait = max(self.paused_until - now, (WINDOW_S - (now - self.sent[0][0])) if self.sent else 0.5)
            time.sleep(min(max(wait, 0.25), 5.0))

    def settle(self, booking: list, actual: int) -> None:
        with self.lock:
            booking[1] = actual

    def pause(self, seconds: float) -> None:
        with self.lock:
            self.paused_until = max(self.paused_until, time.monotonic() + seconds)


@dataclass
class Usage:
    calls: int = 0
    prompt: int = 0
    cached: int = 0
    output: int = 0
    cost: float = 0.0
    retries: int = 0
    detours: int = 0  # calls this deployment handed on to the next one in their route


class Client:
    """Thread-safe. One per audit run; the usage log is `<run>/usage.jsonl`."""

    def __init__(self, usage_log: Path, endpoint: str | None = None, key: str | None = None,
                 endpoint_2: str | None = None, key_2: str | None = None):
        endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        key = key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        if not endpoint or not key:
            raise ModelError("Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY for the audit.")
        endpoint_2 = endpoint_2 if endpoint_2 is not None else os.environ.get("AZURE_OPENAI_ENDPOINT_2", "")
        key_2 = key_2 if key_2 is not None else os.environ.get("AZURE_OPENAI_API_KEY_2", "")
        # resource suffix -> (responses URL, key); a resource that is not configured is skipped by every route
        self.resources = {"": (_responses_url(endpoint), key)}
        if endpoint_2 and key_2:
            self.resources["_2"] = (_responses_url(endpoint_2), key_2)
        # Each call opens its own connection (see _post): an idle connection Azure has already closed is never reused.
        self.transport: httpx.BaseTransport | None = None  # tests put a stand-in network here
        self.paces = {k: Pace(m) for k, m in MODELS.items()}
        self.usage = {k: Usage() for k in MODELS}
        self.usage_log = usage_log
        self.log_lock = threading.Lock()

    def route(self, key: str) -> list[str]:
        """The deployments a role (or a single model key) may use, in order, leaving out unconfigured resources."""
        return [k for k in ROUTES.get(key, [key]) if k in MODELS and MODELS[k].resource in self.resources]

    stop: Callable[[], bool] | None = None  # a cancelled audit makes no further model calls (AUD.7)

    def respond(self, role: str, body: dict, agent_id: str) -> dict:
        """POST one Responses request on the first deployment in the role's route that takes it. Retries what can
        pass on each; moves on when one refuses or cannot take it; raises the last error when none can."""
        if self.stop is not None and self.stop():
            raise ModelError("The audit was cancelled.")
        route = self.route(role)
        if not route:
            raise ModelError(f"No configured deployment for {role}.")
        for i, key in enumerate(route):
            last = i == len(route) - 1
            try:
                return self._respond_on(key, body, agent_id, spill=not last)
            except (PolicyBlocked, Unavailable) as exc:
                if last:
                    raise
                log.warning("audit %s: %s could not take the call (%s); trying %s", agent_id, MODELS[key].deployment,
                            str(exc)[:160], MODELS[route[i + 1]].deployment)
                with self.log_lock:
                    self.usage[key].detours += 1
        raise ModelError(f"No deployment for {role} took the call.")  # unreachable: the last raises above

    def _respond_on(self, model_key: str, body: dict, agent_id: str, spill: bool) -> dict:
        model = MODELS[model_key]
        url, key = self.resources[model.resource]
        body = _for_model(body, model)
        reserve = int(body.get("max_output_tokens", 8000))
        estimate = _prompt_estimate(body) + reserve  # how Azure counts a request against the minute
        pace = self.paces[model_key]
        if spill and estimate > pace.token_cap:
            raise Unavailable(f"the request (~{estimate} tokens) is larger than its {pace.tokens_per_minute}-token minute")
        timeouts = throttled = 0
        for attempt in range(MAX_ATTEMPTS):
            booking = pace.acquire(estimate)
            started = time.monotonic()
            # A call that ran out of time may simply need longer (deep reasoning on a hard lead): each timeout gives the
            # next try a longer deadline, up to three times the usual, instead of cutting it off at the same point.
            deadline = model.read_timeout_s * min(1 + timeouts, MAX_DEADLINE_FACTOR)
            try:
                res = self._post(url, key, body, deadline)
            except (httpx.TimeoutException, DeadlineExceeded) as exc:
                timeouts += 1
                self._backoff(model_key, pace, attempt, None, f"{type(exc).__name__}: {exc}", agent_id)
                continue
            except httpx.TransportError as exc:
                self._backoff(model_key, pace, attempt, None, f"{type(exc).__name__}: {exc}", agent_id)
                continue
            self._pace_from_headers(pace, res.headers)
            if res.status_code == 429 and spill:
                throttled += 1
                if throttled >= SPILL_AFTER:
                    raise Unavailable(f"answered 429 {throttled} times")
            if res.status_code in RETRY_STATUS:
                self._backoff(model_key, pace, attempt, res, res.text[:300], agent_id)
                continue
            if res.status_code >= 400:
                text = res.text[:600]
                if "context" in text.lower() and ("length" in text.lower() or "too long" in text.lower()):
                    raise ContextTooLong(text)
                if "cyber_policy" in text or "flagged for possible cybersecurity risk" in text:
                    raise PolicyBlocked(text)
                raise ModelError(f"{model.deployment} answered {res.status_code}: {text}")
            data = res.json()
            if data.get("status") == "incomplete" and not data.get("output"):
                reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
                raise ModelError(f"{model.deployment} returned an incomplete response with no output ({reason}).")
            self._record(model_key, data, agent_id, int((time.monotonic() - started) * 1000), booking, reserve)
            return data
        raise ModelError(f"{model.deployment}: every one of {MAX_ATTEMPTS} attempts failed.")

    def _post(self, url: str, key: str, body: dict, deadline_s: float) -> httpx.Response:
        """POST with a hard deadline on the whole call. A read timeout only notices silence; a connection stuck below
        the HTTP layer can hold an agent for hours. Each call runs on its own daemon thread with its own client, and
        past the deadline the caller abandons it and retries. Nothing waits on the stuck socket (closing it from here
        would wait on the lock its read holds), and an abandoned thread never keeps the process alive."""
        box: dict = {}

        def call() -> None:
            try:
                with httpx.Client(timeout=httpx.Timeout(deadline_s, connect=CONNECT_TIMEOUT_S),
                                  limits=httpx.Limits(max_keepalive_connections=0), transport=self.transport) as client:
                    box["response"] = client.post(url, headers={"api-key": key}, json=body)
            except httpx.HTTPError as exc:  # handed to the caller, which retries what can pass
                box["error"] = exc

        worker = threading.Thread(target=call, name="audit-call", daemon=True)
        worker.start()
        worker.join(deadline_s + CONNECT_TIMEOUT_S)
        if worker.is_alive():
            raise DeadlineExceeded(f"no complete answer within {deadline_s:.0f}s")
        if "error" in box:
            raise box["error"]
        return box["response"]

    def _backoff(self, model_key: str, pace: Pace, attempt: int, res: httpx.Response | None, why: str,
                 agent_id: str) -> None:
        delay = random.uniform(0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** attempt))  # full jitter
        if res is not None:
            hinted = _retry_after(res.headers)
            if hinted is not None:
                delay = max(delay, hinted + random.uniform(0, 1.0))
            if res.status_code == 429:
                pace.pause(delay)  # every thread on this deployment waits, not just the one that was told
        with self.log_lock:
            self.usage[model_key].retries += 1
        log.warning("audit %s %s retry %d in %.1fs: %s", agent_id, model_key, attempt + 1, delay, why[:200])
        time.sleep(delay)

    @staticmethod
    def _pace_from_headers(pace: Pace, headers: httpx.Headers) -> None:
        try:
            remaining = int(headers.get("x-ratelimit-remaining-tokens", "-1"))
            limit = int(headers.get("x-ratelimit-limit-tokens", "0"))
            request_limit = int(headers.get("x-ratelimit-limit-requests", "0"))
        except ValueError:
            return
        with pace.lock:  # the deployment's real limits, which change when its quota is raised
            pace.tokens_per_minute = limit or pace.tokens_per_minute
            pace.requests_per_minute = request_limit or pace.requests_per_minute
        if limit and 0 <= remaining < limit * (1 - HEADROOM) / 2:
            pace.pause(float(headers.get("x-ratelimit-reset-tokens", "0") or 0) or 5.0)

    def _record(self, model_key: str, data: dict, agent_id: str, ms: int, booking: list, reserve: int) -> None:
        usage = data.get("usage") or {}
        prompt = int(usage.get("input_tokens", 0))
        cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        output = int(usage.get("output_tokens", 0))
        model = MODELS[model_key]
        cost = model.cost(prompt, cached, output)
        self.paces[model_key].settle(booking, prompt + max(output, reserve))
        with self.log_lock:
            u = self.usage[model_key]
            u.calls, u.prompt, u.cached, u.output, u.cost = (u.calls + 1, u.prompt + prompt, u.cached + cached,
                                                             u.output + output, u.cost + cost)
            with self.usage_log.open("a") as f:
                f.write(json.dumps({"at": time.time(), "agent": agent_id, "model": model.deployment, "prompt": prompt,
                                    "cached": cached, "output": output, "cost": round(cost, 6), "ms": ms}) + "\n")

    def totals(self) -> dict:
        with self.log_lock:
            models = {k: vars(u).copy() for k, u in self.usage.items()}
        return {"models": models, "cost": round(sum(v["cost"] for v in models.values()), 4)}


def _responses_url(endpoint: str) -> str:
    host = endpoint.split("//", 1)[-1].split("/", 1)[0]
    return f"https://{host}/openai/v1/responses"


def _for_model(body: dict, model: Model) -> dict:
    """The request as this deployment takes it. Encrypted reasoning is readable only by the deployment that wrote it,
    so it goes to the model it came from (GPT deployments share it) and is left out for any other."""
    body = {**body, "model": model.deployment, "store": False}
    if model.reasoning:
        return {**body, "include": ["reasoning.encrypted_content"]}
    body.pop("reasoning", None)
    body["input"] = [i for i in body.get("input") or [] if not (isinstance(i, dict) and i.get("type") == "reasoning")]
    return body


def _prompt_estimate(body: dict) -> int:
    """Tokens the request's input will count as. Encrypted reasoning passed back is opaque text that does not count
    at its character length, so it is left out; the rest is about four characters a token."""
    def strip(item):
        return {k: v for k, v in item.items() if k != "encrypted_content"} if isinstance(item, dict) else item
    visible = {**body, "input": [strip(i) for i in body.get("input") or []]}
    return len(json.dumps(visible)) // 4


def _retry_after(headers: httpx.Headers) -> float | None:
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = headers.get(name)
        if value:
            try:
                return float(value) * scale
            except ValueError:
                continue
    return None
