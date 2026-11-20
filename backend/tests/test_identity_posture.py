"""ID.2: the company's sign-in provider, read for people without multi-factor, unused accounts and too many admins."""

from datetime import datetime

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Connector, Organization, Project, Signal
from app.services import identity_posture as idp

NOW = datetime(2026, 11, 11, 12, 0)


class _Answer:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers, self.text = body, status, headers or {}, str(body)

    def json(self):
        return self._body


class _Http:
    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, headers=None):
        self.asked.append(url)
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                return answer
        return _Answer({"error": "not found"}, 404)


def test_okta_people_admins_and_factors_are_read_page_by_page():
    base = "https://acme.okta.com/api/v1"
    http = _Http({
        f"{base}/iam/assignees/users": _Answer({"value": [{"id": "u1"}]}),
        f"{base}/users?": _Answer([{"id": "u1", "profile": {"login": "ana@acme"}, "lastLogin": "2026-11-10T08:00:00Z"},
                                   {"id": "u2", "profile": {"login": "ben@acme"}, "lastLogin": "2026-06-01T08:00:00Z"}],
                                  headers={"link": f'<{base}/users?after=u2>; rel="next"'}),
        f"{base}/users/u1/factors": _Answer([{"status": "ACTIVE"}]),
        f"{base}/users/u2/factors": _Answer([]),
    })
    http.routes = {f"{base}/users?after=u2": _Answer([]), **http.routes}
    people = idp.read_okta(http, "acme.okta.com", "t")
    assert [(p.name, p.admin, p.mfa) for p in people] == [("ana@acme", True, True), ("ben@acme", False, False)]
    assert people[1].last_sign_in == datetime(2026, 6, 1, 8, 0)


def test_entra_without_a_premium_plan_reads_people_with_multi_factor_and_sign_ins_unknown():
    users = "https://graph.microsoft.com/v1.0/users?$filter=accountEnabled eq true&$top=999&$select=id,displayName,userPrincipalName,createdDateTime"
    http = _Http({
        "https://graph.microsoft.com/v1.0/reports": _Answer({"error": "needs P1"}, 403),
        users + ",signInActivity": _Answer({"error": "needs P1"}, 403),
        users: _Answer({"value": [{"id": "e1", "userPrincipalName": "cy@acme.com"}]}),
    })
    [person] = idp.read_entra(http, "t")
    assert (person.name, person.mfa, person.last_sign_in) == ("cy@acme.com", None, None)
    assert idp.judge([person], "entra", NOW) == []  # what the provider did not say is never a finding


def test_google_people_come_with_their_two_step_state_and_last_sign_in():
    http = _Http({"https://admin.googleapis.com/admin/directory/v1/users": _Answer({"users": [
        {"id": "g1", "primaryEmail": "di@acme.com", "isAdmin": True, "isEnrolledIn2Sv": False,
         "lastLoginTime": "2026-11-01T00:00:00.000Z"},
        {"id": "g2", "primaryEmail": "ed@acme.com", "isEnrolledIn2Sv": True, "lastLoginTime": "1970-01-01T00:00:00.000Z"}]})})
    people = idp.read_google(http, "t")
    assert [(p.name, p.admin, p.mfa, p.last_sign_in is None) for p in people] == [
        ("di@acme.com", True, False, False), ("ed@acme.com", False, True, True)]


def test_weak_accounts_are_raised_and_closed_when_put_right(client, monkeypatch):
    people = [idp.Person("a", "ana", True, False, datetime(2026, 11, 10)),
              idp.Person("b", "ben", False, True, datetime(2026, 6, 1)),
              *[idp.Person(f"x{i}", f"admin{i}", True, True, NOW) for i in range(5)]]
    found = idp.judge(people, "okta", NOW)
    assert [(f["key"], f["severity"], f["title"]) for f in found] == [
        ("nomfa-a", "high", "Administrator can sign in without multi-factor"),
        ("stale-b", "medium", "Account unused for 163 days"),
        ("admins", "medium", "6 administrators on Okta")]
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = db.scalar(select(Project).where(Project.org_id == org.id).order_by(Project.id))
        c = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "okta"))
        pid = project.id
        monkeypatch.setattr(idp.routing, "projects_for", lambda *a: [pid])
        try:
            assert idp.apply(db, c, found) == (3, 0)
            assert idp.apply(db, c, found[1:]) == (0, 1)  # ana turned on multi-factor
        finally:
            db.rollback()
            db.execute(delete(Signal).where(Signal.fingerprint.like("idp-okta-%")))
            db.commit()
