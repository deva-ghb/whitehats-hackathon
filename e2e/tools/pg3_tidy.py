"""PG.3: what a person does in the Studio after the three imports: delete the nodes that live discovery added a second time
(the Terraform state and discovery name the same resource and the state's copy has no ARN, so they were not matched).
Saves the result as a new version of the clone, reason says so. Usage: python3 pg3_tidy.py <base url> <v4 export json>"""
import json
import sys
from pathlib import Path

from pg3_api import call, sign_in

BASE, SRC = sys.argv[1:3]
doc = json.loads(Path(SRC).read_text())
SAME = {"/aws/lambda/shop-api", "/aws/lambda/payments-worker", "/aws/lambda/shop-api-authorizer", "webhook-signing-secret",
        "api-token", "2bdxq6o11f", "payments-worker", "shop-api-authorizer", "shop-api"}
drop = {n["id"] for n in doc["nodes"] if (n.get("provenance") or {}).get("source") == "aws" and n["name"] in SAME}
print("removing", len(drop))
assert not any(e["from"] in drop or e["to"] in drop for e in doc["edges"]), "an edge touches a duplicate"
doc["nodes"] = [n for n in doc["nodes"] if n["id"] not in drop]
token = sign_in(BASE, "engineer@acme.example")
status, clones = call(BASE, token, "GET", "/api/clones?project=main")
cid = next(c["id"] for c in clones["clones"] if c["clone"] == "proving-ground-imported")
status, out = call(BASE, token, "POST", f"/api/clones/{cid}/versions?project=main",
                   {"document": doc, "reason": "Tidied after the imports: removed 9 nodes that discovery added a second time"})
print(status, out)
