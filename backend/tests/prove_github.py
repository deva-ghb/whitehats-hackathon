"""Live, read-only proof of the GitHub connector against the proving-ground repository.

    uv run python tests/prove_github.py --token   # what GITHUB_TOKEN can see of the calls the connector makes
    uv run python tests/prove_github.py --app     # the real connector through the WhiteHats GitHub App
    uv run python tests/prove_github.py --scan    # the repository scan of every repository the App can see

Every call is a GET (the App's token exchange is a POST to GitHub's sign-in, which changes nothing).
Nothing is written to GitHub. Tokens are sent, never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ["WH_DATA_DIR"] = tempfile.mkdtemp(prefix="wh-prove-")
os.environ["WH_LOAD_DOTENV"] = "0"
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]

from app.connectors.github import API, GitHub
from app.connectors.http import httpx_transport


def token_report(repo: str) -> None:
    """Each call the connector makes, answered for GITHUB_TOKEN: which ones a personal token can and cannot do."""
    headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    meta = httpx_transport("GET", f"{API}/repos/{repo}", headers)
    branch = (meta.body or {}).get("default_branch", "main")
    calls = {
        "repository (metadata)": f"/repos/{repo}",
        "workflows (actions)": f"/repos/{repo}/actions/workflows",
        "open pull requests": f"/repos/{repo}/pulls?state=open&per_page=1",
        "branches (contents)": f"/repos/{repo}/branches",
        "commits (contents)": f"/repos/{repo}/commits?sha={branch}&per_page=1",
        "file tree (contents)": f"/repos/{repo}/git/trees/{branch}?recursive=1",
    }
    for label, path in calls.items():
        answer = httpx_transport("GET", f"{API}{path}", headers)
        detail = ""
        if answer.status == 200 and "workflows" in path:
            detail = ", ".join(w["path"] for w in answer.body["workflows"])
        elif answer.status != 200:
            detail = str((answer.body or {}).get("message", ""))
        print(f"{label:24} {answer.status} {detail}")
    owned = httpx_transport("GET", f"{API}/user/repos?affiliation=owner&per_page=100", headers)
    names = [r["full_name"] for r in owned.body or []]
    print(f"repositories the token's owner has: {len(names)}; proving ground among them: {repo in names}")


def app_proof() -> None:
    from connector_kit import app_installation

    from app.connectors.github_app import app_token_from_settings

    installation = app_installation(app_token_from_settings()(), os.environ.get("GITHUB_APP_INSTALLATION_ID", ""))
    source = GitHub({"installation_id": installation}, app_token=app_token_from_settings())
    result = source.test()
    print("identity:", result.identity)
    for probe in result.probes:
        print(f"  {probe.permission:20} {'ok' if probe.ok else 'MISSING'}  {probe.detail}")
    found = source.discover()
    for node in found.nodes:
        props = node["properties"]
        extra = props.get("dependency_manifests") or props.get("workflows")
        print(f"{node['type']:28} {node['name']}  {extra}")


def scan_proof() -> None:
    """Scan every repository the App sees and print what was found. Reads only; prints names and counts, never file text."""
    from collections import Counter

    from connector_kit import app_installation

    from app.connectors.github_app import app_token_from_settings
    from app.services.repo_scan import scan_repositories

    installation = app_installation(app_token_from_settings()(), os.environ.get("GITHUB_APP_INSTALLATION_ID", ""))
    source = GitHub({"installation_id": installation}, app_token=app_token_from_settings())
    found = scan_repositories(source, environment="prod")
    names = {node["id"]: node["name"] for node in found.nodes}
    print(f"{len(found.nodes)} nodes, {len(found.edges)} connections")
    for node in found.nodes:
        runtime = node["properties"].get("runtime")
        print(f"  {node['type']:30} {node['name']}" + (f"  runtime {runtime}" if runtime else ""))
    for edge in found.edges:
        print(f"  {names[edge['from']]} {edge['type']} {names[edge['to']]}")
    print("connections by type:", dict(Counter(edge["type"] for edge in found.edges)))
    print("read and not drawn:", found.not_drawn)
    for note in found.notes:
        print("note:", note)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--token", action="store_true")
    mode.add_argument("--app", action="store_true")
    mode.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    if args.token:
        token_report(os.environ["PROVING_GROUND_REPO"])
    elif args.scan:
        scan_proof()
    else:
        app_proof()


if __name__ == "__main__":
    main()
