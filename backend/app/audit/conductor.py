"""Run a security audit of one repository snapshot, following the vendored method's six steps.

1. Map: four reconnaissance agents (cheap route) read the snapshot; a planner (strong route) writes
   `architecture.md` and the coverage units. The ledger is built here, deterministically, and checked by the method's validator.
2. Hunt: hunters (strong route) each take the units of one attack class and return the method's structured result.
3. Coverage critic (strong route): one pass for the quick profile; what it finds is recorded as deferred, never hidden.
4. Disprove: every unique candidate goes to a fresh verifier (strong route) that did not hunt it and tries to refute it.
   A record is `confirmed` only with a bounded local check the verifier ran itself in the sealed container.
5. Structured output: `findings.json`, checked by the method's validator; a record that fails is repaired once.
6. Reports, written here from the records: `REPORT.md`, `FINDINGS-DETAIL.md`, `NEEDS-VALIDATION.md`.

Resilient by design: agents run in parallel under the models' rate limits; a failed hunter is retried once with a
fresh owner, then its units are deferred with the reason; a failed verifier is retried once, then its candidate
stays an unvalidated candidate and the run is marked incomplete. Every finished agent is kept on disk, so running
the same command again resumes the audit instead of paying for it twice. A dollar budget stops new work cleanly.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import jsonschema

from . import agents, tools
from . import method_text as m
from .azure import Client

log = logging.getLogger("whitehats.audit")

QUICK_SUBSYSTEM = "profile/quick/all-in-scope-subsystems"
# A pull request's review (AUD.5): the quick method narrowed to one change. It starts from the architecture an earlier
# audit mapped (PRIOR_ARCHITECTURE in the run folder, when there is one) and the change's diff (CHANGE_DIFF), plans a
# few units around the changed files, and skips the coverage critic. Verification and proofs are the same as ever.
CHANGE = "change"
PRIOR_ARCHITECTURE = "prior-architecture.md"
CHANGE_DIFF = "change.diff"
SPHERE = "sphere.md"  # KS.1, app/sphere/brief.py
MAX_DIFF_CHARS = 40_000
MAX_HUNTERS = 12
PARALLEL = 8
FINGERPRINT_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/@+-")


class Cancelled(RuntimeError):
    """A person cancelled the audit while it ran (AUD.7)."""


@dataclass
class Run:
    target: Path
    out: Path
    repo: str
    source_ref: str
    profile: str = "quick"
    budget_usd: float = 15.0
    scope: list[str] = field(default_factory=list)  # repository paths a scoped audit covers; empty is everything
    scope_reason: str = ""  # in words, why the audit is scoped (the cloud change that started it)
    listener: Callable[[dict], None] | None = None  # told of every progress line, for the screens
    cancelled: Callable[[], bool] | None = None  # asked at every progress line: a person cancelled the audit
    started: float = field(default_factory=time.time)
    events: Path = field(init=False)

    def __post_init__(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.events = self.out / "events.jsonl"

    def say(self, step: str, message: str, **extra) -> None:
        """One line of progress, for the console and for the screens that draw an audit as it runs. A cancelled audit
        stops here, at its next line of progress."""
        if self.cancelled is not None and self.cancelled():
            raise Cancelled("A person cancelled this audit.")
        entry = {"at": round(time.time() - self.started, 1), "step": step, "message": message, **extra}
        with self.events.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        log.info("[%s] %s", step, message)
        if self.listener is not None:
            try:
                self.listener(entry)
            except Exception:  # a screen that cannot be told must never stop the audit
                log.exception("audit listener failed")


def audit(run: Run, client: Client) -> dict:
    snap = tools.Snapshot.at(run.target)
    facts = run.out / "cloud-facts.json"
    snap.cloud = json.loads(facts.read_text()) if facts.exists() else []
    sandbox = tools.sandbox_ready()
    _metadata(run, "in_progress")
    run.say("start", f"Auditing {run.repo} at {run.source_ref}: {len(snap.files)} files, profile {run.profile}.",
            sandbox=sandbox or "ready")
    if not sandbox:
        snap.deps = run.out / "deps"
        notes_file = run.out / "deps-notes.json"
        if not notes_file.exists():
            run.say("start", "Installing the code's pinned libraries for local checks (ready-built packages only).")
            notes_file.write_text(json.dumps(tools.prepare_dependencies(snap, snap.deps)))
        snap.deps_notes = json.loads(notes_file.read_text())

    snap.leads = _scanner_leads(run, snap)
    if run.profile == CHANGE:
        # A pull request's review looks at what the change touches; leads elsewhere belong to the next full audit.
        snap.leads = [lead for lead in snap.leads if any(_under(lead.get("file", ""), p) for p in run.scope)]
    architecture, units = _map(run, client, snap)
    ledger = {u["coverage_id"]: u for u in units}
    _write_ledger(run, ledger)
    run.say("map", f"{len(units)} coverage units planned.", units=[_unit_view(u) for u in units])

    candidates, extra = _hunt(run, client, snap, architecture, ledger, bool(not sandbox))
    if run.profile != CHANGE:
        _critic(run, client, snap, architecture, ledger, candidates)
    _write_ledger(run, ledger)

    candidates = merge_duplicates(run, ledger, candidates)
    records, unvalidated = _verify(run, client, snap, architecture, ledger, candidates, bool(not sandbox))
    records = merge_confirmed(run, ledger, records)
    records = _validated_findings(run, client, snap, records)
    _lineage(run, records)
    if not sandbox:
        _proofs(run, client, snap, records)
    _write_ledger(run, ledger)
    _reports(run, ledger, records, extra, unvalidated, client)
    status = "incomplete" if unvalidated else "complete"
    _metadata(run, status, incomplete_reason="validation_budget_exhausted" if unvalidated else None)
    summary = _summary(run, ledger, records, client, status)
    (run.out / "summary.json").write_text(json.dumps(summary, indent=1))
    run.say("done", f"Audit {status}: {summary['counts']}, ${summary['cost']:.2f}, {summary['minutes']} min.",
            summary=summary)
    return summary


# ------------------------------------------------------------------ 0. scanner leads

SCANNER_ENGINES_ALWAYS = ("gitleaks", "osv-scanner", "trivy", "whitehats")  # secrets, libraries, images, house rules
MAX_LEADS = 60


def _scanner_leads(run: Run, snap: tools.Snapshot) -> list[dict]:
    """Run WhiteHats' own scanners on the snapshot and keep what they raise as leads (`scanner-leads.json`).

    Scanners are fast and reliable at what they know (secrets, vulnerable libraries and images, risky identity
    policy, dangerous calls) and that is where reading agents miss most. A lead is prior evidence for the plan and
    the hunt, never a finding: a hunter checks it like any other candidate, and a verifier still has to confirm it.
    Infrastructure checks are noisy, so only their high and critical results are kept.
    """
    path = run.out / "scanner-leads.json"
    if path.exists():
        return json.loads(path.read_text())
    from ..decisions_api.decide import decide  # the platform's scanner runner; imported here to keep the engine light

    run.say("start", "WhiteHats' scanners are reading the code for leads.")
    try:
        decision = decide(snap.root)
    except (OSError, RuntimeError, ValueError) as exc:
        run.say("start", f"The scanners could not run ({exc}); the audit goes on without their leads.")
        return []
    leads = []
    for s in decision.suspects:
        if s.engine not in SCANNER_ENGINES_ALWAYS and s.severity not in ("high", "critical"):
            continue
        if s.evidence.path not in snap.files:
            continue
        leads.append({"engine": s.engine, "rule": s.rule_id, "severity": s.severity, "title": s.title,
                      "path": s.evidence.path, "line": s.evidence.start_line, "cwe": list(s.cwe)})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    leads = sorted(leads, key=lambda x: (order.get(x["severity"], 4), x["path"], x["line"]))[:MAX_LEADS]
    failed = [r.engine for r in decision.engines if r.error]
    path.write_text(json.dumps(leads, indent=1))
    run.say("start", f"Scanners raised {len(leads)} leads" + (f"; these did not run: {', '.join(failed)}" if failed
                                                              else "") + ".", leads=len(leads))
    return leads


def _leads_text(leads: list[dict]) -> str:
    return "\n".join(f"- [{x['severity']}] {x['engine']} {x['rule']}: {x['title']} ({x['path']}:{x['line']})"
                     for x in leads)


# ------------------------------------------------------------------ 1. map

def _map(run: Run, client: Client, snap: tools.Snapshot) -> tuple[str, list[dict]]:
    tree = tools.list_files(snap, "")
    change = run.profile == CHANGE
    prior = run.out / PRIOR_ARCHITECTURE
    diff = (run.out / CHANGE_DIFF).read_text()[:MAX_DIFF_CHARS] if (run.out / CHANGE_DIFF).exists() else ""
    if change and prior.exists():
        # A pull request's review starts from the map an earlier audit of this repository made, and reads the change.
        run.say("map", "Starting from the architecture an earlier audit mapped; planning around the change.")
        architecture, units = _plan(run, client, snap, tree, "## Architecture an earlier audit of this repository "
                                    "mapped (check what the change alters)\n" + prior.read_text(), diff)
        return architecture, units
    recon = {}

    def one(key: str, prompt: str) -> tuple[str, dict | None]:
        spec = agents.Spec(
            agent_id=f"recon-{key}", model="cheap", effort="medium", max_turns=30, max_output_tokens=8_000,
            instructions=(AUTHORISED + "You are a reconnaissance agent in a defensive, source-only security audit of a repository "
                          "snapshot. You read files with the tools; you never run anything. Submit facts with "
                          "repository-relative file:line references."),
            task=f"Repository files:\n{tree}\n\n{prompt}\n\nSubmit {{\"facts\": \"<markdown>\"}}.",
            submit_description="Submit your facts.",
            check=lambda a: [] if len(str(a.get("facts", ""))) > 200 else ["facts must be a substantial markdown text"])
        return key, _guarded(run, client, snap, spec)

    run.say("map", "Four reconnaissance agents are reading the code.")
    with ThreadPoolExecutor(4) as pool:
        for key, result in pool.map(lambda kv: one(*kv), m.recon_prompts().items()):
            recon[key] = result["facts"] if result else "(this reconnaissance agent failed)"

    facts = "\n\n".join(f"## Reconnaissance {k}\n{v}" for k, v in sorted(recon.items()))
    return _plan(run, client, snap, tree, facts, diff if change else "")


def _plan(run: Run, client: Client, snap: tools.Snapshot, tree: str, facts: str, diff: str) -> tuple[str, list[dict]]:
    change = run.profile == CHANGE
    # KS.1: the business rules and accepted exceptions the project's approvers confirmed (written by the platform)
    sphere = (run.out / SPHERE).read_text() if (run.out / SPHERE).exists() else ""
    if sphere:
        facts = f"{facts}\n\n{sphere}"
    least, most = (1, 8) if change else (3, 60)
    plan_spec = agents.Spec(
        agent_id="planner", model="strong", effort="medium" if change else "high", max_turns=30,
        max_output_tokens=20_000 if change else 40_000,
        instructions=(AUTHORISED + ("You plan the review of one pull request's change, as a focused security audit. "
                                    if change else "You plan the coverage of a quick-profile security audit. ")
                      + "You read source to check facts; you do not hunt. Units are coarse: one per material entry "
                        "surface x trust boundary x attack class."),
        task=_plan_task(tree, facts, run.scope, run.scope_reason, snap.leads, diff=diff),
        submit_description="Submit the architecture summary and the coverage units.",
        check=lambda a: _check_plan(a, snap, least, most) + _leads_uncovered(a, snap.leads))
    plan = _guarded(run, client, snap, plan_spec)
    if plan is None:
        raise RuntimeError("The planner failed twice; the audit cannot plan coverage.")
    architecture = plan["architecture_md"]
    if diff:
        # Hunters and verifiers read the architecture verbatim: for a pull request it ends with the change itself.
        architecture += f"\n\n## The change under review ({run.scope_reason})\n```diff\n{diff}\n```\n"
    (run.out / "architecture.md").write_text(architecture)
    # Hunters and verifiers read the sphere with the architecture; it is not saved into the map a later review reuses.
    return (f"{architecture}\n\n{sphere}" if sphere else architecture), _build_units(plan)


def _plan_task(tree: str, facts: str, scope: list[str], reason: str, leads: list[dict], diff: str = "") -> str:
    scoped = ""
    if diff:
        scoped = f"""
This reviews one pull request ({reason}). Plan units only for what the change can alter: the surfaces, trust
boundaries and data it touches in the changed files, and where the code it changes is called from or calls into.
Ask what the change lets a caller do that it could not do before (a value the caller now controls, a check removed
or loosened, a new route or parameter). Between 1 and 8 units. Every changed file below is in some unit's
starting_paths: {", ".join(scope)}.

The change:
```diff
{diff}
```
"""
    elif scope:
        scoped = f"""
This is a scoped audit ({reason}). Plan units only for surfaces and boundaries inside these paths, and units
whose trust boundary those paths change: {", ".join(scope)}. Fewer units are expected (at least 3).
"""
    elif reason:
        scoped = f"""
This is a focused re-audit because the live account changed: {reason}. Plan units only for the surfaces and trust
boundaries that change could affect (the resource it touches, the code and infrastructure that declare or call it,
and the identities around it); use cloud_facts to see the live settings. Fewer units are expected (at least 3).
"""
    return f"""{scoped}Repository files:
{tree}

Reconnaissance facts:
{facts}

Leads WhiteHats' scanners raised on this code (prior evidence, not findings). Every lead's file must be in the
starting_paths of a unit whose attack class fits it (secrets: cryptography and secrets; libraries, images and CI:
supply chain; identity policy: cloud and deployment; dangerous calls: injection or file handling):
{_leads_text(leads) or "(none)"}

Write:
1. "architecture_md": the architecture summary hunters will read verbatim: product, principals, trust boundaries and
   where they are enforced (file:line), entry surfaces, sinks, deployment facts source cannot establish. At most
   1,500 words.
2. "units": the coverage units. Each unit is one material combination of entry surface, trust boundary and attack
   class. Cover every entry surface and boundary the facts name, including infrastructure as code, identity policy,
   dependency manifests, container images, CI workflows and secrets in settings. Choose "attack_class_ref" only
   from this catalogue (copy a line exactly):
{m.catalogue()}
   Fields per unit: "surface" (label), "surface_ref" (stable source reference such as "shop/http_api.py#GET /orders"),
   "boundary" (label), "boundary_ref" (the source control that defines it, such as "gatekeeper/authorizer.py#handler"),
   "attack_class_ref", "starting_paths" (existing repository-relative files), "priority" (1 highest to 5).
   References must be plain visible text with no surrounding spaces. Between 12 and 40 units.
3. "excluded_companions": every companion file you did not use, each {{"file": "...", "reason": "<source fact>"}}.

Submit {{"architecture_md": "...", "units": [...], "excluded_companions": [...]}}."""


def _check_plan(plan: dict, snap: tools.Snapshot, least: int = 3, most: int = 60) -> list[str]:
    problems = []
    if len(str(plan.get("architecture_md", ""))) < 400:
        problems.append("architecture_md is missing or too short")
    units = plan.get("units")
    if not isinstance(units, list) or not least <= len(units) <= most:
        return problems + [f"units must be a list of {least} to {most} units"]
    seen = set()
    for i, u in enumerate(units):
        where = f"units[{i}]"
        for key in ("surface", "surface_ref", "boundary", "boundary_ref", "attack_class_ref"):
            if not _visible(u.get(key)):
                problems.append(f"{where}.{key} must be plain visible text")
        if m.block(str(u.get("attack_class_ref", ""))) is None:
            problems.append(f"{where}.attack_class_ref {u.get('attack_class_ref')!r} is not a catalogue line")
        paths = u.get("starting_paths") or []
        missing = [p for p in paths if p not in snap.files]
        if not paths or missing:
            problems.append(f"{where}.starting_paths must list existing files; not found: {missing}")
        key = (u.get("surface_ref"), u.get("boundary_ref"), u.get("attack_class_ref"))
        if key in seen:
            problems.append(f"{where} repeats another unit's surface, boundary and attack class")
        seen.add(key)
    for e in plan.get("excluded_companions") or []:
        if e.get("file") not in m.COMPANIONS or not _visible(e.get("reason")):
            problems.append(f"excluded_companions entry {e} needs a companion file name and a reason")
    return problems


def _leads_uncovered(plan: dict, leads: list[dict]) -> list[str]:
    covered = {p for u in plan.get("units") or [] if isinstance(u, dict) for p in u.get("starting_paths") or []}
    missing = sorted({x["path"] for x in leads if x["path"] not in covered})
    return [f"these files carry scanner leads but are in no unit's starting_paths: {missing}"] if missing else []


def _visible(value) -> bool:
    return (isinstance(value, str) and value == value.strip() and bool(value)
            and unicodedata.normalize("NFC", value) == value
            and not any(unicodedata.category(c)[0] == "C" for c in value))


def _encode(ref: str) -> str:
    return urllib.parse.quote(ref, safe="-._~")


def _under(path: str, scope: str) -> bool:
    """True when a repository path is the scoped path or inside it (a scope entry may be a file or a folder)."""
    path, scope = str(path).strip("/"), str(scope).strip("/")
    return bool(scope) and (path == scope or path.startswith(scope + "/"))


def coverage_id(refs: dict) -> str:
    parts = [refs["surface"], refs["boundary"], refs["subsystem"], refs["attack_class"]]
    if "lifecycle" in refs:
        parts.append(refs["lifecycle"])
    return "::".join(_encode(p) for p in parts)


def _build_units(plan: dict) -> list[dict]:
    excluded = [{"block": f"{e['file']}#Core discipline", "reason": e["reason"]}
                for e in plan.get("excluded_companions") or []]
    units = {}
    for u in plan["units"]:
        ref = u["attack_class_ref"]
        name_file = ref.split("#", 1)[0]
        ordinary = ref if name_file == m.ATTACK_CLASSES else None
        companions = [] if ordinary else [ref] + [f"{name_file}#{f}" for f in m.COMPANION_FRAME]
        refs = {"surface": u["surface_ref"], "boundary": u["boundary_ref"], "subsystem": QUICK_SUBSYSTEM,
                "attack_class": ref}
        cid = coverage_id(refs)
        if cid in units:
            continue
        units[cid] = {
            "coverage_id": cid, "canonical_refs": refs, "surface": u["surface"], "boundary": u["boundary"],
            "subsystem": "All in-scope subsystems (quick profile)", "attack_class": ref.split("#", 1)[1],
            "starting_paths": sorted(set(u["starting_paths"])), "ordinary_attack_class_block": ordinary,
            "selected_companion_blocks": companions,
            "excluded_blocks": [e for e in excluded if e["block"] not in companions],
            "prior_status": "none", "attempts": [], "wave": 1, "status": "planned", "agent_id": None,
            "reviewed_paths": [], "local_checks": [], "result_fingerprints": [], "unresolved": [],
            "priority": int(u.get("priority") or 3)}
    return [units[k] for k in sorted(units)]


# ------------------------------------------------------------------ 2. hunt

def _hunt(run: Run, client: Client, snap: tools.Snapshot, architecture: str, ledger: dict,
          can_check: bool) -> tuple[dict[str, dict], dict]:
    groups: dict[str, list[str]] = defaultdict(list)
    for cid, u in sorted(ledger.items(), key=lambda kv: (kv[1]["priority"], kv[0])):
        groups[u["canonical_refs"]["attack_class"]].append(cid)
    batches = [ids for _, ids in sorted(groups.items(), key=lambda kv: min(ledger[c]["priority"] for c in kv[1]))]
    while len(batches) > MAX_HUNTERS:  # fold the two smallest groups together, never drop one
        batches.sort(key=len)
        batches = [batches[0] + batches[1]] + batches[2:]
    run.say("hunt", f"{len(batches)} hunters are taking {len(ledger)} units.")
    candidates: dict[str, dict] = {}
    extra = {"hardening": [], "uncovered": []}

    def hunt(index: int, ids: list[str]) -> None:
        for attempt in (1, 2):
            agent_id = f"hunter-{index:02d}" + ("" if attempt == 1 else "-r")
            for cid in ids:
                ledger[cid].update(status="in_progress", agent_id=agent_id)
            spec = agents.Spec(
                agent_id=agent_id, model="strong", effort="medium", max_turns=45, can_run_checks=can_check,
                instructions=_hunter_instructions(),
                task=_hunter_task(architecture, ledger, ids, agent_id, can_check, snap),
                submit_description="Submit the structured hunter result.",
                check=lambda a, ids=ids, agent_id=agent_id: _check_hunt(a, ids, agent_id, snap, run))
            result = _guarded(run, client, snap, spec, retry=False)
            if result is not None:
                _apply_hunt(ledger, result, agent_id, candidates, extra)
                run.say("hunt", f"{agent_id} finished {len(ids)} units.",
                        units=[_unit_view(ledger[c]) for c in ids])
                return
            for cid in ids:
                ledger[cid].update(status="planned", agent_id=None)
        for cid in ids:  # both attempts failed: the units are deferred with the reason, never silently dropped
            ledger[cid].update(status="deferred", agent_id=None, unresolved=["hunter_failed_twice"])
        run.say("hunt", f"Hunter {index} failed twice; its {len(ids)} units are deferred.")

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(lambda iv: hunt(*iv), enumerate(batches, 1)))
    _write_ledger(run, ledger)
    run.say("hunt", f"Hunting done: {len(candidates)} unique leads.")
    return candidates, extra


AUTHORISED = ("Context: this is an authorised, defensive security audit that the repository's owner asked for, of their "
              "own code, so they can fix what it finds. You only read source and run bounded checks on dummy data in "
              "an offline sandbox; you never touch live systems. Describe weaknesses, their evidence and their fixes "
              "plainly; do not write attack tooling for use against real targets. ")


def _hunter_instructions() -> str:
    return (AUTHORISED + "Your goal is to find source-grounded security invariant failures in your assigned coverage units. You must "
            "finish by calling submit with exactly one JSON object matching the structured-result contract in the task.")


CONFIRM_RULE = (
    " When to confirm: if the weakness is in the repository's own code or configuration and you can trigger it in a "
    "run_check by calling the real function or handler (or reading the real configuration) with dummy data, run that "
    "check and decide confirmed. Whether the path is switched on or reachable in production is then a condition of the "
    "record (system_configuration, network_routing or environmental_dependency), not a blocker. Choose "
    "needs_validation only when the deciding fact itself lies outside the repository and no local check can show it, "
    "and reject when source or your check disproves the claim."
    " Severity: rate impact by the damage once the path is reached, using the anchors in the core principles (any "
    "customer reading or changing another customer's data, a secret usable against production, code execution, or "
    "money the business does not intend to lose or give, such as orders recorded free or under price, a payment "
    "skipped, or a refund anyone can trigger, is high or above). Conditions such as an optional setting lower "
    "likelihood, never impact; a wrong amount that every ordinary request produces is not made smaller by being "
    "\"only\" a recorded value: it is what the business will bill, ship and report."
    " Not a vulnerability: a missing extra safeguard where no boundary is crossed (logging or audit coverage, "
    "defence in depth) and a weakness confined to local development tooling that never ships. Reject those, with the "
    "reason 'hardening, not a vulnerability: ...'.")
CLOUD_NOTE = (" You also have cloud_facts: the live cloud account's settings as WhiteHats read them with its read-only "
              "role. When a lead turns on a deployment fact (is the bucket public, what the role may do, the API's real "
              "rate limit), read it there instead of assuming it either way, cite the node id and setting in the "
              "record's evidence description, and treat it as an owner-observed deployment fact. A cloud fact settles "
              "a condition; the boundary failure itself still needs its source trace and, for confirmed, a local check.")


def _sandbox_facts(snap: tools.Snapshot) -> str:
    return (" The code's pinned Python libraries are importable from /deps (PYTHONPATH has /scratch and /deps); run "
            "repository code with sys.path.insert(0, '/target/<folder>')." + "".join(f" {n}" for n in snap.deps_notes)
            + (CLOUD_NOTE if snap.cloud else ""))


def _hunter_leads(ledger: dict, ids: list[str], snap: tools.Snapshot) -> str:
    paths = {p for c in ids for p in ledger[c]["starting_paths"]}
    mine = [x for x in snap.leads if x["path"] in paths]
    if not mine:
        return "(none)"
    return ("WhiteHats' scanners raised these on files in your units. They are leads, not findings: check each one "
            "like any other candidate, raise it as a candidate when it holds (with your own trace and evidence), and "
            "when it does not, say why in hardening, naming the rule.\n" + _leads_text(mine))


def _hunter_task(architecture: str, ledger: dict, ids: list[str], agent_id: str, can_check: bool,
                 snap: tools.Snapshot) -> str:
    units = [{k: ledger[c][k] for k in ("coverage_id", "surface", "boundary", "subsystem", "attack_class",
                                        "starting_paths", "ordinary_attack_class_block", "selected_companion_blocks",
                                        "excluded_blocks")} for c in ids]
    refs = sorted({b for c in ids for b in ([ledger[c]["ordinary_attack_class_block"]] if ledger[c][
        "ordinary_attack_class_block"] else []) + ledger[c]["selected_companion_blocks"]})
    blocks = "\n\n".join(f"### {r}\n{m.block(r)}" for r in refs)
    execution = (
        "Local checks: you have run_check, which is the approved sandbox (no network, empty environment, read-only "
        "target at /target, writes only to /scratch, 1 CPU, 512 MB, 64 processes, 10 MB files, 90 seconds). The "
        "parent records what each check printed as your artifact, agents/" + agent_id + "/artifacts/check-<n>.json, and "
        "the tool tells you the exact path; cite only paths it gave you. Nothing else is promoted."
        + _sandbox_facts(snap) if can_check else
        "Local checks: no sandbox is available for this run, so do not execute anything. A candidate that needs a "
        "local result is needs_validation with the blocker 'no OS-enforced sandbox was available'.")
    return f"""## Architecture
{architecture}

## Your assigned units (agent id: {agent_id})
{json.dumps(units, indent=1)}

## Scanner leads in your units
{_hunter_leads(ledger, ids, snap)}

## Selected attack-class blocks
{blocks}

## Core principles
{m.core_principles()}

## Hunting method
{m.hunting_method()}

## {execution}

## Validation rules
{m.validation_rules()}

{m.hunter_result_contract()}

## Schema branches for candidates (from report-schema.json)
confirmed: {json.dumps(m.branch("confirmed"))}
needs_validation: {json.dumps(m.branch("needs_validation"))}

Every check's agent_id is "{agent_id}". Paths are repository-relative. Submit the JSON object as a string."""


def _check_hunt(result: dict, ids: list[str], agent_id: str, snap: tools.Snapshot, run: Run) -> list[str]:
    problems = []
    units = result.get("units")
    if not isinstance(units, list):
        return ["units must be a list with one entry per assigned coverage_id"]
    got = [u.get("coverage_id") for u in units]
    if sorted(got) != sorted(ids):
        problems.append(f"units must cover exactly these coverage_ids once each: {ids}")
    fingerprints = {c.get("fingerprint") for c in result.get("candidates") or []}
    for u in units:
        where = f"unit {u.get('coverage_id')}"
        disposition = u.get("disposition")
        if disposition not in ("covered", "candidate", "blocked"):
            problems.append(f"{where}: disposition must be covered, candidate or blocked")
        checks = u.get("checks") or []
        if not checks:
            problems.append(f"{where}: needs at least one check")
        for c in checks:
            c["agent_id"] = agent_id
            paths = c.get("reviewed_paths") or []
            if not paths or any(p not in snap.files for p in paths):
                problems.append(f"{where}: every check needs reviewed_paths that are existing files")
            if not _visible(c.get("invariant")) or not _visible(c.get("result")):
                problems.append(f"{where}: every check needs invariant and result text")
            if c.get("method") == "source":
                c["artifact"] = None
            elif c.get("method") == "local":
                art = str(c.get("artifact") or "")
                if not art.startswith(f"agents/{agent_id}/artifacts/") or not (run.out / art).is_file():
                    problems.append(f"{where}: a local check must cite an artifact path run_check gave you")
            else:
                problems.append(f"{where}: check method must be source or local")
        linked = u.get("candidate_fingerprints") or []
        if disposition == "candidate" and (not linked or not set(linked) <= fingerprints):
            problems.append(f"{where}: a candidate unit links fingerprints present in candidates")
        if disposition != "candidate" and linked:
            problems.append(f"{where}: only a candidate unit links fingerprints")
        if disposition == "blocked" and not u.get("unresolved"):
            problems.append(f"{where}: a blocked unit needs unresolved facts")
        if disposition == "covered" and u.get("unresolved"):
            problems.append(f"{where}: a covered unit has no unresolved facts (use blocked)")
    for c in result.get("candidates") or []:
        problems += [f"candidate {c.get('fingerprint')}: {p}" for p in _check_record(
            {**{k: v for k, v in c.items() if k != "proposed_verdict"}, "verdict": c.get("proposed_verdict")}, snap)]
    return problems


def _check_record(record: dict, snap: tools.Snapshot) -> list[str]:
    verdict = record.get("verdict")
    if verdict not in ("confirmed", "needs_validation", "rejected"):
        return ["verdict must be confirmed, needs_validation or rejected"]
    problems = [f"{'/'.join(map(str, e.absolute_path)) or 'record'}: {e.message}"
                for e in jsonschema.Draft202012Validator(m.branch(verdict)).iter_errors(record)][:15]
    fp = str(record.get("fingerprint", ""))
    if not fp or fp[0] not in FINGERPRINT_OK - set("._:/@+-") or set(fp) - FINGERPRINT_OK:
        problems.append("fingerprint must match ^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$ with no line, agent or verdict")
    for item in (record.get("trace") or []) + (record.get("evidence") or []):
        path, line = item.get("file"), item.get("line")
        if path not in snap.files:
            problems.append(f"{path} is not a file in the repository")
        elif isinstance(line, int) and line > len(snap.text(path).splitlines()):
            problems.append(f"{path}:{line} is past the end of the file")
    return problems


def _apply_hunt(ledger: dict, result: dict, agent_id: str, candidates: dict, extra: dict) -> None:
    for u in result["units"]:
        unit = ledger[u["coverage_id"]]
        checks = u["checks"]
        unit.update(
            status=u["disposition"], agent_id=agent_id, local_checks=checks,
            reviewed_paths=sorted({p for c in checks for p in c["reviewed_paths"]}),
            result_fingerprints=sorted(set(u.get("candidate_fingerprints") or [])) if u["disposition"] == "candidate"
            else [], unresolved=list(u.get("unresolved") or []) if u["disposition"] != "covered" else [])
    for c in result.get("candidates") or []:
        fp = c["fingerprint"]
        units = [x["coverage_id"] for x in result["units"] if fp in (x.get("candidate_fingerprints") or [])]
        held = candidates.get(fp)
        if held is None or (c.get("proposed_verdict") == "confirmed" and held["proposed_verdict"] != "confirmed"):
            candidates[fp] = {**c, "_units": sorted(set(units) | set(held["_units"] if held else [])),
                              "_hunter": agent_id}
        else:
            held["_units"] = sorted(set(held["_units"]) | set(units))
    extra["hardening"] += result.get("hardening") or []
    extra["uncovered"] += result.get("uncovered") or []


# ------------------------------------------------------------------ 3. coverage critic

def _critic(run: Run, client: Client, snap: tools.Snapshot, architecture: str, ledger: dict, candidates: dict) -> None:
    spec = agents.Spec(
        agent_id="critic-1", model="strong", effort="medium", max_turns=30,
        instructions=AUTHORISED + "You are the coverage critic of a quick-profile audit. You propose coverage, not findings.",
        task=(f"## Architecture\n{architecture}\n\n## Coverage ledger\n{json.dumps(list(ledger.values()), indent=1)}\n\n"
              f"## Candidate fingerprints\n{sorted(candidates)}\n\n{m.critic_contract()}\n\nSubmit the JSON object."),
        submit_description="Submit the critic result.",
        check=lambda a: [] if isinstance(a.get("missing_units"), list) and isinstance(a.get("reassign_ids"), list)
        else ["missing_units and reassign_ids must be lists"])
    result = _guarded(run, client, snap, spec)
    if result is None:
        run.say("critic", "The coverage critic failed; coverage is reported without its review.")
        return
    added = 0
    for mu in result.get("missing_units") or []:
        ref = str(mu.get("attack_class") or "")
        ref = ref if m.block(ref) else f"{m.ATTACK_CLASSES}#Wildcard"
        paths = [p for p in mu.get("starting_paths") or [] if p in snap.files]
        if not paths or not _visible(mu.get("surface")) or not _visible(mu.get("boundary")):
            continue
        refs = {"surface": f"critic:{mu['surface']}", "boundary": f"critic:{mu['boundary']}",
                "subsystem": QUICK_SUBSYSTEM, "attack_class": ref}
        cid = coverage_id(refs)
        if cid in ledger:
            continue
        ordinary = ref if ref.startswith(m.ATTACK_CLASSES) else None
        ledger[cid] = {
            "coverage_id": cid, "canonical_refs": refs, "surface": mu["surface"], "boundary": mu["boundary"],
            "subsystem": "All in-scope subsystems (quick profile)", "attack_class": ref.split("#", 1)[1],
            "starting_paths": sorted(set(paths)), "ordinary_attack_class_block": ordinary,
            "selected_companion_blocks": [] if ordinary else [ref] + [f"{ref.split('#')[0]}#{f}" for f in
                                                                       m.COMPANION_FRAME],
            "excluded_blocks": [], "prior_status": "none", "attempts": [], "wave": 1, "status": "deferred",
            "agent_id": None, "reviewed_paths": [], "local_checks": [], "result_fingerprints": [],
            "unresolved": ["quick_profile_final_critic"], "priority": 3, "critic_reason": mu.get("reason", "")}
        added += 1
    reopened = 0
    for cid in result.get("reassign_ids") or []:
        unit = ledger.get(cid)
        if unit is None or unit["status"] not in ("covered", "candidate", "blocked"):
            continue
        unit["attempts"].append({k: unit[k] for k in ("wave", "status", "agent_id", "reviewed_paths", "local_checks",
                                                       "result_fingerprints", "unresolved")}
                                | {"reassignment_reason": "the quick-profile critic asked for another pass"})
        unit.update(wave=unit["wave"] + 1, status="deferred", agent_id=None, reviewed_paths=[], local_checks=[],
                    result_fingerprints=[], unresolved=["quick_profile_final_critic"])
        reopened += 1
    run.say("critic", f"Coverage critic: {added} gaps recorded and {reopened} units reopened, all deferred.")


# ------------------------------------------------------------------ 4. disprove

def _sink(cand: dict) -> tuple[str, str]:
    last = (cand.get("trace") or [{}])[-1]
    return str(last.get("file", "")), str(last.get("scope", "")).strip().lower()


def merge_duplicates(run: Run, ledger: dict, candidates: dict) -> dict:
    """One root cause is one candidate (the method's consolidation rule). Hunters working different units can name
    the same root cause differently; leads that end at the same sink (file and scope) are merged before anyone
    verifies them. The kept lead is a proposed confirmation over a needs_validation one, then the one with the fuller
    trace; every unit that linked a dropped fingerprint links the kept one instead."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fp, cand in candidates.items():
        groups[_sink(cand)].append(fp)
    kept: dict = {}
    replaced: dict[str, str] = {}
    for fps in groups.values():
        fps.sort(key=lambda f: (candidates[f].get("proposed_verdict") != "confirmed",
                                -len(candidates[f].get("trace") or []), f))
        head = fps[0]
        kept[head] = {**candidates[head], "_units": sorted({u for f in fps for u in candidates[f]["_units"]})}
        for other in fps[1:]:
            replaced[other] = head
    for unit in ledger.values():
        if unit["result_fingerprints"]:
            unit["result_fingerprints"] = sorted({replaced.get(f, f) for f in unit["result_fingerprints"]})
    if replaced:
        (run.out / "merged-leads.json").write_text(json.dumps(replaced, indent=1))
        run.say("verify", f"{len(replaced)} leads were the same root cause as another and were merged; "
                          f"{len(kept)} remain.", merged=replaced)
    return kept


def _steps(record: dict) -> list[tuple[str, str]]:
    return [(str(t.get("file", "")), str(t.get("scope", "")).strip().lower()) for t in record.get("trace") or []]


def _same_root(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> bool:
    """Two verified traces describe one root cause when they share at least two identical steps and one ends in a file
    the other passes through. One shared step is not enough: the authorizer every request crosses is on most traces."""
    if not a or not b or len(set(a) & set(b)) < 2:
        return False
    return b[-1][0] in {f for f, _ in a} or a[-1][0] in {f for f, _ in b}


def merge_confirmed(run: Run, ledger: dict, records: list[dict]) -> list[dict]:
    """The second consolidation pass. Verifiers rewrite a lead's trace as they check it, so two leads that looked
    different to merge_duplicates can come back as the same weakness reached two ways. Confirmed records that share a
    root cause (_same_root) become one: the most severe, then the fuller trace, is kept, and every unit that linked a
    dropped fingerprint links the kept one."""
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    confirmed = sorted((r for r in records if r.get("verdict") == "confirmed"),
                       key=lambda r: (rank.get(str((r.get("severity") or {}).get("overall_severity")), 4),
                                      -len(r.get("trace") or []), str(r.get("fingerprint"))))
    kept: list[dict] = []
    replaced: dict[str, str] = {}
    for record in confirmed:
        head = next((k for k in kept if _same_root(_steps(record), _steps(k))), None)
        if head is None:
            kept.append(record)
        else:
            replaced[str(record["fingerprint"])] = str(head["fingerprint"])
    if not replaced:
        return records
    for unit in ledger.values():
        if unit["result_fingerprints"]:
            unit["result_fingerprints"] = sorted({replaced.get(f, f) for f in unit["result_fingerprints"]})
    (run.out / "merged-findings.json").write_text(json.dumps(replaced, indent=1))
    run.say("verify", f"{len(replaced)} confirmed findings were the same weakness reached another way and were merged.",
            merged=replaced)
    return [r for r in records if str(r.get("fingerprint")) not in replaced]


def _verify(run: Run, client: Client, snap: tools.Snapshot, architecture: str, ledger: dict, candidates: dict,
            can_check: bool) -> tuple[list[dict], list[str]]:
    run.say("verify", f"{len(candidates)} leads go to fresh verifiers who try to disprove them.")
    records, unvalidated = [], []

    def verify(index: int, fp: str) -> None:
        cand = candidates[fp]
        for attempt in (1, 2):
            agent_id = f"verifier-{index:02d}" + ("" if attempt == 1 else "-r")
            spec = agents.Spec(
                agent_id=agent_id, model="strong", effort="high", max_turns=40, can_run_checks=can_check,
                instructions=AUTHORISED + "You are an independent verifier in a defensive security audit. Finish by calling submit.",
                task=_verifier_task(architecture, ledger, cand, can_check, snap),
                submit_description='Submit {"decision": ..., "record": {...}}.',
                check=lambda a, agent_id=agent_id: _check_verdict(a, snap, run, agent_id))
            result = _guarded(run, client, snap, spec, retry=False)
            if result is not None:
                record = result["record"]
                records.append(record | {"_units": cand["_units"], "_verifier": agent_id, "_hunter": cand["_hunter"]})
                run.say("verify", f"{record['verdict']}: {record['title']}", verdict=record["verdict"],
                        fingerprint=record["fingerprint"], severity=(record.get("severity") or {}).get(
                            "overall_severity"), units=cand["_units"])
                return
        unvalidated.append(fp)
        run.say("verify", f"Could not validate {fp}; it stays an unvalidated lead and the run is incomplete.")

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(lambda iv: verify(*iv), enumerate(sorted(candidates), 1)))
    for fp in unvalidated:
        for cid in candidates[fp]["_units"]:
            unit = ledger[cid]
            if "validation_budget_exhausted" not in unit["unresolved"]:
                unit["unresolved"] = unit["unresolved"] + ["validation_budget_exhausted"]
    return sorted(records, key=lambda r: r["fingerprint"]), sorted(unvalidated)


def _verifier_task(architecture: str, ledger: dict, cand: dict, can_check: bool, snap: tools.Snapshot) -> str:
    linked = [{"coverage_id": c, "checks": ledger[c]["local_checks"]} for c in cand["_units"]]
    rules = sorted({f"{b.split('#')[0]}#Validation rules" for c in cand["_units"]
                    for b in ledger[c]["selected_companion_blocks"]})
    rule_text = "\n\n".join(m.block(r) or "" for r in rules)
    clean = {k: v for k, v in cand.items() if not k.startswith("_")}
    sandbox = ("The approved sandbox is the run_check tool: no network, empty environment, read-only target at "
               "/target, writes only to /scratch, low limits. The parent captures what it printed as your artifact. "
               "A confirmed decision requires that you ran at least one run_check yourself that shows the minimum "
               "observed result; describe that check in execution." + _sandbox_facts(snap) + CONFIRM_RULE if can_check else
               "No sandbox is available in this run: do not execute. Without a bounded local result, confirmed is "
               "not available; keep needs_validation with the blocker, or reject.")
    return f"""{m.verifier_prompt()}

## Execution in this run
{sandbox}

## Architecture facts
{architecture}

## The candidate
{json.dumps(clean, indent=1)}

## Its coverage-unit checks
{json.dumps(linked, indent=1)}

## Companion validation rules
{rule_text or "(none selected)"}

## Schema branches (from report-schema.json)
confirmed: {json.dumps(m.branch("confirmed"))}
needs_validation: {json.dumps(m.branch("needs_validation"))}
rejected: {json.dumps(m.branch("rejected"))}

Keep the fingerprint {cand["fingerprint"]!r} unless you find a genuinely different root cause."""


def _check_verdict(answer: dict, snap: tools.Snapshot, run: Run, agent_id: str) -> list[str]:
    decision, record = answer.get("decision"), answer.get("record")
    if decision not in ("confirmed", "needs_validation", "rejected") or not isinstance(record, dict):
        return ['submit {"decision": "confirmed|needs_validation|rejected", "record": {...}}']
    record["verdict"] = decision
    problems = _check_record(record, snap)
    if decision == "confirmed" and not any((run.out / "agents" / agent_id / "artifacts").glob("check-*.json")):
        problems.append("confirmed needs a bounded local check you ran with run_check; run one, or choose "
                        "needs_validation with the exact blocker")
    return problems


# ------------------------------------------------------------------ 4b. a proof check per confirmed finding

PROOF_RULES = """Write a proof check for this confirmed finding: a small test that anyone can rerun in the same sealed
container to see the weakness, and later to see it gone.

- It exits 0 when the boundary holds (the weakness is fixed) and exits non-zero when the weakness is present.
- It exercises the repository's own code or configuration at /target, the way the finding's trace does (import the
  real module, call the real handler, parse the real Terraform), with dummy data only. Never test a copy of the code.
- It does not depend on the exact line, wording or structure of the vulnerable code, so any correct fix passes it:
  test the behaviour (the attack input is refused, the record stays private, the policy is not public), not the text.
- It prints one plain line saying what it observed.
- Before you submit, run it with run_check and see it exit non-zero on the current code.

Submit {"files": [{"name": "...", "content": "..."}], "command": "...", "workdir": "", "explains": "<one sentence:
what the check does and why it fails now>"}."""


def _proofs(run: Run, client: Client, snap: tools.Snapshot, records: list[dict]) -> None:
    confirmed = [r for r in records if r["verdict"] == "confirmed"]
    path = run.out / "proofs.json"
    proofs = json.loads(path.read_text()) if path.exists() else {}
    todo = [r for r in confirmed if r["fingerprint"] not in proofs]
    run.say("prove", f"Writing a rerunnable proof check for {len(todo)} confirmed findings.")

    def prove(index: int, record: dict) -> None:
        agent_id = f"prover-{index:02d}"
        holder: dict = {}
        spec = agents.Spec(
            agent_id=agent_id, model="strong", effort="medium", max_turns=30, can_run_checks=True,
            instructions=AUTHORISED + "You write regression checks that prove a security weakness and its fix. Finish with submit.",
            task=f"{PROOF_RULES}\n\nSandbox:{_sandbox_facts(snap)}\n\nFinding:\n"
                 f"{json.dumps(_public(record), indent=1)}",
            submit_description="Submit the proof check.",
            check=lambda a: _check_proof(a, snap, run, agent_id, holder))
        result = _guarded(run, client, snap, spec)
        if result is None:
            run.say("prove", f"No proof check for {record['title']}; its fix is proved by review only.")
            return
        proofs[record["fingerprint"]] = {**result, "before": holder.get("before") or result.get("_before")}
        run.say("prove", f"Proof check ready: {record['title']}", fingerprint=record["fingerprint"])

    with ThreadPoolExecutor(PARALLEL) as pool:
        list(pool.map(lambda iv: prove(*iv), enumerate(todo, 1)))
    path.write_text(json.dumps(proofs, indent=1))


def _check_proof(answer: dict, snap: tools.Snapshot, run: Run, agent_id: str, holder: dict) -> list[str]:
    files = {str(f.get("name")): str(f.get("content")) for f in answer.get("files") or [] if isinstance(f, dict)}
    if not files or not str(answer.get("command", "")).strip():
        return ["submit files (name and content) and a command"]
    try:
        shown, artifact, code = tools.run_check(snap, run.out / "agents" / agent_id, agent_id, files,
                                                answer["command"], answer.get("workdir") or "")
    except tools.ToolError as exc:
        return [str(exc)]
    if code == 0:
        return ["The check exits 0 on the current code, where the weakness is present. It must fail now:\n" + shown]
    if code == -1:
        return ["The check timed out; make it smaller:\n" + shown]
    holder["before"] = {"exit_code": code, "artifact": artifact}
    answer["_before"] = holder["before"]
    return []


def proof_rerun(snapshot: Path, deps: Path | None, proof: dict, files: dict[str, str], work: Path) -> dict:
    """Run a finding's proof check on the snapshot with `files` laid over it: fixed when it exits 0.

    `work` is a folder for this rerun's scratch and artifact; the snapshot itself is never changed.
    """
    with tempfile.TemporaryDirectory(prefix="wh-proof-") as tmp:
        root = Path(tmp) / "target"
        shutil.copytree(snapshot, root, symlinks=False)
        for name, text in files.items():
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ".." in parts:
                raise ValueError(f"{name} is not a path inside the repository")
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        snap = tools.Snapshot.at(root)
        snap.deps = deps
        checks = {f["name"]: f["content"] for f in proof["files"]}
        shown, _, code = tools.run_check(snap, work, "proof", checks, proof["command"], proof.get("workdir") or "")
    return {"fixed": code == 0, "exit_code": code, "output": shown}


# ------------------------------------------------------------------ 5. findings.json

def _validated_findings(run: Run, client: Client, snap: tools.Snapshot, records: list[dict]) -> list[dict]:
    path = run.out / "findings.json"
    for attempt in range(3):
        path.write_text(json.dumps([_public(r) for r in records], indent=1))
        errors = _node("validate-findings.cjs", path)
        if not errors:
            run.say("findings", f"findings.json passes the method's validator ({len(records)} records).")
            return records
        run.say("findings", f"findings.json has {len(errors)} validator errors; repairing (attempt {attempt + 1}).",
                errors=errors[:20])
        broken = sorted({int(e.split("[", 1)[1].split("]", 1)[0]) for e in errors if e.startswith("ERROR: $[")})
        if not broken:
            break
        for i in broken:
            records[i] = _repair(run, client, snap, records[i], [e for e in errors if e.startswith(f"ERROR: $[{i}]")],
                                 attempt) or records[i]
    errors = _node("validate-findings.cjs", path)
    bad = {int(e.split("[", 1)[1].split("]", 1)[0]) for e in errors if e.startswith("ERROR: $[")}
    kept = [r for i, r in enumerate(records) if i not in bad]
    run.say("findings", f"{len(bad)} records could not be made valid and are left out of findings.json.")
    path.write_text(json.dumps([_public(r) for r in kept], indent=1))
    return kept


def _repair(run: Run, client: Client, snap: tools.Snapshot, record: dict, errors: list[str], attempt: int) -> dict | None:
    verdict = record["verdict"]
    spec = agents.Spec(
        agent_id=f"repair-{record['fingerprint'][:40].lower().replace('/', '-').replace(':', '-').replace('.', '-')}"
                 f"-{attempt}".replace("--", "-").strip("-")[:60], model="strong", effort="low", max_turns=8,
        instructions=AUTHORISED + "Fix the record's format so it passes the validator. Do not change its meaning or verdict.",
        task=(f"Validator errors:\n{chr(10).join(errors)}\n\nSchema branch:\n{json.dumps(m.branch(verdict))}\n\n"
              f"Record:\n{json.dumps(_public(record), indent=1)}\n\nSubmit the corrected record."),
        submit_description="Submit the corrected record.",
        check=lambda a: _check_record(a, snap) or ([] if a.get("verdict") == verdict else ["keep the verdict"]))
    fixed = _guarded(run, client, snap, spec, retry=False)
    return fixed | {k: v for k, v in record.items() if k.startswith("_")} if fixed else None


def _lineage(run: Run, records: list[dict]) -> None:
    """Which agents found and verified each record, so a fix can be handed their notes (`lineage.json`)."""
    (run.out / "lineage.json").write_text(json.dumps({
        r["fingerprint"]: {"hunter": r.get("_hunter"), "verifier": r.get("_verifier"), "units": r.get("_units", [])}
        for r in records}, indent=1))


def _public(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _node(script: str, path: Path) -> list[str]:
    proc = subprocess.run(["node", str(m.METHOD_DIR / script), str(path)], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return []
    return [line for line in (proc.stderr + proc.stdout).splitlines() if line.startswith("ERROR")] or [proc.stderr]


# ------------------------------------------------------------------ ledger, metadata, reports

def _write_ledger(run: Run, ledger: dict) -> None:
    units = [{k: v for k, v in u.items() if k not in ("priority", "critic_reason")} for u in ledger.values()]
    units.sort(key=lambda u: u["coverage_id"])
    path = run.out / "coverage-ledger.json"
    path.write_text(json.dumps(units, indent=1))
    errors = _node("validate-coverage-ledger.cjs", path)
    if errors:
        run.say("ledger", f"The coverage ledger has {len(errors)} validator errors.", errors=errors[:20])


def _metadata(run: Run, status: str, incomplete_reason: str | None = None) -> None:
    meta = {"run_id": run.out.name, "repo": run.repo, "target": str(run.target), "source_ref": run.source_ref,
            "profile": run.profile, "scope_paths": ["."], "budget": None, "budget_usd": run.budget_usd,
            "execution_policy": "sandboxed-source-and-local-only",
            "selected_companion_files": list(m.COMPANIONS), "prior_runs": [],
            "shared_file_owners": {"all": "conductor"}, "run_status": status,
            "models": {"recon": "gpt-6-luna", "plan, hunt, critic, verify": "gpt-6.1-terra"}}
    if incomplete_reason:
        meta["incomplete_reason"] = incomplete_reason
    (run.out / "run-metadata.json").write_text(json.dumps(meta, indent=1))


def _unit_view(u: dict) -> dict:
    return {"id": u["coverage_id"], "surface": u["surface"], "attack_class": u["attack_class"],
            "paths": u["starting_paths"], "status": u["status"]}


def _summary(run: Run, ledger: dict, records: list[dict], client: Client, status: str) -> dict:
    counts = {v: sum(r["verdict"] == v for r in records) for v in ("confirmed", "needs_validation", "rejected")}
    statuses = defaultdict(int)
    for u in ledger.values():
        statuses[u["status"]] += 1
    reached = sum(statuses[s] for s in ("covered", "candidate", "blocked"))
    totals = client.totals()
    return {"status": status, "counts": counts, "units": dict(statuses), "coverage_percent":
            round(100 * reached / max(1, len(ledger))), "cost": totals["cost"], "usage": totals["models"],
            "minutes": round((time.time() - run.started) / 60, 1), "source_ref": run.source_ref, "repo": run.repo}


def _reports(run: Run, ledger: dict, records: list[dict], extra: dict, unvalidated: list[str],
             client: Client) -> None:
    summary = _summary(run, ledger, records, client, "incomplete" if unvalidated else "complete")
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
    confirmed = sorted([r for r in records if r["verdict"] == "confirmed"],
                       key=lambda r: sev[r["severity"]["overall_severity"]])
    lines = [f"# Security audit report: {run.repo}", "",
             (f"Source: {run.source_ref}. Profile: {run.profile} (one hunting wave and one coverage review; partial "
             "coverage by design)."), ""]
    if unvalidated:
        lines += ["**Candidate validation is incomplete.** These leads were not validated and are not findings: "
                  + ", ".join(unvalidated), ""]
    lines += ["## Summary", "",
              f"- Confirmed: {summary['counts']['confirmed']}",
              f"- Needs validation: {summary['counts']['needs_validation']}",
              f"- Rejected after a fresh verifier tried to disprove them: {summary['counts']['rejected']}",
              f"- Coverage units reached: {summary['coverage_percent']}% ({dict(summary['units'])})",
              f"- Cost: ${summary['cost']:.2f}; time: {summary['minutes']} minutes", "",
              "## Confirmed findings", ""]
    for r in confirmed:
        lines.append(f"- **{r['severity']['overall_severity'].upper()}**: {r['title']} "
                     f"({r['trace'][-1]['file']}:{r['trace'][-1]['line']}). {r['remediation']['strategy']}")
    lines += ["", "## Not covered in this run", ""]
    for u in ledger.values():
        if u["status"] in ("deferred", "planned", "out_of_scope"):
            lines.append(f"- {u['surface']} / {u['boundary']} / {u['attack_class']}: {', '.join(u['unresolved'])}")
    if extra["hardening"]:
        lines += ["", "## Hardening notes (not findings)", ""] + [f"- {h}" for h in extra["hardening"][:40]]
    (run.out / "REPORT.md").write_text("\n".join(lines) + "\n")

    detail = [f"# Findings in detail: {run.repo}", ""]
    for r in confirmed:
        detail += [f"## {r['title']}", "", (f"Fingerprint: `{r['fingerprint']}`. Severity: "
                   f"{r['severity']['overall_severity']} (likelihood {r['severity']['likelihood']['score']}, impact "
                   f"{r['severity']['impact']['score']}). Confidence: {r['confidence']['score']}."), "",
                   r["description"], "", f"Root cause: {r['root_cause']}", "",
                   f"Intended behaviour: {r['intended_behavior']}", "", "Trace:"]
        detail += [f"- {t['kind']}: {t['file']}:{t['line']} ({t['scope']}): {t['description']}" for t in r["trace"]]
        detail += ["", f"Observed: {r['execution']['observed_result']}", "", f"Fix: {r['remediation']['strategy']}", ""]
    (run.out / "FINDINGS-DETAIL.md").write_text("\n".join(detail) + "\n")

    nv = [f"# Needs validation: {run.repo}", ""]
    for r in [r for r in records if r["verdict"] == "needs_validation"]:
        nv += [f"## {r['title']}", "", r["description"], "", "Blockers:"] + [f"- {b}" for b in r["blockers"]]
        nv += ["", f"Plan: {json.dumps(r['validation_plan'])}", ""]
    (run.out / "NEEDS-VALIDATION.md").write_text("\n".join(nv) + "\n")


def _guarded(run: Run, client: Client, snap: tools.Snapshot, spec: agents.Spec, retry: bool = True) -> dict | None:
    """Run an agent; on failure try once more (fresh conversation) when `retry`; None when it cannot finish."""
    for attempt in (1, 2) if retry else (1,):
        if client.totals()["cost"] > run.budget_usd:
            run.say("budget", f"The ${run.budget_usd:.2f} budget is spent; {spec.agent_id} does not start.")
            return None
        try:
            return agents.run(client, snap, run.out, spec)
        except agents.AgentFailed as exc:
            run.say("agent", f"{spec.agent_id} failed (attempt {attempt}): {str(exc)[:300]}")
            if attempt == 1 and retry:
                # The retry is a fresh conversation: keep the failed one for review, never resume it (a session that
                # ran out of turns would fail again at once).
                stamp = int(time.time())
                for name in ("transcript.jsonl", "session.json"):
                    stale = run.out / "agents" / spec.agent_id / name
                    if stale.exists():
                        stale.rename(stale.with_name(f"{stale.stem}.failed-{stamp}{stale.suffix}"))
    return None
