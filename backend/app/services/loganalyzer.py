"""Log analyzer: turn a flood of lines into a digest small enough for a model.

Three stages, so the AI never sees raw logs:
1. Mine templates. Every line is reduced to its shape by masking the variable
   parts (numbers, IDs, addresses), so millions of lines become a few hundred
   templates with counts.
2. Score. Each template's count in the window is compared with its own
   baseline. New shapes, spikes, errors and security-relevant shapes are notable.
3. Pack. Notable templates are ranked and packed per collector into that
   collector's token budget, then across collectors into the digest budget.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Collector, LogDigest, LogEvent, LogTemplate, TemplateCount
from .llm import Section, complete, estimate_tokens, pack_context

MAX_TOKENS_PER_LINE = 48
KEEP_RAW_LINES = 20_000  # per org; older raw lines are deleted, their counts live on in templates

ERROR_WORDS = ("error", "fail", "failed", "failure", "fatal", "panic", "exception", "critical", "refused", "timeout")
WARN_WORDS = ("warn", "warning", "retry", "deprecated", "slow")
# Phrases, not single words: "token" or "login" alone match every OS policy message and drown real signals.
SECURITY_WORDS = (
    "authentication failure", "authentication failed", "failed password", "invalid user", "permission denied",
    "access denied", "unauthorized", "forbidden", "login failed", "failed login", "logon failure", "sudo:",
    "brute force", "blocked by", "firewall", "malware", "quarantine", "privilege escalation", "status=401",
    "status=403", "possible break-in", "too many authentication",
)


def _is_variable(tok: str) -> bool:
    """A token whose exact value does not matter to the line's meaning."""
    if any(ch.isdigit() for ch in tok):
        return True
    core = tok.strip("[]()<>{},;:'\"")
    if len(core) >= 12 and all(ch.isalnum() or ch in "-_./" for ch in core) and not core.isalpha():
        return True
    return bool(core.startswith(("/", "~/")) and core.count("/") >= 2)


def mask_line(line: str) -> str:
    out = []
    for tok in line.split()[:MAX_TOKENS_PER_LINE]:
        if "=" in tok and not tok.startswith("="):
            key, _, val = tok.partition("=")
            out.append(f"{key}=<*>" if val else tok)
        elif _is_variable(tok):
            out.append("<*>")
        else:
            out.append(tok)
    # Collapse runs of placeholders so "a 1 2 3 b" and "a 4 5 b" share a shape.
    collapsed = []
    for tok in out:
        if tok == "<*>" and collapsed and collapsed[-1] == "<*>":
            continue
        collapsed.append(tok)
    return " ".join(collapsed)


def classify(line: str) -> tuple[str, bool]:
    low = line.lower()
    level = "error" if any(w in low for w in ERROR_WORDS) else "warn" if any(w in low for w in WARN_WORDS) else "info"
    security = any(w in low for w in SECURITY_WORDS)
    return level, security


def reclassify(db: Session) -> int:
    """Re-apply the current level and security rules to stored shapes (run at startup)."""
    changed = 0
    for t in db.scalars(select(LogTemplate)):
        level, security = classify(t.sample)
        if (level, security) != (t.level, t.security):
            t.level, t.security = level, security
            changed += 1
    db.commit()
    return changed


def ingest_lines(db: Session, collector: Collector, lines: list[dict]) -> int:
    """Store a batch of {ts?, source, message} lines; returns how many were accepted."""
    now = utcnow()
    minute = now.replace(second=0, microsecond=0)
    cache: dict[str, LogTemplate] = {}
    per_template: Counter = Counter()
    accepted = 0
    for item in lines:
        msg = str(item.get("message", "")).strip()
        if not msg:
            continue
        msg = msg[:2000]
        pattern = mask_line(msg)
        sig = hashlib.sha1(pattern.encode()).hexdigest()
        tpl = cache.get(sig) or db.scalar(
            select(LogTemplate).where(LogTemplate.org_id == collector.org_id, LogTemplate.signature == sig)
        )
        if tpl is None:
            level, security = classify(msg)
            tpl = LogTemplate(org_id=collector.org_id, signature=sig, pattern=pattern, level=level, security=security,
                              count=0, sample=msg[:400], source=str(item.get("source", ""))[:200],
                              first_seen=now, last_seen=now)
            db.add(tpl)
            db.flush()
        cache[sig] = tpl
        tpl.count += 1
        tpl.last_seen = now
        per_template[tpl.id] += 1
        db.add(LogEvent(org_id=collector.org_id, collector_id=collector.id, template_id=tpl.id, ts=now,
                        source=str(item.get("source", ""))[:200], message=msg))
        accepted += 1
    for tid, n in per_template.items():
        row = db.scalar(select(TemplateCount).where(TemplateCount.template_id == tid, TemplateCount.minute == minute))
        if row is None:
            db.add(TemplateCount(template_id=tid, minute=minute, count=n))
        else:
            row.count += n
    collector.events_total += accepted
    _trim_raw(db, collector.org_id)
    return accepted


def _trim_raw(db: Session, org_id: int) -> None:
    total = db.scalar(select(func.count(LogEvent.id)).where(LogEvent.org_id == org_id)) or 0
    if total <= KEEP_RAW_LINES:
        return
    cutoff = db.scalar(
        select(LogEvent.id).where(LogEvent.org_id == org_id).order_by(LogEvent.id.desc()).offset(KEEP_RAW_LINES)
    )
    if cutoff:
        db.query(LogEvent).filter(LogEvent.org_id == org_id, LogEvent.id <= cutoff).delete()


def _window_counts(db: Session, org_id: int, start: datetime, end: datetime) -> dict[int, int]:
    rows = db.execute(
        select(TemplateCount.template_id, func.sum(TemplateCount.count))
        .join(LogTemplate, LogTemplate.id == TemplateCount.template_id)
        .where(LogTemplate.org_id == org_id, TemplateCount.minute >= start, TemplateCount.minute < end)
        .group_by(TemplateCount.template_id)
    ).all()
    return {tid: int(n) for tid, n in rows}


def score_window(db: Session, org_id: int, minutes: int = 60) -> list[dict]:
    """Rank the templates seen in the window by how much a person should care."""
    end = utcnow().replace(second=0, microsecond=0) + timedelta(minutes=1)
    start = end - timedelta(minutes=minutes)
    base_start = start - timedelta(hours=24)
    current = _window_counts(db, org_id, start, end)
    baseline = _window_counts(db, org_id, base_start, start)
    if not current:
        return []
    tpls = {t.id: t for t in db.scalars(select(LogTemplate).where(LogTemplate.id.in_(current.keys())))}
    # Which collector produced each template recently, for per-collector packing.
    owner = dict(db.execute(
        select(LogEvent.template_id, func.max(LogEvent.collector_id))
        .where(LogEvent.template_id.in_(current.keys())).group_by(LogEvent.template_id)
    ).all())
    base_windows = 24 * 60 / minutes
    # Until some history exists, every shape would look "new". Learn first, then judge.
    has_baseline = db.scalar(select(LogTemplate.id).where(LogTemplate.org_id == org_id,
                                                          LogTemplate.first_seen < start).limit(1)) is not None
    items = []
    for tid, n in current.items():
        t = tpls[tid]
        expected = baseline.get(tid, 0) / base_windows
        is_new = has_baseline and t.first_seen >= start
        spike = (n / expected) if expected > 0 else None
        reasons, score = [], 0.0
        if is_new:
            reasons.append("new")
            score += 3
        if spike is not None and spike >= 5 and n >= 5:
            reasons.append(f"spike x{spike:.0f}")
            score += min(spike / 5, 4)
        if t.level == "error":
            reasons.append("error")
            score += 2
        elif t.level == "warn":
            score += 0.5
        if t.security:
            reasons.append("security")
            score += 2.5
        score += min(n, 1000) ** 0.25 / 4
        items.append({
            "template_id": tid, "pattern": t.pattern, "sample": t.sample, "count": n, "level": t.level,
            "security": t.security, "new": is_new, "spike": round(spike, 1) if spike else None,
            "reasons": reasons, "score": round(score, 2), "source": t.source, "collector_id": owner.get(tid),
            "notable": bool(reasons),
        })
    items.sort(key=lambda i: i["score"], reverse=True)
    return items


def build_digest(db: Session, org_id: int, minutes: int = 60, budget: int = 1500, summarize: bool = True) -> LogDigest:
    """Score the window, pack notable templates per collector budget, then into the digest budget."""
    end = utcnow()
    start = end - timedelta(minutes=minutes)
    items = score_window(db, org_id, minutes)
    lines = sum(i["count"] for i in items)
    raw_chars = db.scalar(
        select(func.sum(func.length(LogEvent.message))).where(LogEvent.org_id == org_id, LogEvent.ts >= start)
    ) or 0
    # Raw lines that were already trimmed still count toward what the model was spared.
    avg_len = (raw_chars / max(1, db.scalar(select(func.count(LogEvent.id)).where(
        LogEvent.org_id == org_id, LogEvent.ts >= start)) or 1)) if raw_chars else 90
    raw_tokens = int(lines * avg_len / 4)

    collectors = {c.id: c for c in db.scalars(select(Collector).where(Collector.org_id == org_id))}
    by_collector: dict[int | None, list[dict]] = defaultdict(list)
    for it in items:
        if it["notable"]:
            by_collector[it["collector_id"]].append(it)

    sections = []
    for cid, its in by_collector.items():
        col = collectors.get(cid)
        name = col.name if col else "Unknown collector"
        per_budget = col.context_budget if col else 400
        rows, used = [], 0
        for it in its:
            row = (f"- x{it['count']} [{', '.join(it['reasons'])}] {it['pattern'][:220]}"
                   f"\n  e.g. {it['sample'][:200]}")
            cost = estimate_tokens(row)
            if used + cost > per_budget:
                rows.append(f"- (+{len(its) - len(rows)} more notable shapes over this collector's budget)")
                break
            rows.append(row)
            used += cost
        top_sev = max((it["score"] for it in its), default=0)
        sections.append(Section(f"{name} ({len(its)} notable)", "\n".join(rows), priority=1 if top_sev >= 5 else 3))

    quiet = len([i for i in items if not i["notable"]])
    sections.append(Section("Routine", f"{quiet} routine shapes, {sum(i['count'] for i in items if not i['notable'])} "
                                        "lines, nothing unusual.", priority=9))
    header = (f"Window: last {minutes} minutes. {lines} lines collapsed into {len(items)} shapes; "
              f"{sum(1 for i in items if i['notable'])} notable.")
    packed = pack_context([Section("Overview", header, 0), *sections], budget)

    digest = LogDigest(org_id=org_id, window_start=start, window_end=end, lines=lines, raw_tokens=raw_tokens,
                       templates=len(items), notable=sum(1 for i in items if i["notable"]),
                       digest_tokens=packed.tokens, budget_tokens=budget, text=packed.text,
                       items=[i for i in items if i["notable"]][:60])
    if summarize and digest.notable:
        fallback = _rules_summary(items)
        res = complete(
            db, org_id, "summarizer",
            "You are a security log analyst. You only see a digest of log templates, never raw logs. "
            "In at most 5 short bullet points, say what matters for security and operations, citing the "
            "template counts. Then one line: 'Action:' with the single most useful next step. Plain English.",
            packed.text, fallback, max_tokens=700,
        )
        digest.ai_summary, digest.model_used = res.text, res.model
    elif not digest.notable:
        digest.ai_summary, digest.model_used = "Nothing notable in this window.", "Rules engine"
    db.add(digest)
    from ..whitehats import dispatch
    from .tenancy import mark_milestone

    dispatch(db, org_id, "on_log_window", items=items)
    mark_milestone(db, org_id, "first_digest")
    db.commit()
    return digest


def _rules_summary(items: list[dict]) -> str:
    notable = [i for i in items if i["notable"]][:5]
    out = []
    for i in notable:
        out.append(f"- {', '.join(i['reasons']).capitalize()}: \"{i['pattern'][:120]}\" seen {i['count']} times.")
    sec = [i for i in notable if i["security"]]
    action = (f"review the {len(sec)} security-related shapes first, starting with the top one" if sec
              else "check the newest error shape against recent deployments")
    return "\n".join(out + [f"Action: {action}."])
