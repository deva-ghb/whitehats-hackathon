"""Versioned estate inventories for the local WhiteHats backend."""

from sqlalchemy import select

from ..models import Clone
from . import clones

ESTATES = {
    "main": ("Storefront", "Checkout API", "Orders database"),
    "health-platform": ("Patient portal", "Appointment API", "Appointments database"),
    "saas-with-ai": ("Workspace app", "Inference gateway", "Workspace database"),
}
ENVIRONMENT = "prod"
INVENTORY_REVISION = "Infrastructure and service dependencies"

# Place the perimeter above the application and data, then the execution and background services.
POSITIONS = {
    "dns": (0, 0), "cdn": (1, 0), "frontend": (2, 0), "gateway": (3, 0), "balancer": (4, 0),
    "repo": (0, 1), "backend": (1, 1), "db": (2, 1), "cache": (3, 1), "secrets": (4, 1),
    "role": (0, 2), "runtime": (1, 2), "worker": (2, 2), "jobs": (3, 2), "archive": (4, 2),
    "logs": (2, 3),
}
PREVIOUS_POSITIONS = {
    "frontend": (0, 0), "gateway": (0, 180), "repo": (310, 180), "backend": (310, 0),
    "db": (620, 0), "jobs": (620, 190), "worker": (310, 365), "archive": (620, 365), "logs": (930, 190),
}


def _service(prefix, suffix, name, properties, x, y):
    return {
        "id": f"{prefix}_{suffix}", "type": "service.backend@1", "name": name,
        "environment": ENVIRONMENT, "owner": "Application engineering",
        "properties": properties, "layout": {"x": x, "y": y},
        "provenance": {"kind": "drawn", "source": "Architecture inventory"},
    }


def _edge(prefix, source, target, kind, description, data):
    return {"from": f"{prefix}_{source}", "to": f"{prefix}_{target}", "type": kind,
            "properties": {"description": description, "data": data}}


def _resource(prefix, suffix, kind, name, properties, x, y):
    return {"id": f"{prefix}_{suffix}", "type": kind, "name": name, "environment": ENVIRONMENT,
            "owner": "Platform engineering", "properties": properties, "layout": {"x": x, "y": y},
            "provenance": {"kind": "drawn", "source": "Architecture inventory"}}


def seed(db, project):
    slug = f"{project.slug}-estate"
    existing = db.scalar(select(Clone).where(Clone.org_id == project.org_id, Clone.slug == slug))
    if existing and any(version.reason == INVENTORY_REVISION for version in existing.versions):
        return
    doc = clones.blank_document(slug, ENVIRONMENT, "Platform engineering", INVENTORY_REVISION)
    estates = ESTATES.items() if project.slug == "whole-estate" else (
        (project.slug, ESTATES.get(project.slug, (project.name, "Application API", "Application database"))),
    )
    original_positions = {}
    for index, (key, (client, api, database)) in enumerate(estates):
        prefix = key.replace("-", "_")
        y = index * 640
        for suffix, (x, offset) in PREVIOUS_POSITIONS.items():
            positions = [{"x": x, "y": y + offset}]
            if suffix in ("frontend", "backend", "db"):
                positions.append({"x": x, "y": index * 260})
            original_positions[f"{prefix}_{suffix}"] = positions
        first = len(doc["nodes"])
        doc["nodes"].extend([
            _service(prefix, "frontend", client, {"kind": "bff", "runtime": "Node.js 22", "exposure": "public"}, 0, y),
            {"id": f"{prefix}_gateway", "type": "edge.api-gateway@1", "name": f"{api} gateway",
             "environment": ENVIRONMENT, "owner": "Platform engineering",
             "properties": {"api_id": f"{prefix}-gateway", "api_type": "http", "region": "ap-south-1",
                            "authorizer": "jwt", "access_logging": True},
             "layout": {"x": 0, "y": y + 180}, "provenance": {"kind": "drawn", "source": "Architecture inventory"}},
            {"id": f"{prefix}_repo", "type": "repo.github@1", "name": f"{api} repository",
             "environment": ENVIRONMENT, "owner": "Application engineering",
             "properties": {"url": f"https://github.com/acme/{key}-service", "auth_method": "github_app",
                            "default_branch": "main", "branch_protection": True},
             "layout": {"x": 310, "y": y + 180}, "provenance": {"kind": "drawn", "source": "Architecture inventory"}},
            _service(prefix, "backend", api, {"kind": "api", "runtime": "Python 3.12", "exposure": "private"}, 310, y),
            {"id": f"{prefix}_db", "type": "data.aurora-postgres@1", "name": database,
             "environment": ENVIRONMENT, "owner": "Platform engineering",
             "properties": {"cluster_identifier": f"{prefix}_db", "region": "ap-south-1", "engine": "aurora-postgresql",
                            "storage_encrypted": True, "publicly_accessible": False},
             "layout": {"x": 620, "y": y}, "provenance": {"kind": "drawn", "source": "Architecture inventory"}},
            _resource(prefix, "jobs", "messaging.sqs@1", f"{api} job queue",
                      {"queue_name": f"{prefix}-jobs", "region": "ap-south-1",
                       "encryption": "sse_sqs", "retention_seconds": 345600, "fifo": False}, 620, y + 190),
            _service(prefix, "worker", f"{api} background worker",
                     {"kind": "worker", "runtime": "Python 3.12", "exposure": "private"}, 310, y + 365),
            _resource(prefix, "archive", "data.s3@1", f"{api} export archive",
                      {"bucket_name": f"acme-{key}-exports", "region": "ap-south-1", "public_access_block": True,
                       "encryption": "sse_kms", "versioning": True, "lifecycle_days": 90}, 620, y + 365),
            _resource(prefix, "logs", "observability.cloudwatch@1", f"{api} application logs",
                      {"log_groups": [f"/acme/{key}/application"], "region": "ap-south-1",
                       "signals": ["application", "authorization"], "retention_days": 90,
                       "read_method": "filter_log_events"}, 930, y + 190),
            _resource(prefix, "dns", "edge.route53@1", f"{client} DNS",
                      {"zone_name": f"{key}.acme.example", "private": False, "dnssec": True,
                       "query_logging": True}, 0, y),
            _resource(prefix, "cdn", "edge.cloudfront@1", f"{client} content delivery",
                      {"distribution_id": f"{prefix}-cdn", "viewer_protocol": "redirect_to_https",
                       "waf": True, "logging": True}, 0, y),
            _resource(prefix, "balancer", "edge.alb@1", f"{api} load balancer",
                      {"load_balancer_name": f"{key}-alb", "region": "ap-south-1",
                       "scheme": "internal", "https_listener_only": True, "access_logs": True}, 0, y),
            _resource(prefix, "runtime", "compute.vm@1", f"{api} application host",
                      {"cloud": "aws", "size": "t3.medium", "os": "Amazon Linux 2023", "region": "ap-south-1",
                       "public_ip": False, "password_auth": False, "disk_encryption": True}, 0, y),
            _resource(prefix, "role", "identity.iam-role@1", f"{api} runtime role",
                      {"role_name": f"{key}-runtime", "trusted_by": ["ec2.amazonaws.com"],
                       "permission_boundary": "AcmeWorkloadBoundary"}, 0, y),
            _resource(prefix, "cache", "data.elasticache@1", f"{api} cache",
                      {"replication_group_id": f"{key}-cache", "region": "ap-south-1", "transit_encryption": True,
                       "at_rest_encryption": True, "auth_enabled": True}, 0, y),
            _resource(prefix, "secrets", "identity.secrets-manager@1", f"{api} secrets store",
                      {"region": "ap-south-1", "rotation_enabled": True, "kms_key": "alias/acme-application"}, 0, y),
        ])
        for node in doc["nodes"][first:]:
            column, row = POSITIONS[node["id"].removeprefix(prefix + "_")]
            node["layout"] = {"x": column * 310, "y": index * 800 + row * 190}
        doc["edges"].extend([
            _edge(prefix, "frontend", "backend", "calls",
                  f"{client} requests application records from {api}.", "Authenticated HTTPS requests"),
            _edge(prefix, "gateway", "backend", "calls",
                  f"The gateway validates the caller token and forwards requests to {api}.", "HTTPS requests and caller identity"),
            _edge(prefix, "backend", "repo", "deploys_from",
                  "Approved builds of the API are produced from the repository's main branch.", "Versioned source and release metadata"),
            _edge(prefix, "backend", "db", "reads",
                  f"{api} retrieves records needed to answer authenticated requests.", "Authorized application records"),
            _edge(prefix, "backend", "db", "writes",
                  f"{api} saves accepted changes to {database}.", "Application record updates"),
            _edge(prefix, "backend", "jobs", "publishes_to",
                  "The API queues work that can finish after the user receives a response.", "Job identifiers and operation names"),
            _edge(prefix, "worker", "jobs", "consumes_from",
                  "The background worker reads queued jobs and acknowledges them after processing.", "Job identifiers and operation names"),
            _edge(prefix, "worker", "archive", "writes",
                  "The worker stores approved exports in encrypted, private archive storage.", "Export files and timestamped metadata"),
            _edge(prefix, "backend", "logs", "emits_telemetry_to",
                  "The API logs request outcomes and access decisions for investigation.", "Request metadata and access decisions"),
            _edge(prefix, "worker", "logs", "emits_telemetry_to",
                  "The worker logs job completion, errors and retries.", "Job status and error summaries"),
            _edge(prefix, "dns", "cdn", "depends_on",
                  "The hosted zone points the application domain to its distribution.", "DNS alias records"),
            _edge(prefix, "cdn", "frontend", "calls",
                  "The distribution serves the portal over HTTPS and fetches application content from the origin.",
                  "HTTPS page and static asset requests"),
            _edge(prefix, "cdn", "gateway", "routes_through",
                  "The distribution forwards API requests to the gateway for authentication.",
                  "HTTPS API requests"),
            _edge(prefix, "gateway", "balancer", "routes_through",
                  "The gateway forwards authenticated traffic through the private load balancer.",
                  "Authorized application requests"),
            _edge(prefix, "balancer", "backend", "calls",
                  "The load balancer routes requests to healthy API instances.",
                  "HTTPS requests and health probes"),
            _edge(prefix, "backend", "runtime", "runs_on",
                  "The API process runs on the private application host.", "API workload"),
            _edge(prefix, "worker", "runtime", "runs_on",
                  "The background worker runs on the same private application host.", "Background workload"),
            _edge(prefix, "runtime", "role", "runs_as",
                  "The application host obtains short-lived credentials through its instance role.",
                  "Scoped AWS identity"),
            _edge(prefix, "backend", "cache", "reads",
                  "The API reads cached read models to reduce database load.", "Short-lived response records"),
            _edge(prefix, "backend", "secrets", "reads",
                  "The API retrieves its connection credential at startup using the workload identity.",
                  "Credential references only"),
        ])
    if existing:
        saved = clones.read_clone(db, project.org_id, existing.id)
        known = {node["id"]: node for node in saved["nodes"]}
        links = {(edge["from"], edge["to"], edge["type"]): edge for edge in saved["edges"]}
        for node in doc["nodes"]:
            previous = known.get(node["id"])
            if not previous:
                saved["nodes"].append(node)
            elif previous.get("layout") in original_positions.get(node["id"], []):
                previous["layout"] = node["layout"]
        for edge in doc["edges"]:
            key = edge["from"], edge["to"], edge["type"]
            if key in links:
                links[key]["properties"] = {**edge["properties"], **links[key].get("properties", {})}
            else:
                saved["edges"].append(edge)
        doc = saved
    problems = clones.document_problems(doc, "acme")
    if problems:
        raise ValueError(f"{slug}: {problems}")
    if existing:
        clones.save_version(db, project.org_id, existing.id, doc, "Platform engineering", INVENTORY_REVISION)
    else:
        clones.create_clone(db, project.org_id, doc, "Platform engineering", INVENTORY_REVISION, project_id=project.id)
