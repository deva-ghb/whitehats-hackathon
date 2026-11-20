"""House rules match the examples they claim, and a run can be clean or suspect with no engine."""

from pathlib import Path

import pytest

from app.decisions_api import decide
from app.decisions_api.rules import RULES, evaluate
from app.decisions_api.suspect import ACCEPTED, Acceptance, EngineRun, Evidence, Suspect


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_each_rule_matches_its_own_examples_and_rejects_the_others(rule):
    assert rule.weakness
    assert rule.must_match
    assert rule.must_not_match
    assert rule.cwe
    for example in rule.must_match:
        assert evaluate(rule, example), rule.id
    for example in rule.must_not_match:
        assert evaluate(rule, example) == [], rule.id


def test_rule_ids_are_unique():
    assert len({rule.id for rule in RULES}) == len(RULES)


def test_a_provider_key_in_a_tree_is_suspect_without_an_engine(tmp_path: Path):
    prefix = "pgsk_" + "live_"
    (tmp_path / "config.py").write_text('TOKEN = "' + prefix + '9f3k2m8q7w1z0x4c5v6b"\n', encoding="utf-8")
    decision = decide(tmp_path, scanners=())
    assert decision.verdict == "suspect"
    suspect = decision.suspects[0]
    assert suspect.engine == "whitehats"
    assert suspect.rule_id == "whitehats.secrets.provider-live-key"
    assert suspect.evidence.path == "config.py"
    assert suspect.evidence.start_line == 1
    assert suspect.cwe == ["CWE-798"]
    assert suspect.severity == "high"
    assert suspect.score == 70
    assert decision.to_dict()["suspects"][0]["score"] == 70


def test_a_word_only_tail_stays_clean(tmp_path: Path):
    prefix = "pgsk_" + "live_"
    (tmp_path / "config.py").write_text('TOKEN = "' + prefix + 'notarealvaluejustwords"\n', encoding="utf-8")
    assert decide(tmp_path, scanners=()).verdict == "clean"


def test_removing_an_owner_guard_is_suspect_and_keeping_it_is_clean(tmp_path: Path):
    removed = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -8,3 +8,3 @@ def read_record(record_id, actor_id):
     record = store.get(record_id)
-    if record is None or record.owner_id != actor_id:
+    if record is None:
         raise Missing
"""
    kept = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -4,1 +4,2 @@
-    if record.owner_id != actor_id:
+    if record.owner_id != actor_id:
+        deny()
"""
    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "records.py").write_text("def read_record():\n    return None\n", encoding="utf-8")
    hit = decide(tmp_path, removed, scanners=())
    assert hit.verdict == "suspect"
    assert any(item.rule_id == "whitehats.diff.owner-check-removed" for item in hit.suspects)
    assert decide(tmp_path, kept, scanners=()).verdict == "clean"


def test_only_a_low_result_is_a_clean_verdict(tmp_path: Path):
    def low_scanner(root, paths, timeout):
        del root, paths, timeout
        suspect = Suspect("semgrep", "example.low", "info", Evidence("a.py", 1, 1), "low", ["CWE-1000"])
        return [suspect], EngineRun("semgrep", "semgrep/semgrep:1.182.1", 0.01)

    decision = decide(tmp_path, scanners=(low_scanner,))
    assert decision.verdict == "clean"
    assert decision.suspects[0].score == 10
    assert decision.escalating == []


def test_an_acceptance_stops_escalation_and_leaves_the_suspect_visible(tmp_path: Path):
    def high_scanner(root, paths, timeout):
        del root, paths, timeout
        suspect = Suspect(
            "checkov", "CKV_AWS_272", "code signing",
            Evidence("modules/worker/main.tf", 91, 107, resource="aws_lambda_function.worker"),
            "high", ["CWE-494"],
        )
        return [suspect], EngineRun("checkov", "bridgecrew/checkov:3.3.30", 0.01)

    acceptance = Acceptance(
        "CKV_AWS_272", "modules/worker/main.tf", "aws_lambda_function.worker",
        "An image function cannot take a code signing config.", status=ACCEPTED,
    )
    decision = decide(tmp_path, acceptances=(acceptance,), scanners=(high_scanner,))
    assert decision.verdict == "clean"
    assert decision.suspects[0].status == ACCEPTED
    assert decision.escalating == []
    assert decision.suspects[0].score == 70


def test_accepted_findings_file_loads_and_refuses_an_entry_nobody_owns(tmp_path):
    from app.decisions_api.acceptances import AcceptanceError, load_acceptances

    good = tmp_path / "accepted-findings.yaml"
    good.write_text(
        "accepted:\n"
        "  - rule_id: CKV_AWS_272\n    path: terraform/modules/worker/main.tf\n"
        "    resource: module.worker.aws_lambda_function.worker\n"
        "    reason: Code signing is not available for image functions.\n"
        "    accepted_by: Deva\n    accepted_on: 2026-10-26\n", encoding="utf-8")
    (item,) = load_acceptances(good)
    assert (item.rule_id, item.status) == ("CKV_AWS_272", ACCEPTED)
    assert item.reason.endswith("Accepted by Deva on 2026-10-26.")

    bad = tmp_path / "bad.yaml"
    bad.write_text("accepted:\n  - rule_id: CKV_AWS_272\n    path: a.tf\n    resource: r\n    reason: Because.\n",
                   encoding="utf-8")
    with pytest.raises(AcceptanceError, match="accepted_by, accepted_on"):
        load_acceptances(bad)
    bad.write_text("not: a list\n", encoding="utf-8")
    with pytest.raises(AcceptanceError):
        load_acceptances(bad)
