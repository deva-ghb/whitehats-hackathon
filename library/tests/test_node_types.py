"""Node type definitions must carry ATT&CK ids and name only known edge types."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "types" / "repo.bitbucket.yaml"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "library.validate", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_node_type_validator_is_built_once():
    from library.validate import _type_validator

    assert _type_validator() is _type_validator()


def test_library_validate_accepts_the_sample_type():
    result = _run()
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_missing_attack_ids_are_rejected(tmp_path: Path):
    bad = tmp_path / "repo.bitbucket.yaml"
    kept = [line for line in SAMPLE.read_text().splitlines(keepends=True) if not line.startswith("attack_techniques:")]
    bad.write_text("".join(kept))
    result = _run(str(bad))
    assert result.returncode != 0
    assert "attack_techniques" in result.stderr


def test_unknown_edge_type_is_rejected(tmp_path: Path):
    bad = tmp_path / "repo.bitbucket.yaml"
    text = SAMPLE.read_text().replace("allowed_out: [built_by]", "allowed_out: [teleports_to]")
    bad.write_text(text)
    result = _run(str(bad))
    assert result.returncode != 0
    assert "teleports_to" in result.stderr
    assert "unknown edge type" in result.stderr
