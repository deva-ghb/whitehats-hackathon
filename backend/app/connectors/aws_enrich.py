"""Describe calls that add a tagged AWS resource's settings to its clone node.

The tag search gives only ARNs and tags. These calls read configuration, never data, and each runs
in the resource's own region. A resource the tag index still lists but AWS no longer has is kept
with what its ARN says, rather than stopping the whole discovery.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .aws_resources import kind_of, queue_url, split_arn
from .base import SourceError

MAX_PAGES = 20
ACCOUNT_RATE_LIMIT = 10_000  # requests a second, what API Gateway allows an account in a region by default  # a list that is still paging after this has run away; fail instead of looping

Call = Callable[..., dict]  # the connector's guarded call: (service, method, *, region, **params) -> answer


def _denied(error: SourceError) -> bool:
    text = str(error).lower()
    return "not authorized" in text or "accessdenied" in text


def _gone(error: SourceError) -> bool:
    """AWS no longer has the resource: InvalidInstanceID.NotFound, DBInstanceNotFound and the like."""
    text = str(error).lower()
    return "notfound" in text and "not authorized" not in text and "accessdenied" not in text


class Enricher:
    """Reads the settings of each tagged resource. One per discovery, so the Lambda list is read once per region."""

    def __init__(self, call: Call, page_size: int):
        self._call = call
        self._page_size = page_size
        self._functions: dict[str, dict[str, dict]] = {}

    def describe(self, arn: str) -> dict:
        """What the describe calls add for this ARN; {} for a service that has none."""
        service, region, _owner, resource = split_arn(arn)
        reader = _READERS.get((service, kind_of(resource))) or _READERS.get((service, ""))
        if reader is None:
            return {}
        try:
            return reader(self, arn, region, resource)
        except SourceError as error:
            if _gone(error):
                return {}
            if _denied(error):
                # The role lost this permission. The node is still listed from its tag, and the missing
                # permission is shown on it from the connection check (CN.5), so discovery goes on.
                return {}
            raise

    def _bucket(self, arn: str, _region: str, _resource: str) -> dict:
        answer = self._call("s3", "get_bucket_location", Bucket=arn.split(":::")[-1])
        return {"region": answer.get("LocationConstraint") or "us-east-1"}

    def _lambda(self, arn: str, region: str, _resource: str) -> dict:
        function = self._functions_in(region).get(arn, {})
        names = sorted((function.get("Environment") or {}).get("Variables") or {})
        package = (function.get("PackageType") or "").lower()
        extra: dict[str, Any] = {
            "runtime": function.get("Runtime") or "unknown",
            "environment_variables": names,  # names only; values can be secrets
            "execution_role": function.get("Role") or "",
        }
        if package in ("zip", "image"):
            extra["package_type"] = package
        if package == "image":
            # The list does not name the image. GetFunction does, with the digest Lambda resolved the tag to;
            # it is only called for image functions, so no zip function's code link is ever fetched.
            code = self._call("lambda", "get_function", region=region, FunctionName=arn).get("Code") or {}
            image = code.get("ResolvedImageUri") or code.get("ImageUri") or ""
            if image:
                extra["image"] = image
        if function.get("MemorySize") is not None:
            extra["memory_mb"] = function["MemorySize"]
        return extra

    def _functions_in(self, region: str) -> dict[str, dict]:
        if region in self._functions:
            return self._functions[region]
        found: dict[str, dict] = {}
        kwargs: dict[str, Any] = {"MaxItems": self._page_size}
        for _ in range(MAX_PAGES):
            page = self._call("lambda", "list_functions", region=region, **kwargs)
            for function in page.get("Functions") or []:
                found[function.get("FunctionArn") or ""] = function
            marker = page.get("NextMarker")
            if not marker:
                self._functions[region] = found
                return found
            kwargs["Marker"] = marker
        raise SourceError(f"Lambda list in {region} still had another page after {MAX_PAGES} pages.")

    def _table(self, arn: str, region: str, _resource: str) -> dict:
        table = self._call("dynamodb", "describe_table", region=region, TableName=arn.split("/")[-1]).get("Table") or {}
        sse = table.get("SSEDescription") or {}
        if sse.get("SSEType") == "KMS":
            return {"encryption": "customer_managed" if sse.get("KMSMasterKeyArn") else "aws_managed"}
        if sse.get("SSEType") == "AES256":
            return {"encryption": "aws_managed"}
        return {}

    def _queue(self, arn: str, region: str, _resource: str) -> dict:
        attrs = self._call("sqs", "get_queue_attributes", region=region, QueueUrl=queue_url(arn),
                           AttributeNames=["All"]).get("Attributes") or {}
        if attrs.get("KmsMasterKeyId"):
            return {"encryption": "sse_kms"}
        if attrs.get("SqsManagedSseEnabled") == "true":
            return {"encryption": "sse_sqs"}
        return {"encryption": "none"}

    def _role(self, arn: str, _region: str, _resource: str) -> dict:
        name = arn.split("/")[-1]
        role = self._call("iam", "get_role", RoleName=name).get("Role") or {}
        inline = self._call("iam", "list_role_policies", RoleName=name).get("PolicyNames") or []
        attached = self._call("iam", "list_attached_role_policies", RoleName=name).get("AttachedPolicies") or []
        for policy in inline:
            self._call("iam", "get_role_policy", RoleName=name, PolicyName=policy)
        names = list(inline) + [item.get("PolicyName", "") for item in attached if item.get("PolicyName")]
        return {"trusted_by": _principals(role.get("AssumeRolePolicyDocument")), "attached_policies": names}

    def _instance(self, arn: str, region: str, resource: str) -> dict:
        answer = self._call("ec2", "describe_instances", region=region, InstanceIds=[resource.split("/")[-1]])
        found = [item for reservation in answer.get("Reservations") or [] for item in reservation.get("Instances") or []]
        if not found:
            return {}
        instance = found[0]
        _service, _region, owner, _resource = split_arn(arn)
        extra: dict[str, Any] = {"size": instance.get("InstanceType") or "unknown",
                                 "public_ip": bool(instance.get("PublicIpAddress"))}
        if instance.get("PlatformDetails"):
            extra["os"] = instance["PlatformDetails"]
        if (instance.get("IamInstanceProfile") or {}).get("Arn"):
            extra["instance_profile"] = instance["IamInstanceProfile"]["Arn"]
        if instance.get("VpcId"):
            extra["vpc_arn"] = f"arn:aws:ec2:{region}:{owner}:vpc/{instance['VpcId']}"
        return extra

    def _vpc(self, _arn: str, region: str, resource: str) -> dict:
        answer = self._call("ec2", "describe_vpcs", region=region, VpcIds=[resource.split("/")[-1]])
        vpcs = answer.get("Vpcs") or []
        if not vpcs:
            return {}
        blocks = [item.get("CidrBlock") for item in vpcs[0].get("CidrBlockAssociationSet") or []]
        return {"cidr": [block for block in blocks if block] or [vpcs[0].get("CidrBlock") or ""]}

    def _database(self, _arn: str, region: str, resource: str) -> dict:
        answer = self._call("rds", "describe_db_instances", region=region,
                            DBInstanceIdentifier=resource.split(":", 1)[-1])
        instances = answer.get("DBInstances") or []
        if not instances:
            return {}
        db = instances[0]
        extra = _database_settings(db)
        extra["instance_class"] = db.get("DBInstanceClass") or ""
        extra["publicly_accessible"] = bool(db.get("PubliclyAccessible"))
        if db.get("DBClusterIdentifier"):
            extra["cluster"] = db["DBClusterIdentifier"]
        return extra

    def _cluster(self, _arn: str, region: str, resource: str) -> dict:
        answer = self._call("rds", "describe_db_clusters", region=region,
                            DBClusterIdentifier=resource.split(":", 1)[-1])
        clusters = answer.get("DBClusters") or []
        if not clusters:
            return {}
        extra = _database_settings(clusters[0])
        if clusters[0].get("PubliclyAccessible") is not None:
            extra["publicly_accessible"] = bool(clusters[0]["PubliclyAccessible"])
        return extra

    def _api(self, _arn: str, region: str, resource: str) -> dict:
        """An HTTP API: whether the internet can call it, and the rate limit on its stages (the lowest one set)."""
        parts = resource.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "apis":
            # REST and WebSocket APIs keep their limits elsewhere (not read yet); a stage, tagged on its own, is
            # read with its API, so it is not read again as an API of its own.
            return {}
        api_id = parts[1]
        api = self._call("apigatewayv2", "get_api", region=region, ApiId=api_id)
        stages = self._call("apigatewayv2", "get_stages", region=region, ApiId=api_id).get("Items") or []
        limits = [stage.get("DefaultRouteSettings") or {} for stage in stages]
        rates = [item["ThrottlingRateLimit"] for item in limits if item.get("ThrottlingRateLimit")]
        bursts = [item["ThrottlingBurstLimit"] for item in limits if item.get("ThrottlingBurstLimit")]
        # A stage with no limit of its own takes the account's, and a limit at the account's does nothing: none.
        limited = [rate for rate in rates if rate < ACCOUNT_RATE_LIMIT]
        extra: dict[str, Any] = {"display_name": api.get("Name") or api_id,
                                 "public": not api.get("DisableExecuteApiEndpoint", False),
                                 "rate_limit": "set" if stages and len(limited) == len(stages) else "none"}
        if rates:
            extra["throttling_rps"] = max(rates)  # the loosest stage is the one a flood goes through
        if bursts:
            extra["throttling_burst"] = max(bursts)
        return extra


# (service, resource kind) -> the reader. An empty kind means every resource of that service.
_READERS: dict[tuple[str, str], Callable[[Enricher, str, str, str], dict]] = {
    ("s3", ""): Enricher._bucket,
    ("lambda", "function"): Enricher._lambda,
    ("dynamodb", "table"): Enricher._table,
    ("sqs", ""): Enricher._queue,
    ("iam", "role"): Enricher._role,
    ("ec2", "instance"): Enricher._instance,
    ("ec2", "vpc"): Enricher._vpc,
    ("rds", "db"): Enricher._database,
    ("rds", "cluster"): Enricher._cluster,
    ("apigateway", ""): Enricher._api,
}


def _database_settings(item: dict) -> dict:
    """The settings an RDS instance and an Aurora cluster share. The engine name is AWS's own."""
    return {"engine": item.get("Engine") or "",
            "storage_encrypted": bool(item.get("StorageEncrypted")),
            "iam_auth": bool(item.get("IAMDatabaseAuthenticationEnabled")),
            "backup_retention_days": int(item.get("BackupRetentionPeriod") or 0)}


def _principals(document: object) -> list[str]:
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError:
            return []
    if not isinstance(document, dict):
        return []
    found = []
    for statement in document.get("Statement") or []:
        principal = statement.get("Principal")
        if isinstance(principal, str):
            found.append(principal)
        elif isinstance(principal, dict):
            for value in principal.values():
                found.extend(value if isinstance(value, list) else [value])
    return [item for item in found if isinstance(item, str)]
