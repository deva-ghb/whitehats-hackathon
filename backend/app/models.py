"""ORM models. An account (the organizations table) owns people and shared setup.
A project owns the estate that account models and what WhiteHats finds there.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class Organization(Base):
    """An account. Screens say Account. The table name stays organizations.

    The platform operator's own account has is_platform=True.
    """

    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str | None] = mapped_column(String(60), unique=True)
    plan: Mapped[str] = mapped_column(String(20), default="growth")
    is_platform: Mapped[bool] = mapped_column(Boolean, default=False)
    # draft -> onboarding -> live -> suspended -> offboarded
    status: Mapped[str] = mapped_column(String(20), default="live")
    region: Mapped[str] = mapped_column(String(30), default="in-south")
    domain: Mapped[str] = mapped_column(String(160), default="")
    domain_token: Mapped[str] = mapped_column(String(80), default="")
    domain_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=True)
    sso_required: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_log_days: Mapped[int] = mapped_column(Integer, default=30)
    retention_run_days: Mapped[int] = mapped_column(Integer, default=365)
    retention_audit_days: Mapped[int] = mapped_column(Integer, default=730)
    ai_policy: Mapped[dict] = mapped_column(JSON, default=dict)  # {providers: [...], zero_retention: bool}
    contract: Mapped[dict] = mapped_column(JSON, default=dict)
    contracted_agents: Mapped[list] = mapped_column(JSON, default=list)
    alerts: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind: slack|teams|email, target: ...}
    repos: Mapped[list] = mapped_column(JSON, default=list)  # [{name, url, test}]
    support_access_until: Mapped[datetime | None] = mapped_column(DateTime)
    support_access_reason: Mapped[str] = mapped_column(Text, default="")
    # The one project a support session may see, or none for the whole account. A plain id, not a foreign
    # key: projects already point at organizations, and a link both ways would make table order circular.
    support_project_id: Mapped[int | None] = mapped_column(Integer)
    secret_key_enc: Mapped[str] = mapped_column(Text, default="")
    go_live_operator: Mapped[str] = mapped_column(String(120), default="")
    go_live_customer: Mapped[str] = mapped_column(String(120), default="")
    live_at: Mapped[datetime | None] = mapped_column(DateTime)
    suspended_reason: Mapped[str] = mapped_column(Text, default="")
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    deletion_due: Mapped[date | None] = mapped_column(Date)
    milestones: Mapped[dict] = mapped_column(JSON, default=dict)  # key -> ISO time it first happened
    milestones_origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    contract_origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    """What an account models and watches: one product, one team, one sample, or the whole estate."""

    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(63))
    criticality: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    environments: Mapped[list] = mapped_column(JSON, default=lambda: ["production"])
    two_approver: Mapped[bool] = mapped_column(Boolean, default=False)
    # A full audit every week (AUD.6), 7 days after the last one finished. A project admin switches it off or on.
    weekly_audits: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    origin: Mapped[str] = mapped_column(String(20), default="empty")  # empty | template | connection
    template_key: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("org_id", "slug"),)


class ProjectMember(Base):
    """A person's role in one project. Separate from the account role stored on the user."""

    __tablename__ = "project_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))  # project_admin | approver | engineer | viewer
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)


class TenantCheck(Base):
    """One readiness check on the road from contract to go-live."""

    __tablename__ = "tenant_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | passed | failed | waived
    detail: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    checked_by: Mapped[str] = mapped_column(String(120), default="")
    origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    __table_args__ = (UniqueConstraint("org_id", "key"),)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Set when the invitation itself grants an account role: the operator's invitation makes the account owner.
    account_role: Mapped[str | None] = mapped_column(String(32))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # operator | ceo | ciso | analyst | engineer
    # The spec 0A account role. role stays as the job title people were invited with.
    account_role: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    # Empty when the action is about the whole account.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# --------------------------------------------------------------------- swarm and incidents


class Agent(Base):
    """A WhiteHat: a small specialist watching one domain."""

    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | learning | dark
    # Null until a measurement exists. 0, 80 and 90 were stamps, not readings.
    coverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_signal_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Counts the loop could not judge, such as sign-in lines with no readable time.
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("project_id", "key"),)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    ref: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(10))  # critical | high | medium | low
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | investigating | verifying | resolved | false_positive
    summary: Mapped[str] = mapped_column(Text, default="")
    business_impact: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    events: Mapped[list[IncidentEvent]] = relationship(order_by="IncidentEvent.at", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("org_id", "ref"),)

    @property
    def closed(self) -> bool:
        return self.status in INCIDENT_CLOSED


# An incident is over once WhiteHats resolved it or an approver called it a false positive.
INCIDENT_CLOSED = ("resolved", "false_positive")


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(20))  # signal | note | action | status
    text: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120), default="")
    actor_id: Mapped[int | None] = mapped_column(Integer)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(240))
    detail: Mapped[str] = mapped_column(Text, default="")
    entity: Mapped[str] = mapped_column(String(160), default="")
    # The real resource (ARN, repository URL). Two projects holding the same one count it once for the account.
    resource: Mapped[str] = mapped_column(String(300), default="")
    mitre: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(40), default="agent")
    origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # A signal is the finding. These point at the clone it was about, instead of a second findings table.
    clone_id: Mapped[int | None] = mapped_column(ForeignKey("clones.id"), index=True)
    clone_version_id: Mapped[int | None] = mapped_column(ForeignKey("clone_versions.id"))
    sense_point_id: Mapped[int | None] = mapped_column(ForeignKey("sense_points.id"))
    node_key: Mapped[str] = mapped_column(String(80), default="")
    # Findings from a pushed change (VS.4): the same weakness in a later commit is the same finding, so it is
    # matched by fingerprint on its branch, and closed (not deleted) when a later commit or the branch removes it.
    fingerprint: Mapped[str] = mapped_column(String(240), default="", index=True)
    branch: Mapped[str] = mapped_column(String(250), default="")
    change_id: Mapped[int | None] = mapped_column(Integer)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_reason: Mapped[str] = mapped_column(String(60), default="")
    scan_plan_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the scan plan or weekly floor run (SCAN.1)
    # A VEX statement that says this finding does not apply (SCAN.5): it stays listed, marked, never closed by it.
    vex_status: Mapped[str] = mapped_column(String(20), default="", server_default="")  # rows written without it too
    vex_statement_id: Mapped[int | None] = mapped_column(Integer)
    # Raised by a WhiteHat that is learning (GL.2): kept to show what it would flag, but it alerts no one, is offered
    # for no incident, and counts in no score or feed. Rows written before this existed are not learning.
    learning: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # The security audit that confirmed this finding (AUD.3); its record, trace and proof check live in the audit's run.
    audit_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Found the first time WhiteHats looked at this part of the account: backlog to review, not something that just
    # happened, so it opens no incident by itself.
    baseline: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    agent: Mapped[Agent | None] = relationship()


class Audit(Base):
    """One security audit of a project's code (AUD.3): what started it, what it covered, what it found, what it cost.

    The audit's own files (the method's ledger, findings, reports and each finding's proof check) stay in `run_dir`;
    this row is what the screens list and what findings point back to.
    """

    __tablename__ = "audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # onboarding | connections | cloud_change | code_change | schedule | request
    trigger: Mapped[str] = mapped_column(String(20))
    trigger_detail: Mapped[str] = mapped_column(Text, default="")  # in words: which change, who asked
    instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    local_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    scope: Mapped[list] = mapped_column(JSON, default=list)  # repository paths; empty is the whole repository
    profile: Mapped[str] = mapped_column(String(10), default="quick")
    repo: Mapped[str] = mapped_column(String(200), default="")
    branch: Mapped[str] = mapped_column(String(250), default="")
    source_ref: Mapped[str] = mapped_column(String(200), default="")
    # A pull request's review (trigger code_change): the change it reviews and the commit it read. Empty otherwise.
    change_id: Mapped[int | None] = mapped_column(Integer, index=True)
    head_sha: Mapped[str] = mapped_column(String(40), default="")
    pull_number: Mapped[int | None] = mapped_column(Integer)
    check_url: Mapped[str] = mapped_column(String(300), default="")  # the "WhiteHats AI review" check it wrote
    # queued | running | complete | incomplete | failed, or skipped: not run, because a full audit already covered the
    # same connections (AUD.6; trigger_detail says when and what)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    step: Mapped[str] = mapped_column(String(20), default="")
    run_dir: Mapped[str] = mapped_column(String(500), default="")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)  # confirmed, needs_validation, rejected
    coverage_percent: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # What a full audit read (AUD.6): each connection by what it is, not by its row, so connecting the same AWS role or
    # the same repositories again is recognised. {"aws:<role arn>": "in words", "github:<owner/repo>": "in words"}
    connections: Mapped[dict] = mapped_column(JSON, default=dict)


class Remediation(Base):
    """A proposed fix that waits for a person's decision."""

    __tablename__ = "remediations"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    # The code finding this fix answers (FX.1), when it was proposed from one.
    signal_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    destination: Mapped[str] = mapped_column(String(80))  # Fix Factory | Jira | Slack | Runbook | ...
    factory_intent: Mapped[str | None] = mapped_column(String(60))
    risk: Mapped[str] = mapped_column(String(10), default="medium")
    simulated_impact: Mapped[str] = mapped_column(String(240), default="")
    # Set only after Fix Factory accepts the fix and returns a run id. Not a simulation.
    factory_run_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | dispatched | rejected | done | verified | failed
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    # A project that asks for two approvers on critical fixes keeps the first one here until a second,
    # different Approver decides.
    first_approved_by: Mapped[str | None] = mapped_column(String(120))
    first_approved_by_id: Mapped[int | None] = mapped_column(Integer)
    first_approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    done_by: Mapped[str | None] = mapped_column(String(120))
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="live")  # live | sample
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PostureSnapshot(Base):
    __tablename__ = "posture_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    score: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("project_id", "day"),)


# --------------------------------------------------------------------- collectors and telemetry


class Collector(Base):
    """A deployed data collector: log reader, network analyzer or process monitor."""

    __tablename__ = "collectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # log | network | process
    mode: Mapped[str] = mapped_column(String(20))  # local | docker | kubernetes | systemd
    host: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | stopped
    token_hash: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_s: Mapped[int] = mapped_column(Integer, default=15)
    context_budget: Mapped[int] = mapped_column(Integer, default=600)  # tokens this collector may use in AI context
    version: Mapped[str] = mapped_column(String(20), default="")
    pid: Mapped[int | None] = mapped_column(Integer)
    events_total: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LogTemplate(Base):
    """A log line shape: variable parts masked so millions of lines collapse into a few hundred rows."""

    __tablename__ = "log_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    signature: Mapped[str] = mapped_column(String(64), index=True)
    pattern: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(10), default="info")
    security: Mapped[bool] = mapped_column(Boolean, default=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    sample: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("org_id", "signature"),)


class LogEvent(Base):
    """Recent raw lines, kept for drill-down. Older lines live on only as template counts."""

    __tablename__ = "log_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    collector_id: Mapped[int] = mapped_column(ForeignKey("collectors.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("log_templates.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)


class TemplateCount(Base):
    """Per-minute counts for each template: the baseline that spikes are measured against."""

    __tablename__ = "template_counts"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("log_templates.id"), index=True)
    minute: Mapped[datetime] = mapped_column(DateTime, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("template_id", "minute"),)


class LogDigest(Base):
    __tablename__ = "log_digests"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    lines: Mapped[int] = mapped_column(Integer)
    raw_tokens: Mapped[int] = mapped_column(Integer)
    templates: Mapped[int] = mapped_column(Integer)
    notable: Mapped[int] = mapped_column(Integer)
    digest_tokens: Mapped[int] = mapped_column(Integer)
    budget_tokens: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSON, default=list)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    model_used: Mapped[str] = mapped_column(String(120), default="")


class NetworkFlow(Base):
    __tablename__ = "network_flows"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    collector_id: Mapped[int] = mapped_column(ForeignKey("collectors.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    proto: Mapped[str] = mapped_column(String(8))
    local_addr: Mapped[str] = mapped_column(String(64))
    local_port: Mapped[int] = mapped_column(Integer)
    remote_addr: Mapped[str] = mapped_column(String(64), default="")
    remote_port: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(20))
    process: Mapped[str] = mapped_column(String(120), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)


class NetworkFinding(Base):
    __tablename__ = "network_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(240))
    detail: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | accepted | resolved
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProcessSample(Base):
    __tablename__ = "process_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    collector_id: Mapped[int] = mapped_column(ForeignKey("collectors.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(120))
    pid: Mapped[int] = mapped_column(Integer)
    user: Mapped[str] = mapped_column(String(80), default="")
    cpu: Mapped[float] = mapped_column(Float, default=0)
    memory_mb: Mapped[float] = mapped_column(Float, default=0)


# --------------------------------------------------------------------- AI models


class ModelDeployment(Base):
    __tablename__ = "model_deployments"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(30))  # azure_openai | openai_compatible | claude_cli | rules
    endpoint: Mapped[str] = mapped_column(String(300), default="")
    deployment: Mapped[str] = mapped_column(String(120), default="")
    api_version: Mapped[str] = mapped_column(String(40), default="v1")
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)  # assistant | summarizer | executive | triage | review
    context_window: Mapped[int] = mapped_column(Integer, default=128000)
    max_output: Mapped[int] = mapped_column(Integer, default=1200)
    priority: Mapped[int] = mapped_column(Integer, default=10)  # lower is tried first
    zero_retention: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="untested")  # ready | needs_key | error | untested
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # primary | optional | "". Set only by the seed. Startup matches this, never the display name.
    seed_slot: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("model_deployments.id"))
    model_label: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(40))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="available")  # available | checked | reading | unbuilt | error
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    last_check: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # sync cursor, counts, last error
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("org_id", "key"),)


class ProjectConnection(Base):
    """An account's connection attached to one project, with the slice of it that project wants.

    The scope is checked by connectors/scope.py: regions, tags, repositories and so on.
    """

    __tablename__ = "project_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), index=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    attached_by: Mapped[str] = mapped_column(String(120), default="")
    attached_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "connector_id"),)


class ChangeEvent(Base):
    """One pushed commit or pull request change WhiteHats is judging (checklist VS.1).

    A change is kept once per commit: (connection, repository, head commit) is unique, so the
    webhook and the poll can both report it and it is still read and judged once. The account owns
    it; project_ids says which projects hold the repository, so findings reach only those.
    """

    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), index=True)
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    repo: Mapped[str] = mapped_column(String(200))
    branch: Mapped[str] = mapped_column(String(250), default="")
    default_branch: Mapped[str] = mapped_column(String(250), default="")
    head_sha: Mapped[str] = mapped_column(String(40))
    first_sha: Mapped[str] = mapped_column(String(40), default="")  # the oldest commit this change covers
    base: Mapped[str] = mapped_column(String(250), default="")  # what the diff was taken against, once fetched
    scan_plan_id: Mapped[int | None] = mapped_column(Integer)  # the scan plan this push ran (SCAN.1)
    pull_number: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10), default="poll")  # webhook | poll
    # pending -> working -> decided | incomplete (an engine did not run) | failed; superseded: a newer
    # commit on the same branch was judged in its place, and its diff covers this one.
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime)  # when GitHub says the push happened
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)  # when a process took it to judge
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    files: Mapped[list] = mapped_column(JSON, default=list)  # the changed files that were read
    skipped: Mapped[list] = mapped_column(JSON, default=list)  # changed files not read, each with the reason
    diff_bytes: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(10), default="")  # clean | suspect
    decision: Mapped[dict] = mapped_column(JSON, default=dict)  # the Decisions API result, as it returned it
    confirmations: Mapped[list] = mapped_column(JSON, default=list)  # VS.3 confirmation of each escalating suspect
    check_url: Mapped[str] = mapped_column(String(300), default="")  # the "WhiteHats" check run on the commit
    check_error: Mapped[str] = mapped_column(Text, default="")  # why the check run was not written, if it was not
    # The inline comments on the pull request (VS.4): when they were added, or why they were not.
    commented_at: Mapped[datetime | None] = mapped_column(DateTime)
    comment_error: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("connector_id", "repo", "head_sha"),)


# --------------------------------------------------------------------- clone studio


class Clone(Base):
    """One estate for one company. Versions hold the graph."""

    __tablename__ = "clones"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    slug: Mapped[str] = mapped_column(String(63))
    environment: Mapped[str] = mapped_column(String(20))
    library_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    versions: Mapped[list[CloneVersion]] = relationship(
        back_populates="clone", order_by="CloneVersion.number", cascade="all, delete-orphan",
    )
    __table_args__ = (UniqueConstraint("project_id", "slug"),)


class CloneVersion(Base):
    """One saved drawing of a clone, with the author and the reason."""

    __tablename__ = "clone_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_id: Mapped[int] = mapped_column(ForeignKey("clones.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(20))
    library_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    clone: Mapped[Clone] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("clone_id", "number"),)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(20))
    owner: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(80), default="")
    access_path: Mapped[str] = mapped_column(String(80), default="")
    credential: Mapped[str] = mapped_column(String(240), default="")
    criticality: Mapped[str] = mapped_column(String(20), default="")
    data_classification: Mapped[list] = mapped_column(JSON, default=list)
    compliance_scope: Mapped[list] = mapped_column(JSON, default=list)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_confirmed_at: Mapped[str] = mapped_column(String(40), default="")
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("clone_version_id", "node_key"),)


class Edge(Base):
    __tablename__ = "edges"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    from_key: Mapped[str] = mapped_column(String(80))
    to_key: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    environment: Mapped[str] = mapped_column(String(20))
    parent_key: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("clone_version_id", "group_key"),)


class AccessPath(Base):
    __tablename__ = "access_paths"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    path_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    environment: Mapped[str] = mapped_column(String(20))
    client: Mapped[str] = mapped_column(String(40), default="")
    routes: Mapped[list] = mapped_column(JSON, default=list)
    credential: Mapped[str] = mapped_column(String(240), default="")
    __table_args__ = (UniqueConstraint("clone_version_id", "path_key"),)


class CredentialRef(Base):
    """A vault pointer. The secret value is never stored here."""

    __tablename__ = "credential_refs"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    ref_key: Mapped[str] = mapped_column(String(80))
    vault_uri: Mapped[str] = mapped_column(String(240))
    purpose: Mapped[str] = mapped_column(String(200), default="")
    __table_args__ = (UniqueConstraint("clone_version_id", "ref_key"),)


class ChangeSet(Base):
    """What one import would change in one clone, kept with each person's decisions until it is applied.

    `items` holds one entry per change (app/services/changesets.py). The raw Terraform state is never
    stored: only the nodes, groups and edges read from it, which hold no secret values.
    """

    __tablename__ = "change_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_id: Mapped[int] = mapped_column(ForeignKey("clones.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(30))  # terraform_state | connections
    label: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | applied | discarded
    base_version: Mapped[int] = mapped_column(Integer)
    applied_version: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    items: Mapped[list] = mapped_column(JSON, default=list)
    left_out: Mapped[dict] = mapped_column(JSON, default=dict)  # resource type -> how many were not drawn


class SensePoint(Base):
    __tablename__ = "sense_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clone_version_id: Mapped[int] = mapped_column(ForeignKey("clone_versions.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="suggested")  # suggested | accepted | rejected
    origin: Mapped[str] = mapped_column(String(12), default="analysis")  # analysis | manual
    paths: Mapped[int] = mapped_column(Integer, default=0)  # attack paths through this node when it was suggested
    rank: Mapped[int] = mapped_column(Integer, default=0)  # 1 is the first suggestion; 0 for a manual one
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    # One sense point per node per saved drawing: a second placement on the same node changes the first.
    __table_args__ = (UniqueConstraint("clone_version_id", "node_key"),)


class VaultSecret(Base):
    """A credential sealed for one company. A clone stores the reference, never this value.

    The column is named value_enc so an offboarding export skips it, the same way it skips
    every other sealed column.
    """

    __tablename__ = "vault_secrets"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    reference: Mapped[str] = mapped_column(String(200))
    value_enc: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("org_id", "reference"),)


class Advisory(Base):
    """One OSV.dev record, stored whole (KB.1). Platform-wide public data: it belongs to no account."""

    __tablename__ = "advisories"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # GHSA-..., PYSEC-..., MAL-...
    ecosystem: Mapped[str] = mapped_column(String(20), index=True)  # the export it came from: PyPI or npm
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # CVE and other ids for the same weakness
    summary: Mapped[str] = mapped_column(Text, default="")
    modified: Mapped[str] = mapped_column(String(40), index=True)  # OSV's own time text, as it gave it
    published: Mapped[str] = mapped_column(String(40), default="")
    withdrawn: Mapped[str] = mapped_column(String(40), default="")
    record: Mapped[dict] = mapped_column(JSON, default=dict)  # the whole record, as OSV published it
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdvisoryPackage(Base):
    """Which package an advisory names, so a lookup by package reads only its own records."""

    __tablename__ = "advisory_packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    advisory_id: Mapped[str] = mapped_column(ForeignKey("advisories.id", ondelete="CASCADE"), index=True)
    ecosystem: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(214), index=True)  # normalised: PyPI folds case and - _ . alike
    __table_args__ = (UniqueConstraint("advisory_id", "ecosystem", "name"),)


class AdvisoryAlias(Base):
    """Another id for the same advisory (a CVE, a GHSA), so a lookup by any of them finds it."""

    __tablename__ = "advisory_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    advisory_id: Mapped[str] = mapped_column(ForeignKey("advisories.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(80), index=True)
    __table_args__ = (UniqueConstraint("advisory_id", "alias"),)


class KnowledgeSource(Base):
    """Where one ecosystem's copy stands: the newest change read, and how the last refresh went."""

    __tablename__ = "knowledge_sources"
    ecosystem: Mapped[str] = mapped_column(String(20), primary_key=True)
    cursor: Mapped[str] = mapped_column(String(40), default="")  # the newest `modified` time already stored
    records: Mapped[int] = mapped_column(Integer, default=0)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_fetched: Mapped[int] = mapped_column(Integer, default=0)  # records downloaded by the last refresh
    last_error: Mapped[str] = mapped_column(Text, default="")


class ScanPlanRecord(Base):
    """One scan plan as it ran (SCAN.1): for a push (`push`) or the weekly floor (`floor`), what it targeted and why,
    or why it was refused. Findings point here through `scan_plan_id`."""

    __tablename__ = "scan_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(10))  # push | floor
    repo: Mapped[str] = mapped_column(String(200), default="")
    change_id: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    refused: Mapped[str] = mapped_column(Text, default="")  # why the plan as written could not run
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class VexStatement(Base):
    """One VEX statement (SCAN.5): a person's triage that a finding does not apply, or a supplier's published word on a
    package or image. Never deleted: a withdrawn or expired statement stays in the history and stops applying."""

    __tablename__ = "vex_statements"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(240), default="", index=True)  # the finding it covers, when one
    image: Mapped[str] = mapped_column(String(300), default="")  # the image it is about, for an image weakness
    vulnerability: Mapped[str] = mapped_column(String(120))
    product: Mapped[str] = mapped_column(String(400))  # an IRI: a package URL or a repository URL
    subcomponent: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(30))  # not_affected | affected | fixed | under_investigation
    justification: Mapped[str] = mapped_column(String(60), default="")
    impact_statement: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(120))  # who decided, by name
    supplier: Mapped[str] = mapped_column(String(120), default="")  # set when a supplier published it
    source_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_by: Mapped[str] = mapped_column(String(120), default="")
    signal_id: Mapped[int | None] = mapped_column(Integer)


class Inventory(Base):
    """A CycloneDX inventory of every part of one image, by digest (SCAN.2). Made once per digest and kept while any
    service runs or is built from it; daily scans read it instead of pulling the image again."""

    __tablename__ = "inventories"
    image: Mapped[str] = mapped_column(String(300), primary_key=True)
    digest: Mapped[str] = mapped_column(String(80), default="")
    made_at: Mapped[datetime | None] = mapped_column(DateTime)
    made_by: Mapped[str] = mapped_column(String(80), default="")
    document: Mapped[dict] = mapped_column(JSON, default=dict)  # the CycloneDX inventory as the tool wrote it
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str] = mapped_column(Text, default="")


class ImageScan(Base):
    """The last scan of one container image a function runs, by digest (VS.5c). Belongs to the platform: the same image
    is the same scan for every account that runs it; findings, which do belong to an account, are signals."""

    __tablename__ = "image_scans"
    image: Mapped[str] = mapped_column(String(300), primary_key=True)  # registry/repository@sha256:...
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime)  # a scan plan asked for it again (SCAN.1)
    weaknesses: Mapped[list] = mapped_column(JSON, default=list)  # the fixable ones, worst first
    unfixed: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class MigrationStep(Base):
    """A one-time data step that has run on this database. Belongs to the platform, not to an account."""

    __tablename__ = "migration_steps"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    done_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



# --------------------------------------------------------------------- the WhiteHats Registry (REG.1)


class RegistryPackage(Base):
    """A fixed build of a library whose upstream release still carries a flaw: built by WhiteHats, proved, and served
    from the company's own package index to the build systems it approves."""

    __tablename__ = "registry_packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    ecosystem: Mapped[str] = mapped_column(String(20), default="PyPI")
    name: Mapped[str] = mapped_column(String(120))  # as the upstream project names itself, e.g. PyPDF2
    version: Mapped[str] = mapped_column(String(80))  # the fixed build, e.g. 3.0.1+whitehats.1
    upstream_version: Mapped[str] = mapped_column(String(60))
    upstream_sha256: Mapped[str] = mapped_column(String(64))  # of the source it was built from, as PyPI lists it
    advisories: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["GHSA-4vvm-4w3v-6mr8", "CVE-2023-36464"]
    summary: Mapped[str] = mapped_column(Text, default="")  # the flaw, in words
    fix_source: Mapped[str] = mapped_column(String(300), default="")  # where the fix came from (the upstream commit)
    patch: Mapped[str] = mapped_column(Text, default="")  # the change applied to the upstream source
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    proof: Mapped[dict] = mapped_column(JSON, default=dict)  # before and after, each with what was run and what it said
    status: Mapped[str] = mapped_column(String(20), default="proved")  # proved | failed | withdrawn
    built_by: Mapped[str] = mapped_column(String(120), default="WhiteHats")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("org_id", "name", "version"),)


class RegistryToken(Base):
    """Read access to the company's registry for one build system or person. Only a hash of the token is kept."""

    __tablename__ = "registry_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))  # who holds it, e.g. "proving-ground CI"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(12), default="")  # the last characters, to tell tokens apart
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)  # set for a token made for one Fix Factory run


class SphereDecision(Base):
    """An approver decision the knowledge sphere learns from (KS.1), recorded where it is made and processed in order
    by the sphere's worker: confirming or rejecting a proposal, approving a fix, marking a finding not affected,
    accepting a network finding, or reversing one of these. Also the sphere's own requests: proposing from an audit,
    and checking that what it cites still exists."""

    __tablename__ = "sphere_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # confirm | reject | fix_approved | not_affected | network_accepted | reversed | propose | check
    kind: Mapped[str] = mapped_column(String(30))
    ref: Mapped[str] = mapped_column(String(120), default="")  # what it is about: "fix:12", "vex:4", "audit:7", a key
    actor: Mapped[str] = mapped_column(String(120), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | working | done | failed
    error: Mapped[str] = mapped_column(Text, default="")
    version_id: Mapped[int | None] = mapped_column(Integer)  # the version it made, when it changed anything


class SphereVersion(Base):
    """One version of a project's knowledge sphere: what was learned and forgotten, and why. The versions in order are
    the sphere; the graph database holds the newest one and is rebuilt from these when it falls behind."""

    __tablename__ = "sphere_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # in plain words, for the history
    decision_id: Mapped[int | None] = mapped_column(Integer)
    # [{"op": "put_node"|"drop_node"|"put_edge"|"drop_edge", ...}] applied in order to the version before
    ops: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("project_id", "number"),)


class SphereGraphState(Base):
    """How far the graph database has applied a project's versions."""

    __tablename__ = "sphere_graph_state"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    applied: Mapped[int] = mapped_column(Integer, default=0)


class FindingDecision(Base):
    """An approver's word on a finding (FD.1): it is a false positive, or its risk is accepted until a date. The same
    finding (same place, same kind) raised again is saved closed with this reason instead of alerting. Withdrawn, or
    past its date, it stops applying and the finding opens again."""

    __tablename__ = "finding_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(300), index=True)  # what makes two findings the same one
    kind: Mapped[str] = mapped_column(String(20))  # false_positive | accepted_risk
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    until: Mapped[datetime | None] = mapped_column(DateTime)  # an accepted risk asks again then
    signal_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40), default="")  # the WhiteHat or scanner that raised it
    agent_id: Mapped[int | None] = mapped_column(Integer)
    remediation_id: Mapped[int | None] = mapped_column(Integer)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_by: Mapped[str] = mapped_column(String(120), default="")


class SettingsCheck(Base):
    """The cloud settings check of one AWS connection (CS.1): how the last one went and which services wait to be checked
    again. Its own row, apart from the connection's state, so the activity reader asking for a check and the check
    writing its result never overwrite each other."""

    __tablename__ = "settings_checks"
    connector_id: Mapped[int] = mapped_column(ForeignKey("connectors.id"), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    status: Mapped[dict] = mapped_column(JSON, default=dict)  # running | done | limited | failed | interrupted, and when
    pending: Mapped[list] = mapped_column(JSON, default=list)  # services the activity history says changed

from .projects import register_project_hooks

register_project_hooks()
