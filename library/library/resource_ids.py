"""Recognise a pasted cloud resource id and name the library types it can be.

AWS ARNs (`arn:aws:s3:::bucket`) and Azure resource ids
(`/subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<name>`).
The parts that say where the resource lives come back too, for pre-filling a node.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# AWS service -> type, or resource prefix -> type when one service holds several kinds.
AWS_SERVICES: dict[str, str | dict[str, str]] = {
    "s3": "data.s3",
    "dynamodb": "data.dynamodb",
    "rds": {"cluster:": "data.aurora-postgres", "db:": "data.rds"},
    "elasticache": "data.elasticache",
    "redshift": "data.redshift",
    "lambda": "service.function",
    "sqs": "messaging.sqs",
    "sns": "messaging.sns",
    "events": "messaging.eventbridge",
    "kafka": "messaging.msk",
    "iam": {"role/": "identity.iam-role"},
    "kms": "identity.kms",
    "secretsmanager": "identity.secrets-manager",
    "ssm": {"parameter/": "identity.ssm-parameter-store"},
    "cognito-idp": "identity.cognito",
    "ecr": "delivery.ecr",
    "eks": "boundary.eks",
    "ec2": {"vpc/": "boundary.vpc", "subnet/": "boundary.subnet", "instance/": "compute.vm"},
    "organizations": "boundary.aws-organization",
    "elasticloadbalancing": {"loadbalancer/app/": "edge.alb"},
    "cloudfront": "edge.cloudfront",
    "apigateway": "edge.api-gateway",
    "route53": "edge.route53",
    "logs": "observability.cloudwatch",
    "cloudtrail": "observability.cloudtrail",
    "guardduty": "control.guardduty",
    "budgets": "control.aws-budgets",
    "bedrock": "ai.bedrock",
}

# Azure resource type, lower case -> types. App Service sites can be web apps or function apps.
AZURE_TYPES: dict[str, tuple[str, ...]] = {
    "microsoft.storage/storageaccounts": ("data.blob",),
    "microsoft.keyvault/vaults": ("identity.key-vault",),
    "microsoft.sql/servers": ("data.azure-sql",),
    "microsoft.sql/servers/databases": ("data.azure-sql",),
    "microsoft.documentdb/databaseaccounts": ("data.cosmos",),
    "microsoft.cache/redis": ("data.redis",),
    "microsoft.search/searchservices": ("data.search",),
    "microsoft.healthcareapis/workspaces/fhirservices": ("data.fhir",),
    "microsoft.web/sites": ("service.function", "service.frontend"),
    "microsoft.containerservice/managedclusters": ("boundary.aks",),
    "microsoft.containerregistry/registries": ("delivery.acr",),
    "microsoft.servicebus/namespaces": ("messaging.service-bus",),
    "microsoft.eventhub/namespaces": ("messaging.event-hubs",),
    "microsoft.eventgrid/topics": ("messaging.event-grid",),
    "microsoft.network/virtualnetworks": ("boundary.vpc",),
    "microsoft.network/virtualnetworks/subnets": ("boundary.subnet",),
    "microsoft.network/dnszones": ("edge.azure-dns",),
    "microsoft.network/frontdoors": ("edge.front-door",),
    "microsoft.cdn/profiles": ("edge.front-door",),
    "microsoft.apimanagement/service": ("edge.apim",),
    "microsoft.cognitiveservices/accounts": ("ai.azure-openai",),
    "microsoft.insights/components": ("observability.app-insights",),
    "microsoft.operationalinsights/workspaces": ("observability.log-analytics",),
    "microsoft.managedidentity/userassignedidentities": ("identity.managed-identity",),
}


@dataclass(frozen=True)
class ResourceMatch:
    type_ids: tuple[str, ...]
    kind: str  # "AWS ARN" | "Azure resource id"
    where: dict[str, str] = field(default_factory=dict)  # region, account, subscription, resource group, name


def recognise(text: str) -> ResourceMatch | None:
    """The types a pasted resource id can be, or None when the text is not one."""
    text = text.strip()
    if text.startswith("arn:"):
        return _aws(text)
    if text.lower().startswith("/subscriptions/"):
        return _azure(text)
    return None


def _aws(arn: str) -> ResourceMatch | None:
    parts = arn.split(":", 5)
    if len(parts) < 6 or not parts[1].startswith("aws"):
        return None
    _, _, service, region, account, resource = parts
    known = AWS_SERVICES.get(service)
    if isinstance(known, dict):
        known = next((type_id for prefix, type_id in known.items() if resource.startswith(prefix)), None)
    if known is None:
        return None
    where = {k: v for k, v in (("region", region), ("account", account)) if v}
    where["name"] = resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return ResourceMatch((known,), "AWS ARN", where)


def _azure(resource_id: str) -> ResourceMatch | None:
    segments = [s for s in resource_id.split("/") if s]
    lower = [s.lower() for s in segments]
    where = _azure_scope(segments, lower)
    if "providers" not in lower:
        if "resource_group" in where:
            return ResourceMatch(("boundary.resource-group",), "Azure resource id", where)
        return ResourceMatch(("boundary.subscription",), "Azure resource id", where)
    after = lower.index("providers") + 1
    namespace, rest = lower[after], lower[after + 1:]
    kinds = rest[0::2]  # type, name, child type, child name, ...
    names = segments[after + 2::2]
    for depth in range(len(kinds), 0, -1):  # the most specific known type wins
        type_ids = AZURE_TYPES.get("/".join([namespace, *kinds[:depth]]))
        if type_ids:
            if len(names) >= depth:
                where["name"] = names[depth - 1]
            return ResourceMatch(type_ids, "Azure resource id", where)
    return None


def _azure_scope(segments: list[str], lower: list[str]) -> dict[str, str]:
    where = {}
    for key, label in (("subscriptions", "subscription"), ("resourcegroups", "resource_group")):
        if key in lower and lower.index(key) + 1 < len(segments):
            where[label] = segments[lower.index(key) + 1]
    return where
