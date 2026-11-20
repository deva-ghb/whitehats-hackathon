"""Every connector on the framework passes the same contract, against recorded answers."""

from datetime import datetime, timedelta

import pytest
from connector_contract import CASES, Case
from connector_kit import HttpReplay, load
from library.resolve import load_library, resolve_clone

from app.connectors import REGISTRY
from app.connectors.base import EventsNotSupported, SourceError, health
from app.connectors.retry import BASE_DELAY_S
from app.connectors.webhooks import remember_delivery, signature_matches

BY_NAME = pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])


def _run(case: Case, scenario: str):
    """The connector against a recording, the recording, and the waits it asked for."""
    replay = case.replay(load(case.name, scenario))
    waits: list[float] = []
    return case.build(replay, waits.append), replay, waits


def test_every_registered_connector_has_a_contract_case():
    assert sorted(REGISTRY) == sorted(c.name for c in CASES)


@BY_NAME
def test_every_permission_says_why_and_none_writes(case):
    kind = REGISTRY[case.name]
    assert kind.permissions
    for permission in kind.permissions:
        assert len(permission.why) > 10, permission
        assert not permission.writes, f"{permission.name} writes; a connector only reads"


@BY_NAME
def test_test_signs_in_and_probes_each_permission(case):
    source, replay, _ = _run(case, "test-ok")
    result = source.test()
    replay.verify()
    assert result.is_ok, result.first_problem()
    assert result.identity
    assert [p.permission for p in result.probes] == [p.name for p in REGISTRY[case.name].permissions]
    assert result.sample > 0


@BY_NAME
def test_a_refused_sign_in_is_explained_in_plain_words(case):
    source, replay, _ = _run(case, "signin-refused")
    with pytest.raises(SourceError) as refused:
        source.test()
    replay.verify()
    message = str(refused.value)
    assert "not authorized" in message or "denied" in message.lower()
    assert "Traceback" not in message and "recorded-placeholder" not in message


@BY_NAME
def test_read_takes_every_page_then_nothing_twice(case):
    source, replay, _ = _run(case, "read-twice")
    first = source.read({})
    second = source.read(first.bookmark)
    replay.verify()
    assert len(first.lines) == case.items_in(replay.recording) > 0
    assert second.lines == []
    assert second.bookmark == first.bookmark
    assert all(line["source"] and "message" in line for line in first.lines)


@BY_NAME
def test_a_sample_does_not_move_the_bookmark(case):
    source, _, _ = _run(case, "test-ok")
    sample = source.read({"kept": 1}, limit=1)
    assert sample.bookmark == {"kept": 1}
    assert len(sample.lines) == 1


@BY_NAME
def test_throttling_waits_and_finishes(case):
    source, replay, waits = _run(case, "throttled")
    batch = source.read({})
    replay.verify()
    assert len(batch.lines) == case.items_in(replay.recording)
    assert BASE_DELAY_S in waits


@BY_NAME
def test_discovery_is_a_clone_of_imported_nodes_the_library_knows(case):
    source, replay, _ = _run(case, "discover-ok")
    found = source.discover()
    replay.verify()
    assert found.nodes
    assert all(node["provenance"]["kind"] == "discovered" for node in found.nodes)  # live discovery (R-054)
    document = {"schema_version": 1, "clone": "contract-check", "environment": "shared", "library_version": "2026.10",
                "version": {"number": 1, "author": "contract", "reason": "discovery check",
                            "created_at": "2026-10-23T00:00:00Z"},
                "nodes": found.nodes, "edges": found.edges, "groups": [], "access_paths": [], "credential_refs": []}
    library = load_library()
    generic = [node for node in found.nodes if library[node["type"].split("@")[0]].get("generic")]
    resolved, problems = resolve_clone(document, library)
    # A resource the library has no type for yet is shown as a Generic node that says what it is.
    assert problems == [f"{node['id']} ({node['name']}): resolves only to the Generic type {node['type'].split('@')[0]}"
                        for node in generic]
    assert all(node["properties"].get("what_is_this") for node in generic)
    assert len(resolved) == len(found.nodes) - len(generic)


@BY_NAME
def test_a_polled_source_refuses_pushed_events(case):
    source, _, _ = _run(case, "test-ok")
    if source.has_events:
        pytest.skip("this connector takes events; its own tests cover them")
    with pytest.raises(EventsNotSupported):
        source.handle_event({}, b"{}")


def test_the_http_replay_answers_in_order_and_refuses_anything_else():
    replay = HttpReplay({"clock": 0, "calls": [
        {"method": "GET", "url": "https://example.test/a", "status": 200, "headers": {"link": "x"}, "body": [1]},
        {"method": "GET", "url": "https://example.test/b", "status": 404, "headers": {}, "body": None},
    ]})
    first = replay("GET", "https://example.test/a", {}, None)
    assert (first.status, first.headers, first.body) == (200, {"link": "x"}, [1])
    with pytest.raises(AssertionError, match="expected GET https://example.test/b"):
        replay("GET", "https://example.test/c", {}, None)
    with pytest.raises(AssertionError, match="not made"):
        HttpReplay({"clock": 0, "calls": [{"method": "GET", "url": "u", "status": 200}]}).verify()


def test_a_signature_is_the_hmac_of_this_exact_body():
    secret, body = "recorded-webhook-secret", b'{"ref":"refs/heads/main"}'
    header = _sign(secret, body)
    assert signature_matches(secret, body, header)
    assert not signature_matches(secret, body, "sha256=" + "00" * 32)
    assert not signature_matches(secret, b'{"ref":"refs/heads/other"}', header)
    assert not signature_matches("another-secret", body, header)
    assert not signature_matches(secret, body, header.removeprefix("sha256="))
    assert not signature_matches(secret, body, "")


def test_each_delivery_is_kept_once_and_the_store_stays_bounded():
    seen = remember_delivery([], "delivery-1")
    assert seen == ["delivery-1"]
    assert remember_delivery(seen, "delivery-1") is None
    assert remember_delivery(seen, "") is None
    assert remember_delivery(seen, "delivery-2") == ["delivery-1", "delivery-2"]
    full = [f"d{i}" for i in range(3)]
    assert remember_delivery(full, "d3", keep=3) == ["d1", "d2", "d3"]


def _sign(secret: str, body: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


EVENTS = pytest.mark.parametrize("case", [c for c in CASES if REGISTRY[c.name].has_events],
                                 ids=[c.name for c in CASES if REGISTRY[c.name].has_events])


@EVENTS
def test_a_signed_delivery_is_read_once_and_a_forged_one_is_refused(case):
    source, replay, _ = _run(case, "events-forged")
    event, body = case.delivery
    lines = source.handle_event({"X-GitHub-Event": event, "X-Hub-Signature-256": _sign(case.webhook_secret, body)},
                                body)
    assert lines and all(line["source"] and line["message"] for line in lines)
    with pytest.raises(SourceError, match="signature"):
        source.handle_event({"X-GitHub-Event": event, "X-Hub-Signature-256": _sign("forged", body)}, body)
    with pytest.raises(SourceError, match="signature"):
        source.handle_event({"X-GitHub-Event": event}, body)
    replay.verify()
    seen = remember_delivery([], "delivery-1")
    assert remember_delivery(seen or [], "delivery-1") is None


@BY_NAME
def test_a_source_that_takes_events_checks_the_signature(case):
    if not REGISTRY[case.name].has_events:
        pytest.skip("this source is polled")
    source, _, _ = _run(case, "events-forged")
    with pytest.raises(SourceError):
        source.handle_event({"X-Hub-Signature-256": "sha256=" + "00" * 32}, b"{}")


NOW = datetime(2026, 10, 23, 12, 0, 0)


def test_health_reads_lag_count_and_error():
    fresh = health(NOW - timedelta(minutes=4), {"total": 42, "last_error": ""}, NOW)
    assert (fresh.items_read, fresh.lag_seconds, fresh.last_error, fresh.is_stalled) == (42, 240, "", False)


@pytest.mark.parametrize(("last_read", "state"), [
    (None, {}),
    (NOW - timedelta(minutes=16), {"total": 3}),
    (NOW - timedelta(minutes=1), {"total": 3, "last_error": "Reading cloudtrail failed: AccessDenied"}),
])
def test_a_source_that_never_read_stopped_or_failed_is_a_blind_spot(last_read, state):
    assert health(last_read, state, NOW).is_stalled


@BY_NAME
def test_discovered_nodes_say_where_they_are_so_scopes_can_route_them(case):
    from app.connectors.scope import resource_of, split

    found = _run(case, "discover-ok")[0].discover()
    for node in found.nodes:
        assert resource_of(node).identity != node["id"], f"{node['name']} has no arn or url"
    accounts = {resource_of(node).account for node in found.nodes} - {""}
    if not accounts:
        pytest.skip("this connector's nodes do not belong to a cloud account")
    mine, other = split(found, [(1, {"accounts": sorted(accounts)}), (2, {"accounts": ["999999999999"]})]).values()
    assert len(mine.nodes) == len(found.nodes) and len(mine.edges) == len(found.edges)
    assert other.nodes == [] and other.edges == []



def test_an_odd_signature_header_is_refused_not_an_error():
    body = b'{"zen": "hello"}'
    odd = "sha256=" + "\u00e9" * 64  # the right length, but not ASCII
    assert signature_matches("secret", body, odd) is False
    assert signature_matches("secret", body, "sha256=" + "zz" * 32) is False


def test_three_events_in_one_millisecond_are_each_read_once_across_resumed_reads():
    from app.connectors.cloudwatch import CloudWatch

    events = [{"eventId": key, "timestamp": 1000, "logStreamName": "s", "message": key} for key in ("a", "b", "c")]

    def one_per_page(group, kwargs):
        left = [e for e in events if e["timestamp"] >= kwargs["startTime"]]
        index = int(kwargs.get("nextToken") or 0)
        page = {"events": left[index:index + 1]}
        if index + 1 < len(left):
            page["nextToken"] = str(index + 1)
        return page

    source = CloudWatch({"log_groups": "g"})
    source._filter = one_per_page
    taken, cursor = [], 1000
    for _ in range(4):
        lines = []
        cursor = source._read_group("g", cursor, lines, limit=1)
        taken += [line["message"] for line in lines]
    assert taken == ["a", "b", "c"]
