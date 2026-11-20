"""PG.3 live run: import the proving ground into a new clone on a deployed WhiteHats platform, state, then repository,
then what the connections found, reviewing through the same API the review screen uses.

WH_PASSWORD (environment) is the sign-in password; PG_STATE_FILE is the git-ignored Terraform state. The state is handed to the
import call only and nothing from it is printed. Usage: python3 pg3_run.py <base url> <leg> [clone name]
  leg: state | repository | connections | export
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

from pg3_api import call, sign_in

BASE, LEG = sys.argv[1], sys.argv[2]
NAME = sys.argv[3] if len(sys.argv) > 3 else "proving-ground-imported"
EMAIL = "engineer@acme.example"
P = "?project=main"
token = sign_in(BASE, EMAIL)


def api(method, path, body=None):
    sep = "&" if "?" in path else "?"
    return call(BASE, token, method, f"{path}{sep}project=main", body)


def clone_id():
    _, found = api("GET", "/api/clones")
    for entry in found["clones"]:
        if entry["clone"] == NAME:
            return entry["id"]
    return None


def review(import_id):
    status, found = api("GET", f"/api/imports/{import_id}")
    assert status == 200, (status, found)
    held = [i for i in found["items"] if i["held"]]
    rest = [i for i in found["items"] if not i["held"]]
    print("changes", len(found["items"]), "held", len(held), "left_out", json.dumps(found["left_out"]))
    print("by kind/action", dict(Counter((i["kind"], i["action"]) for i in found["items"])))
    for item in held:
        print("HELD", item["item"]["type"], "|", item["item"]["name"])
    for item in found["items"]:
        if item["action"] != "add":
            print("ACTION", item["action"], item["kind"], item["item"].get("name") or item["item"].get("type"))
    waiting = [i for i in rest if i["waits_for"]]
    print("accepted-ready waiting on something:", len(waiting))
    keys = [i["key"] for i in rest]
    if keys:
        status, found = api("PUT", f"/api/imports/{import_id}/decisions", {"keys": keys, "decision": "accept"})
        assert status == 200, (status, found)
        print("after accept:", {k: found[k] for k in ("changes", "undecided", "accepted", "rejected")}, "after", found.get("after"))
    status, done = api("POST", f"/api/imports/{import_id}/apply")
    print("apply", status, json.dumps(done)[:1500])


if LEG in ("state", "repository", "connections"):
    source = {"state": "terraform_state", "repository": "repository", "connections": "connections"}[LEG]
    body = {"source": source}
    existing = clone_id()
    if existing is None:
        body["new_clone"] = {"name": NAME, "environment": "prod"}
    else:
        body["clone_id"] = existing
    if LEG == "state":
        body["state"] = json.loads(Path(os.environ["PG_STATE_FILE"]).read_text())
    status, made = api("POST", "/api/imports", body)
    print("start", status, json.dumps(made)[:600])
    if status == 200 and made.get("id"):
        review(made["id"])
elif LEG == "export":
    cid = clone_id()
    status, doc = api("GET", f"/api/clones/{cid}/export")
    out = sys.argv[4]
    Path(out).write_text(json.dumps(doc, indent=2))
    print("exported", status, len(doc["nodes"]), len(doc["groups"]), len(doc["edges"]), "version", doc["version"]["number"])
