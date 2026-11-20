"""Which node library type, and which of its properties, each Terraform resource becomes.

Every type and property named here exists in library/types/, so an imported clone
resolves against the library (test_terraform_state checks this). A resource with no
entry becomes the Generic services type, which says what it was.

The `arn` property is where a later import finds a thing again: the clone matches a discovered or scanned resource to
an existing node on its ARN only (changesets.align and changesets.enrich). So every type that is a single AWS resource
with an ARN carries the `arn` attribute the state holds, exactly as the provider documents it, and never one built
from parts. A resource whose state leaves `arn` out simply gets no `arn` property.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

GENERIC = "generic.services@1"


def _lower(value: object) -> object:
    return value.lower() if isinstance(value, str) else value


def _as_list(value: object) -> object:
    return [value] if isinstance(value, str) else value


def _scheme(internal: object) -> object:
    return "internal" if internal is True else "internet_facing" if internal is False else None


def _rds_engine(engine: object) -> object:
    """Terraform names editions (sqlserver-ex, oracle-ee); the library keeps the engine family."""
    if not isinstance(engine, str):
        return None
    for family in ("sqlserver", "oracle"):
        if engine.startswith(family):
            return family
    return engine


def _ssm_type(value: object) -> object:
    return {"String": "string", "StringList": "string_list", "SecureString": "secure_string"}.get(value) if isinstance(value, str) else None


def _flow_destination(value: object) -> object:
    names = {"cloud-watch-logs": "cloudwatch_logs", "s3": "s3", "kinesis-data-firehose": "firehose"}
    return names.get(value) if isinstance(value, str) else None


# Resources that only configure another resource (its policy, its encryption, who may invoke it).
# They are not drawn as nodes of their own: the library has no type for them, and a Generic node for
# each would bury the estate. The import says how many of each it read and did not draw.
SETTINGS = frozenset({
    "aws_iam_role_policy", "aws_iam_role_policy_attachment", "aws_iam_policy_attachment",
    "aws_s3_bucket_public_access_block", "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_policy", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_lifecycle_configuration",
    "aws_s3_bucket_versioning", "aws_s3_bucket_logging", "aws_s3_object",
    "aws_lambda_permission", "aws_lambda_alias", "aws_sqs_queue_policy",
    "aws_apigatewayv2_stage", "aws_apigatewayv2_route",
    "aws_cloudwatch_event_target", "aws_ecr_repository_policy", "aws_ecr_lifecycle_policy",
    "aws_signer_signing_job", "aws_kms_alias",
})


@dataclass(frozen=True)
class Mapping:
    """One Terraform resource type: its library type, its group kind (boundaries only) and its properties."""

    type_ref: str
    group_kind: str = ""
    # (Terraform attribute, library property, conversion)
    fields: tuple[tuple[str, str, Callable[[object], object] | None], ...] = ()
    fixed: dict[str, object] = field(default_factory=dict)
    region_key: str = ""


MAPPINGS: dict[str, Mapping] = {
    "aws_vpc": Mapping("boundary.vpc@1", "vpc", (("cidr_block", "cidr", _as_list),), {"cloud": "aws"}, "region"),
    "aws_subnet": Mapping("boundary.subnet@1", "subnet", (("cidr_block", "cidr", _as_list),
                                                          ("availability_zone", "availability_zone", None))),
    "aws_security_group": Mapping("boundary.security-group@1", "security-group", (("name", "group_name", None),),
                                  {"cloud": "aws"}),
    "azurerm_resource_group": Mapping("boundary.resource-group@1", "resource-group",
                                      (("name", "name", None), ("location", "location", None))),
    "azurerm_virtual_network": Mapping("boundary.vpc@1", "vpc", (("address_space", "cidr", _as_list),
                                                                 ("location", "region", None)), {"cloud": "azure"}),
    "azurerm_subnet": Mapping("boundary.subnet@1", "subnet", (("address_prefixes", "cidr", _as_list),)),
    "azurerm_network_security_group": Mapping("boundary.security-group@1", "security-group",
                                              (("name", "group_name", None),), {"cloud": "azure"}),
    "aws_lambda_function": Mapping("service.function@2", fields=(
        ("runtime", "runtime", None), ("memory_size", "memory_mb", None), ("package_type", "package_type", _lower),
        ("arn", "arn", None),
    ), fixed={"platform": "aws_lambda"}, region_key="region"),
    "azurerm_linux_function_app": Mapping("service.function@2", fixed={"platform": "azure_functions"}, region_key="region"),
    "azurerm_windows_function_app": Mapping("service.function@2", fixed={"platform": "azure_functions"}, region_key="region"),
    "azurerm_function_app": Mapping("service.function@2", fixed={"platform": "azure_functions"}, region_key="region"),
    "aws_ecs_service": Mapping("service.worker@1"),
    "aws_instance": Mapping("compute.vm@1", fields=(("instance_type", "size", None),), fixed={"cloud": "aws"},
                            region_key="region"),
    "azurerm_linux_virtual_machine": Mapping("compute.vm@1", fields=(("size", "size", None), ("location", "region", None)),
                                             fixed={"cloud": "azure", "os": "linux"}),
    "azurerm_windows_virtual_machine": Mapping("compute.vm@1", fields=(("size", "size", None), ("location", "region", None)),
                                               fixed={"cloud": "azure", "os": "windows"}),
    "aws_iam_role": Mapping("identity.iam-role@1", fields=(("name", "role_name", None), ("arn", "arn", None))),
    "aws_s3_bucket": Mapping("data.s3@1", fields=(("bucket", "bucket_name", None), ("arn", "arn", None)),
                                region_key="region"),
    "aws_dynamodb_table": Mapping("data.dynamodb@1", fields=(("name", "table_name", None), ("arn", "arn", None)),
                                  region_key="region"),
    "aws_db_instance": Mapping("data.rds@1", fields=(
        ("identifier", "db_instance_identifier", None), ("engine", "engine", _rds_engine),
        ("instance_class", "instance_class", None), ("storage_encrypted", "storage_encrypted", None),
        ("publicly_accessible", "publicly_accessible", None), ("iam_database_authentication_enabled", "iam_auth", None),
        ("backup_retention_period", "backup_retention_days", None), ("arn", "arn", None),
    ), region_key="region"),
    "azurerm_storage_account": Mapping("data.blob@1", fields=(
        ("name", "account_name", None), ("allow_nested_items_to_be_public", "allow_blob_public_access", None),
        ("shared_access_key_enabled", "shared_key_access", None),
    )),
    "azurerm_mssql_database": Mapping("data.azure-sql@1", fields=(("name", "database_name", None),)),
    "aws_sqs_queue": Mapping("messaging.sqs@1", fields=(("name", "queue_name", None), ("fifo_queue", "fifo", None),
                                                        ("arn", "arn", None)), region_key="region"),
    "aws_apigatewayv2_api": Mapping("edge.api-gateway@1", fields=(("id", "api_id", None),
                                                                  ("protocol_type", "api_type", _lower),
                                                                  ("arn", "arn", None)), region_key="region"),
    "aws_lb": Mapping("edge.alb@1", fields=(("name", "load_balancer_name", None), ("internal", "scheme", _scheme),
                                            ("arn", "arn", None)), region_key="region"),
    "aws_ecr_repository": Mapping("delivery.ecr@1", fields=(("name", "repository_name", None), ("arn", "arn", None)),
                                  region_key="region"),
    "aws_cloudwatch_log_group": Mapping("observability.cloudwatch@1", fields=(
        ("name", "log_groups", _as_list), ("retention_in_days", "retention_days", None), ("arn", "arn", None),
    ), fixed={"signals": ["logs"]}, region_key="region"),
    "aws_kms_key": Mapping("identity.kms@1", fields=(("enable_key_rotation", "rotation_enabled", None),
                                                     ("arn", "arn", None)), region_key="region"),
    "aws_ssm_parameter": Mapping("identity.ssm-parameter-store@1", fields=(
        ("name", "parameter_path", None), ("type", "type", _ssm_type), ("key_id", "kms_key", None), ("arn", "arn", None),
    ), region_key="region"),
    "aws_cloudwatch_event_rule": Mapping("messaging.eventbridge@1", fields=(
        ("event_bus_name", "bus_name", None), ("arn", "arn", None)), region_key="region"),
    "aws_flow_log": Mapping("observability.vpc-flow-logs@1", fields=(
        ("id", "flow_log_id", None), ("log_destination_type", "destination", _flow_destination),
        ("traffic_type", "traffic_type", _lower),
    )),
    "aws_cloudtrail": Mapping("observability.cloudtrail@1", fields=(
        ("name", "trail_name", None), ("is_multi_region_trail", "multi_region", None),
        ("enable_log_file_validation", "log_file_validation", None), ("s3_bucket_name", "destination_bucket", None),
        ("arn", "arn", None),
    )),
}


def mapping_for(resource_type: str, attributes: dict) -> Mapping | None:
    """The mapping for one resource, or None when the library has no type for it."""
    if resource_type == "aws_lb" and attributes.get("load_balancer_type") not in (None, "application"):
        return None  # Network and gateway load balancers have no library type yet.
    return MAPPINGS.get(resource_type)


# A resource the state gives no name to is called by its Terraform label and what it is, the way a person would say it.
_NAMELESS = {"aws_kms_key": "{label} key"}


def nameless_name(resource_type: str, label: str) -> str:
    """A name for a resource with none of its own, or "" when its Terraform address has to stand in."""
    pattern = _NAMELESS.get(resource_type)
    return pattern.format(label=label) if pattern else ""
