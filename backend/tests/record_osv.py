"""Record what the public OSV API answers for a few packages, and those records from OSV's bucket (KB.1).

Usage (from platform/backend; reads public data only, no account and no key):

    uv run python tests/record_osv.py

Writes tests/fixtures/osv/recorded.json: for each query, the API's answer; and every record it names, as the bucket
OSV's bulk exports come from holds it. test_knowledge.py builds the knowledge base from those records and checks its
lookups give the API's answer, field by field. Run it again to bring the recording up to date.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "fixtures" / "osv" / "recorded.json"
API = "https://api.osv.dev/v1/query"
BUCKET = "https://osv-vulnerabilities.storage.googleapis.com"
# S04's pinned dependency, then well-known packages in both ecosystems so ranges, aliases and fixes vary.
QUERIES = [
    ("PyPI", "pyyaml", "5.3.1"),
    ("PyPI", "jinja2", "2.10"),
    ("PyPI", "requests", "2.19.0"),
    ("PyPI", "django", "3.2"),
    ("npm", "lodash", "4.17.15"),
    ("npm", "minimist", "1.2.0"),
]


def _query(client: httpx.Client, ecosystem: str, name: str, version: str) -> list[dict]:
    found, token = [], None
    while True:
        body = {"package": {"ecosystem": ecosystem, "name": name}, "version": version}
        if token:
            body["page_token"] = token
        answer = client.post(API, json=body)
        answer.raise_for_status()
        page = answer.json()
        found.extend(page.get("vulns") or [])
        token = page.get("next_page_token")
        if not token:
            return found


def main() -> None:
    queries, records = [], {}
    with httpx.Client(timeout=60) as client:
        for ecosystem, name, version in QUERIES:
            vulns = _query(client, ecosystem, name, version)
            queries.append({"ecosystem": ecosystem, "name": name, "version": version, "vulns": vulns})
            for vuln in vulns:
                if vuln["id"] not in records:
                    answer = client.get(f"{BUCKET}/{ecosystem}/{vuln['id']}.json")
                    answer.raise_for_status()
                    records[vuln["id"]] = {"ecosystem": ecosystem, "record": answer.json()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"queries": queries, "records": records}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {len(queries)} queries and {len(records)} records into {OUT}")


if __name__ == "__main__":
    main()
