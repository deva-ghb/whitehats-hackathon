"""Build the ATT&CK reference file the validator checks technique ids against.

Run it on MITRE's enterprise bundle (https://github.com/mitre-attack/attack-stix-data, enterprise-attack.json)
when ATT&CK publishes a new version, then fix any type the validator now refuses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REFERENCE = "attack-enterprise.json"


def build(bundle: dict) -> dict:
    """Every technique id with its name, and for a revoked or deprecated one, what replaced it."""
    objects = bundle["objects"]
    external = {obj["id"]: _attack_id(obj) for obj in objects if _attack_id(obj)}
    replaced = {
        external[rel["source_ref"]]: external.get(rel["target_ref"])
        for rel in objects
        if rel.get("type") == "relationship" and rel.get("relationship_type") == "revoked-by" and rel["source_ref"] in external
    }
    techniques = {}
    for obj in objects:
        technique = external.get(obj["id"]) if obj.get("type") == "attack-pattern" else None
        if technique is None:
            continue
        retired = bool(obj.get("revoked") or obj.get("x_mitre_deprecated"))
        techniques[technique] = {"name": obj["name"], "retired": retired, "replaced_by": replaced.get(technique)}
    return {"attack_version": _collection_version(objects), "techniques": dict(sorted(techniques.items()))}


def _attack_id(obj: dict) -> str | None:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id")
    return None


def _collection_version(objects: list[dict]) -> str:
    return next((obj.get("x_mitre_version", "") for obj in objects if obj.get("type") == "x-mitre-collection"), "")


def _dumps(reference: dict) -> str:
    """One technique per line, so a new ATT&CK version reads as a short diff."""
    rows = [f"  {json.dumps(tid)}: {json.dumps(entry)}" for tid, entry in reference["techniques"].items()]
    version = json.dumps(reference["attack_version"])
    return "{\n \"attack_version\": " + version + ",\n \"techniques\": {\n" + ",\n".join(rows) + "\n }\n}\n"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m library.attack <enterprise-attack.json> <output.json>", file=sys.stderr)
        return 1
    reference = build(json.loads(Path(args[0]).read_text()))
    Path(args[1]).write_text(_dumps(reference))
    print(f"ATT&CK {reference['attack_version']}: {len(reference['techniques'])} techniques")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
