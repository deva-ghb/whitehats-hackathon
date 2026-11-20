"""HAND.1: a weakness in the live setup (a public API with no rate limit) goes to a person with steps, because Fix
Factory cannot change a live account, and it closes only when a later read of the account shows the setting."""

from itertools import count

import pytest
from conftest import login, remove_project
from sqlalchemy import select

from app.connectors.aws_enrich import Enricher
from app.connectors.aws_resources import UNKNOWN_ENVIRONMENT
from app.db import SessionLocal
from app.models import Clone, Organization, Project, ProjectMember, Remediation, Signal, User
from app.services import posture, setup_checks
from app.services.clones import blank_document, create_clone, read_clone, save_version

ARN = "arn:aws:apigateway:ap-southeast-2::/apis/abc123"
_names = count(1)


def _api_node(**props) -> dict:
    return {"id": "awsres_api", "type": setup_checks.API_TYPE, "name": "shop-api", "environment": UNKNOWN_ENVIRONMENT,
            "provenance": {"kind": "discovered", "source": "aws"},
            "properties": {"arn": ARN, "api_id": "abc123", "api_type": "http", "region": "ap-southeast-2", **props}}


@pytest.fixture
def mapped(client):
    """A project of the demo company whose map holds one public HTTP API with no rate limit."""
    slug = f"setup-checks-{next(_names)}"
    with SessionLocal() as db:
        org = db.scalar(select(Organization).where(Organization.slug == "acme"))
        project = Project(org_id=org.id, name="Setup checks", slug=slug)
        db.add(project)
        db.flush()
        ceo = db.scalar(select(User).where(User.email == "ceo@acme.example"))
        db.add(ProjectMember(project_id=project.id, user_id=ceo.id, role="approver"))
        document = blank_document(slug, "production", "AWS connection", "map from its connections")
        document["nodes"] = [_api_node(public=True, rate_limit="none")]
        clone = create_clone(db, org.id, document, "AWS connection", "map from its connections", project_id=project.id)
        ids = {"project": project.id, "slug": slug, "clone": clone.id, "org": org.id}
    yield ids
    remove_project(ids["project"])


def _check(clone_id: int) -> dict:
    with SessionLocal() as db:
        done = setup_checks.run(db, db.get(Clone, clone_id))
        db.commit()
        return done


def _set(ids: dict, **props) -> None:
    with SessionLocal() as db:
        document = read_clone(db, ids["org"], ids["clone"])
        document["nodes"] = [_api_node(**props)]
        save_version(db, ids["org"], ids["clone"], document, "AWS connection", "AWS saw UpdateStage")


def _rows(project_id: int) -> tuple[list[Signal], list[Remediation]]:
    with SessionLocal() as db:
        signals = db.scalars(select(Signal).where(Signal.project_id == project_id)).all()
        fixes = db.scalars(select(Remediation).where(Remediation.project_id == project_id)).all()
        db.expunge_all()
        return list(signals), list(fixes)


def test_a_public_api_with_no_rate_limit_goes_to_a_person_with_steps(mapped):
    assert _check(mapped["clone"])["opened"] == 1
    assert _check(mapped["clone"])["opened"] == 0  # the same weakness on the next read is the same finding
    (signal,), (fix,) = _rows(mapped["project"])
    assert (signal.severity, signal.source, signal.node_key, signal.resource) == ("high", setup_checks.SOURCE, "awsres_api", ARN)
    assert signal.title == "The public API shop-api has no rate limit" and signal.closed_at is None
    assert (fix.destination, fix.factory_intent, fix.status, fix.signal_id) == ("A person", None, "pending", signal.id)
    assert "Fix Factory only changes code" in fix.why
    assert "Throttling" in fix.action and "closes this fix" in fix.action


def test_a_stage_node_from_an_older_read_raises_nothing(mapped):
    node = _api_node(public=True, rate_limit="none")
    node["properties"]["arn"] = ARN + "/stages/$default"
    assert setup_checks._no_rate_limit(node) is None


def test_an_api_with_a_limit_or_not_public_is_left_alone(mapped):
    _set(mapped, public=True, rate_limit="set", throttling_rps=10.0)
    assert _check(mapped["clone"])["opened"] == 0
    _set(mapped, public=False, rate_limit="none")
    assert _check(mapped["clone"])["opened"] == 0


def test_it_closes_when_the_read_shows_the_limit_and_the_score_rises(client, mapped):
    _check(mapped["clone"])
    with SessionLocal() as db:
        before = posture.compute(db, mapped["org"], project_id=mapped["project"])["score"]
    headers = login(client, "ceo@acme.example")
    _, (fix,) = _rows(mapped["project"])
    taken = client.post(f"/api/remediations/{fix.id}/approve?project={mapped['slug']}", headers=headers).json()
    assert taken["status"] == "dispatched" and taken["closes_itself"]
    assert taken["result"].startswith("Taken on by") and "reads the change in AWS" in taken["result"]
    refused = client.post(f"/api/remediations/{fix.id}/done?project={mapped['slug']}", headers=headers)
    assert refused.status_code == 409 and "cannot be marked done by hand" in refused.json()["detail"]

    _set(mapped, public=True, rate_limit="set", throttling_rps=10.0, throttling_burst=20)
    assert _check(mapped["clone"])["closed"] == 1
    (signal,), (fix,) = _rows(mapped["project"])
    assert signal.closed_reason == setup_checks.CHANGED
    assert (fix.status, fix.done_by) == ("verified", "WhiteHats")
    assert "now has a rate limit of 10 requests a second (bursts of 20)" in fix.result
    with SessionLocal() as db:
        assert posture.compute(db, mapped["org"], project_id=mapped["project"])["score"] > before


def test_the_fix_page_shows_the_setting_not_code(client, mapped):
    _check(mapped["clone"])
    _, (fix,) = _rows(mapped["project"])
    page = client.get(f"/api/remediations/{fix.id}?project={mapped['slug']}", headers=login(client, "ceo@acme.example")).json()
    assert page["evidence"]["kind"] == "setting" and page["evidence"]["finding"]["resource"] == ARN
    assert page["change"] == {"kind": "manual", "destination": "A person", "steps": fix.action.splitlines()}
    assert page["touches"]["where"][0]["name"] == "shop-api"


class _Answers:
    def __init__(self, stages, api=None):
        self.stages, self.api = stages, api or {"Name": "shop-api"}

    def __call__(self, service, method, *, region="", **params):
        assert service == "apigatewayv2" and region == "ap-southeast-2" and params == {"ApiId": "abc123"}
        return self.api if method == "get_api" else {"Items": self.stages}


@pytest.mark.parametrize(("stages", "expected"), [
    ([{"DefaultRouteSettings": {"DetailedMetricsEnabled": False}}], {"rate_limit": "none"}),
    ([{"DefaultRouteSettings": {"ThrottlingRateLimit": 10.0, "ThrottlingBurstLimit": 20}}],
     {"rate_limit": "set", "throttling_rps": 10.0, "throttling_burst": 20}),
    # one stage with a limit and one without: the one without is open to a flood
    ([{"DefaultRouteSettings": {"ThrottlingRateLimit": 10.0}}, {"DefaultRouteSettings": {}}],
     {"rate_limit": "none", "throttling_rps": 10.0}),
    ([{"DefaultRouteSettings": {"ThrottlingRateLimit": 10.0}}, {"DefaultRouteSettings": {"ThrottlingRateLimit": 50.0}}],
     {"rate_limit": "set", "throttling_rps": 50.0}),
    ([], {"rate_limit": "none"}),
    # raised to the account's own limit "for a load test": it caps nothing
    ([{"DefaultRouteSettings": {"ThrottlingRateLimit": 10000.0, "ThrottlingBurstLimit": 5000}}],
     {"rate_limit": "none", "throttling_rps": 10000.0, "throttling_burst": 5000}),
])
def test_the_read_says_whether_every_stage_has_a_rate_limit(stages, expected):
    extra = Enricher(_Answers(stages), 50).describe(ARN)
    assert extra == {"display_name": "shop-api", "public": True, **expected}


def test_an_api_without_its_default_address_is_not_public():
    extra = Enricher(_Answers([], {"Name": "inside", "DisableExecuteApiEndpoint": True}), 50).describe(ARN)
    assert extra["public"] is False


def test_a_stage_tagged_on_its_own_is_not_read_as_a_second_api():
    assert Enricher(_Answers([]), 50).describe(ARN + "/stages/$default") == {}


def test_a_rest_api_is_not_read_yet():
    assert Enricher(_Answers([]), 50).describe("arn:aws:apigateway:ap-southeast-2::/restapis/xyz") == {}
