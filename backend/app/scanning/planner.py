"""Where to look for one push (SCAN.1): the changed files, each with the catalogue scanners that read it, and the image
of each service whose code changed, read from the twin. Nothing else in the repository is planned: the weekly floor
covers the rest, so a narrow plan can delay a finding but never hide one.

The planner writes a plan as plain data and the plan is checked like any other (plan.checked): an agent that plans
instead of these rules is held to exactly the same catalogue and scope.
"""

from __future__ import annotations

from collections.abc import Iterable

from .catalogue import defaults

MADE_BY = "WhiteHats planner (rules: changed files and the images of the services they belong to)"


def services(nodes: Iterable[dict], repo: str) -> list[tuple[str, dict]]:
    """(code folder, node) for every node whose code lives in `repo`, longest folder first, so the closest one wins."""
    found = [(str(node["properties"].get("code_folder") or ""), node) for node in nodes
             if (node.get("properties") or {}).get("code_repo") == repo]
    return sorted(found, key=lambda item: (-len(item[0]), str(item[1].get("id"))))


def owner(path: str, placed: list[tuple[str, dict]]) -> dict | None:
    """The service whose code folder holds `path`."""
    return next((node for folder, node in placed if folder and path.startswith(folder.rstrip("/") + "/")), None)


def for_push(repo: str, branch: str, head: str, files: Iterable[str], nodes: Iterable[dict]) -> dict:
    placed = services(nodes, repo)
    targets, touched = [], {}
    for path in sorted(files):
        service = owner(path, placed)
        reason = "changed in this push"
        if service is not None:
            reason += f", part of {service.get('name', service['id'])}"
            touched[str(service["id"])] = service
        targets.append({"repo": repo, "path": path, "scanners": list(defaults(path)), "reason": reason})
    for service in touched.values():
        image = str((service.get("properties") or {}).get("image") or "")
        if image:
            targets.append({"image": image, "scanners": ["trivy"],
                            "reason": f"the image {service.get('name', service['id'])} runs, since its code changed"})
    return {"trigger": f"push {head[:7]} to {branch or 'the default branch'} of {repo}", "made_by": MADE_BY,
            "targets": targets}
