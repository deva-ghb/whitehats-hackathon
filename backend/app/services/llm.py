"""Model gateway: one door to every model, with budgets, fallbacks and a usage ledger.

Every AI feature calls `complete()` with a purpose (assistant, summarizer,
executive, ...). The gateway picks the enabled deployment assigned to that
purpose, tries deployments in priority order, records every call, and falls
back to the built-in rules engine so a screen never breaks because a model is
unreachable.
"""

from __future__ import annotations

import shutil
import subprocess
import os
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import LLMCall, ModelDeployment
from ..security import unseal

PURPOSES = ("assistant", "summarizer", "executive", "triage", "review")
# The hackathon build is Google only: Gemini on Vertex AI, with the rules engine as the offline fallback.
PROVIDERS = {
    "vertex": "Gemini on Vertex AI",
    "rules": "Built-in rules engine (offline)",
}
# Gemini signs in as the server's own Google identity (the Cloud Run service account, or the developer's gcloud
# sign-in locally) when no API key is given. No key is stored.
GEMINI_PROJECT = os.environ.get("WH_GEMINI_PROJECT", "")
GEMINI_LOCATION = os.environ.get("WH_GEMINI_LOCATION", "global")


def estimate_tokens(text: str) -> int:
    """Roughly 4 characters per token for English and logs; good enough for budgeting."""
    return max(1, len(text) // 4)


@dataclass
class Section:
    title: str
    text: str
    priority: int = 5  # 1 = must keep


@dataclass
class Packed:
    text: str
    tokens: int
    budget: int
    included: list[str]
    trimmed: list[str]
    dropped: list[str]


def pack_context(sections: list[Section], budget: int) -> Packed:
    """Fit sections into a token budget: highest priority first, the last one trimmed, the rest dropped."""
    out, included, trimmed, dropped, used = [], [], [], [], 0
    for s in sorted(sections, key=lambda s: s.priority):
        block = f"## {s.title}\n{s.text.strip()}\n"
        cost = estimate_tokens(block)
        if used + cost <= budget:
            out.append(block)
            included.append(s.title)
            used += cost
            continue
        room = budget - used
        if room > 60:
            cut = block[: room * 4]
            lines = cut.splitlines()[:-1] or [cut]
            out.append("\n".join(lines) + "\n[trimmed to fit the context budget]\n")
            trimmed.append(s.title)
            used = budget
        else:
            dropped.append(s.title)
    text = "\n".join(out)
    return Packed(text=text, tokens=estimate_tokens(text), budget=budget, included=included, trimmed=trimmed,
                  dropped=dropped)


@dataclass
class Result:
    text: str
    model: str
    fallback: bool
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    error: str = ""


class ProviderError(RuntimeError):
    pass


def _key(dep: ModelDeployment) -> str:
    from ..db import SessionLocal

    with SessionLocal() as db:
        return unseal(dep.secret_enc, db, dep.org_id)


def azure_gap(dep: ModelDeployment, key: str) -> str:
    """Name only the Azure settings that are actually missing."""
    missing = []
    if not key:
        missing.append("the API key")
    if not (dep.endpoint or "").strip():
        missing.append("the endpoint")
    if not (dep.deployment or "").strip():
        missing.append("the deployment name")
    if not missing:
        return ""
    if len(missing) == 1:
        return f"Add {missing[0]}."
    return "Add " + ", ".join(missing[:-1]) + " and " + missing[-1] + "."


def _call_azure(dep: ModelDeployment, system: str, user: str, max_tokens: int,
                response_format: dict | None = None) -> tuple[str, int, int]:
    key = _key(dep)
    gap = azure_gap(dep, key)
    if gap:
        raise ProviderError(gap)
    base = dep.endpoint.rstrip("/")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if dep.api_version in ("", "v1"):
        url = f"{base}/openai/v1/chat/completions"
        body = {"model": dep.deployment, "messages": messages, "max_completion_tokens": max_tokens}
    else:
        url = f"{base}/openai/deployments/{dep.deployment}/chat/completions?api-version={dep.api_version}"
        body = {"messages": messages, "max_completion_tokens": max_tokens}
    if response_format:
        body["response_format"] = response_format
    return _post_chat(url, {"api-key": key}, body)


def _call_openai_compatible(dep: ModelDeployment, system: str, user: str, max_tokens: int,
                            response_format: dict | None = None) -> tuple[str, int, int]:
    if not dep.endpoint or not dep.deployment:
        raise ProviderError("An OpenAI-compatible endpoint needs a base URL and a model name.")
    key = _key(dep)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": dep.deployment, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if response_format:
        body["response_format"] = response_format
    return _post_chat(dep.endpoint.rstrip("/") + "/chat/completions", headers, body)


def _post_chat(url: str, headers: dict, body: dict) -> tuple[str, int, int]:
    try:
        res = httpx.post(url, headers=headers, json=body, timeout=90)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach the model endpoint: {exc}") from exc
    if res.status_code >= 400:
        try:
            msg = res.json().get("error", {}).get("message", res.text)
        except ValueError:
            msg = res.text
        raise ProviderError(f"Model endpoint answered {res.status_code}: {str(msg)[:300]}")
    data = res.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if not text.strip():
        raise ProviderError("The model returned an empty answer (it may have used its whole output budget thinking).")
    usage = data.get("usage") or {}
    return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def _call_claude_cli(dep: ModelDeployment, system: str, user: str, max_tokens: int,
                     response_format: dict | None = None) -> tuple[str, int, int]:
    del response_format  # the CLI has no structured-output switch; the caller's prompt states the format
    if not shutil.which("claude"):
        raise ProviderError("The claude CLI is not installed on this server.")
    argv = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--strict-mcp-config",
            "--setting-sources", "project", "--tools", "", "--append-system-prompt", system]
    if dep.deployment:
        argv += ["--model", dep.deployment]
    try:
        proc = subprocess.run(argv, input=user, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProviderError("The claude CLI timed out.") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ProviderError(f"The claude CLI failed: {(proc.stderr or proc.stdout)[:300]}")
    return proc.stdout.strip(), estimate_tokens(system + user), estimate_tokens(proc.stdout)


def _google_token() -> str:
    """A short-lived token for the server's own Google identity."""
    import google.auth
    import google.auth.transport.requests

    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
    except google.auth.exceptions.GoogleAuthError as exc:
        raise ProviderError("This server has no Google identity to call Gemini with.") from exc
    return str(creds.token)


def _call_vertex(dep: ModelDeployment, system: str, user: str, max_tokens: int,
                 response_format: dict | None = None) -> tuple[str, int, int]:
    key = _key(dep)
    if not key and not GEMINI_PROJECT:
        raise ProviderError("Add the Vertex AI API key, or run WhiteHats with a Google identity (WH_GEMINI_PROJECT).")
    model = dep.deployment
    if not model.startswith("gemini-") or not all(c.isascii() and (c.isalnum() or c in "-.") for c in model):
        raise ProviderError("Enter a valid Gemini model ID.")
    config = {"maxOutputTokens": max_tokens}
    if response_format:
        config["responseMimeType"] = "application/json"
        if response_format.get("type") == "json_schema":
            schema = (response_format.get("json_schema") or {}).get("schema")
            if schema:
                config["responseJsonSchema"] = schema
    try:
        if key:
            url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"
            headers = {"x-goog-api-key": key}
        else:
            host = "aiplatform.googleapis.com" if GEMINI_LOCATION == "global" else \
                f"{GEMINI_LOCATION}-aiplatform.googleapis.com"
            url = (f"https://{host}/v1/projects/{GEMINI_PROJECT}/locations/{GEMINI_LOCATION}"
                   f"/publishers/google/models/{model}:generateContent")
            headers = {"Authorization": f"Bearer {_google_token()}"}
        reply = httpx.post(url, headers=headers, timeout=45,
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": config},
        )
        reply.raise_for_status()
        payload = reply.json()
        parts = payload["candidates"][0]["content"]["parts"]
        answer = "".join(part.get("text", "") for part in parts if not part.get("thought")).strip()
        if not answer:
            raise ValueError("Empty answer")
        usage = payload.get("usageMetadata", {})
        return answer, usage.get("promptTokenCount", 0), (
            usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        )
    except httpx.HTTPStatusError as exc:
        raise ProviderError(f"Vertex AI returned HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
        raise ProviderError("Vertex AI did not return a usable answer.") from exc


CALLERS = {"vertex": _call_vertex, "azure_openai": _call_azure, "openai_compatible": _call_openai_compatible, "claude_cli": _call_claude_cli}


def candidates(db: Session, org_id: int, purpose: str) -> list[ModelDeployment]:
    deps = db.scalars(
        select(ModelDeployment).where(ModelDeployment.org_id == org_id, ModelDeployment.enabled.is_(True))
        .order_by(ModelDeployment.priority, ModelDeployment.id)
    ).all()
    from ..models import Organization

    org = db.get(Organization, org_id)
    policy = (org.ai_policy or {}) if org else {}
    allowed = set(policy.get("providers") or []) if policy.get("agreed") else None
    zero = bool(policy.get("zero_retention"))

    def permitted(d: ModelDeployment) -> bool:
        # The tenant's agreed AI policy is enforced here, not just recorded.
        if allowed is not None and d.provider not in allowed:
            return False
        return not (zero and not d.zero_retention)

    return [d for d in deps if d.provider != "rules" and (purpose in (d.roles or []) or not d.roles) and permitted(d)]


def call_deployment(dep: ModelDeployment, system: str, user: str, max_tokens: int,
                    response_format: dict | None = None) -> tuple[str, int, int]:
    """`response_format` is the OpenAI chat field (for example a strict JSON schema); callers still validate."""
    from . import local_workspace

    if local_workspace.ENABLED and not (dep.provider == "vertex" and GEMINI_PROJECT):
        answer = "Review the evidence in this project and confirm the next step with the project owner."
        if response_format:
            answer = "{}"
        return answer, estimate_tokens(system + user), estimate_tokens(answer)
    if dep.provider not in CALLERS:
        raise ProviderError(f"Unknown provider {dep.provider}.")
    return CALLERS[dep.provider](dep, system, user, max_tokens, response_format)


def complete(db: Session, org_id: int, purpose: str, system: str, user: str, fallback: str,
             max_tokens: int | None = None, response_format: dict | None = None) -> Result:
    """Try each deployment for this purpose; on failure use `fallback`, the rules engine's answer."""
    errors = []
    for dep in candidates(db, org_id, purpose):
        budget = min(max_tokens or dep.max_output, dep.max_output)
        start = time.monotonic()
        try:
            text, p_tok, c_tok = call_deployment(dep, system, user, budget, response_format)
        except ProviderError as exc:
            ms = int((time.monotonic() - start) * 1000)
            errors.append(f"{dep.name}: {exc}")
            dep.status, dep.last_error = ("needs_key" if "API key" in str(exc) else "error"), str(exc)
            db.add(LLMCall(org_id=org_id, deployment_id=dep.id, model_label=dep.name, purpose=purpose,
                           prompt_tokens=estimate_tokens(system + user), latency_ms=ms, ok=False, error=str(exc)))
            continue
        ms = int((time.monotonic() - start) * 1000)
        dep.status, dep.last_error, dep.last_latency_ms = "ready", "", ms
        db.add(LLMCall(org_id=org_id, deployment_id=dep.id, model_label=dep.name, purpose=purpose,
                       prompt_tokens=p_tok or estimate_tokens(system + user),
                       completion_tokens=c_tok or estimate_tokens(text), latency_ms=ms))
        db.commit()
        return Result(text.strip(), dep.name, False, p_tok, c_tok, ms)
    db.add(LLMCall(org_id=org_id, model_label="Rules engine", purpose=purpose, ok=True, fallback=True,
                   prompt_tokens=0, completion_tokens=estimate_tokens(fallback), error="; ".join(errors)[:1000]))
    db.commit()
    return Result(fallback, "Rules engine", True, 0, estimate_tokens(fallback), 0, "; ".join(errors))


def test_deployment(db: Session, dep: ModelDeployment) -> dict:
    start = time.monotonic()
    dep.last_test_at = utcnow()
    try:
        if dep.provider == "rules":
            text = "ok"
        else:
            text, _, _ = call_deployment(dep, "You are a health check. Reply with the single word: ok",
                                         "Reply with ok.", 400)
        dep.status, dep.last_error = "ready", ""
        ok = True
    except ProviderError as exc:
        text, ok = str(exc), False
        dep.status = "needs_key" if "API key" in str(exc) else "error"
        dep.last_error = str(exc)
    dep.last_latency_ms = int((time.monotonic() - start) * 1000)
    db.add(LLMCall(org_id=dep.org_id, deployment_id=dep.id, model_label=dep.name, purpose="health_check",
                   latency_ms=dep.last_latency_ms, ok=ok, error="" if ok else text))
    db.commit()
    return {"ok": ok, "status": dep.status, "latency_ms": dep.last_latency_ms, "message": text[:300]}
