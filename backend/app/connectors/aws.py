"""AWS inventory, CloudTrail and CloudWatch, read through a role the customer created.

The connector assumes that role with an external id. It never holds a long-lived key,
and it refuses to read S3 objects or DynamoDB items even when the role would allow it.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .aws_enrich import Enricher
from .aws_guard import blocked
from .aws_resources import assemble
from .base import Batch, ConnectionTest, Discovery, Permission, Probe, Source, SourceError
from .cloudwatch import ClientFactory, boto_clients
from .retry import Pacer, aws_wait_hint, with_retries

log = logging.getLogger("whitehats.connectors.aws")

FLOW_EVERY_S = 900  # where each network's traffic log goes is read again this often (NET.2)
LOOKBACK_S = 3600  # a source with no bookmark yet starts an hour back
# AWS adds an action to the account's activity history minutes after it happened, and not in order: an action can
# arrive after a later one was already read. Each read of the history goes back this far before its bookmark and skips
# the actions it has already seen (by event id), so a late arrival is read, once.
TRAIL_OVERLAP_S = 900
MAX_PAGES = 20  # a read that is still paging after this has run away; fail instead of looping
TRAIL_PAGE_MAX = 50  # LookupEvents rejects a page larger than this
CALLS_PER_SECOND = 5
TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "docs" / "connectors" / "aws-readonly-role.yaml"


def _aws_errors() -> tuple[type[Exception], ...]:
    from botocore.exceptions import BotoCoreError, ClientError

    return BotoCoreError, ClientError


def _epoch(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _denied(text: str) -> bool:
    lowered = text.lower()
    return "not authorized" in lowered or "accessdenied" in lowered or "explicit deny" in lowered


def _missing(text: str) -> bool:
    lowered = text.lower()
    return "nosuch" in lowered or "notfound" in lowered or "not exist" in lowered or "does not exist" in lowered


GLOBAL_REGION = "us-east-1"  # where AWS records the changes of its global services
GLOBAL_SOURCES = frozenset({"iam.amazonaws.com", "sts.amazonaws.com", "signin.amazonaws.com",
                            "organizations.amazonaws.com", "sso.amazonaws.com", "cloudfront.amazonaws.com",
                            "route53.amazonaws.com"})

class Aws(Source):
    """One customer's AWS account, read from the Runner by assuming their role."""

    key = "aws"
    permissions = (
        Permission("sts:AssumeRole", "sign in as the read-only role the customer created for WhiteHats", covers=("*",)),
        Permission("tag:GetResources", "list the resources in scope from their tags", covers=("*",)),
        Permission("iam:GetRole", "read IAM roles and the names of the policies on them",
                   covers=("identity.iam-role",)),
        Permission("lambda:ListFunctions", "list Lambda functions and their configuration", covers=("service.function",)),
        Permission("lambda:GetFunction", "read which container image a Lambda function runs, with its digest",
                   covers=("service.function",)),
        Permission("ecr:GetAuthorizationToken", "pull a function's container image to scan it for known weaknesses",
                   covers=("service.function",)),
        Permission("apigateway:GET", "list API Gateway APIs", covers=("edge.api-gateway",)),
        Permission("dynamodb:ListTables", "list DynamoDB tables; table items are never read", covers=("data.dynamodb",)),
        Permission("s3:GetBucketLocation", "read where an S3 bucket lives; objects are never read", covers=("data.s3",)),
        Permission("sqs:ListQueues", "list SQS queues; messages are never read", covers=("messaging.sqs",)),
        Permission("ec2:DescribeInstances", "read EC2 instance settings: size, address, instance profile",
                   covers=("compute.vm",)),
        Permission("ec2:DescribeVpcs", "read the address ranges of VPCs", covers=("boundary.vpc",)),
        Permission("ec2:DescribeSecurityGroups", "read firewall rules, incoming and outgoing",
                   covers=("boundary.security-group",)),
        Permission("ec2:DescribeNetworkInterfaces", "see which firewall rules are in use",
                   covers=("boundary.security-group",)),
        Permission("ec2:DescribeFlowLogs", "see where each network's traffic log goes",
                   covers=("observability.vpc-flow-logs",)),
        Permission("rds:DescribeDBInstances", "read RDS database settings; databases are never opened",
                   covers=("data.rds", "data.aurora-postgres")),
        Permission("rds:DescribeDBClusters", "read Aurora cluster settings; databases are never opened",
                   covers=("data.aurora-postgres",)),
        Permission("cloudtrail:LookupEvents", "read new CloudTrail events since the last bookmark",
                   covers=("observability.cloudtrail",)),
        Permission("logs:FilterLogEvents", "read new CloudWatch log events since the last bookmark",
                   covers=("observability.cloudwatch",)),
    )
    page_size = TRAIL_PAGE_MAX

    def __init__(self, config: dict[str, str], secret: Callable[[], str] = lambda: "", *,
                 clients: ClientFactory = boto_clients, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(config, secret)
        self._clients = clients
        self._clock = clock
        self._sleep = sleep
        self._pacer = Pacer(1 / CALLS_PER_SECOND, sleep=self._sleep)
        self._cache: dict[tuple[str, str], Any] = {}
        self._credentials: dict | None = None
        self._identity = ""
        self._mappings: list[dict] | None = None
        self._touched_logs = False

    def test(self) -> ConnectionTest:
        identity = self._session()
        sample, read_error = self._sample()
        probes = (
            Probe("sts:AssumeRole", True, f"Signed in as {identity}."),
            self._probe("tag:GetResources", self._load_tags),
            self._probe("iam:GetRole", self._probe_role),
            self._probe("lambda:ListFunctions", self._probe_functions),
            # A name no function has: "not found" means the call itself is allowed.
            self._probe("lambda:GetFunction",
                        lambda: self._call("lambda", "get_function", FunctionName="whitehats-permission-probe")),
            self._probe("ecr:GetAuthorizationToken", lambda: self._call("ecr", "get_authorization_token")),
            self._probe("apigateway:GET", self._probe_apis),
            self._probe("dynamodb:ListTables", self._probe_tables),
            self._probe("s3:GetBucketLocation", self._probe_bucket),
            self._probe("sqs:ListQueues", self._probe_queues),
            self._probe("ec2:DescribeInstances", lambda: self._call("ec2", "describe_instances", MaxResults=5)),
            self._probe("ec2:DescribeVpcs", lambda: self._call("ec2", "describe_vpcs", MaxResults=5)),
            self._probe("ec2:DescribeSecurityGroups",
                        lambda: self._call("ec2", "describe_security_groups", MaxResults=5)),
            self._probe("ec2:DescribeNetworkInterfaces",
                        lambda: self._call("ec2", "describe_network_interfaces", MaxResults=5)),
            self._probe("ec2:DescribeFlowLogs", lambda: self._call("ec2", "describe_flow_logs", MaxResults=5)),
            self._probe("rds:DescribeDBInstances", lambda: self._call("rds", "describe_db_instances", MaxRecords=20)),
            self._probe("rds:DescribeDBClusters", lambda: self._call("rds", "describe_db_clusters", MaxRecords=20)),
            self._read_probe("cloudtrail:LookupEvents", sample, read_error, "lookup_events"),
            self._logs_probe(sample, read_error),
        )
        return ConnectionTest(identity, probes, sample)

    def discover(self) -> Discovery:
        account = self._session().split(":")[4]
        mappings = self._load_tags()
        enricher = Enricher(self._call, self.page_size)
        extras = {item["ResourceARN"]: enricher.describe(item["ResourceARN"]) for item in mappings}
        return assemble(account, self._region(), mappings, extras)

    def registry_login(self, image: str) -> tuple[str, str, str]:
        """(registry, user, password) to pull `image` from the account's ECR, a token that lasts 12 hours (VS.5c).

        The image's own registry and region are used, so an image in another region of the account is pulled too."""
        registry = image.split("/", 1)[0]
        parts = registry.split(".")
        if len(parts) < 6 or parts[1:3] != ["dkr", "ecr"] or not parts[0].isdigit():
            raise SourceError(f"{registry} is not an ECR registry, so WhiteHats has no sign-in for it.")
        answer = self._call("ecr", "get_authorization_token", region=parts[3], registryIds=[parts[0]])
        data = (answer.get("authorizationData") or [{}])[0]
        user, _, password = base64.b64decode(data.get("authorizationToken") or "").decode().partition(":")
        if not password:
            raise SourceError(f"ECR gave no pull token for {registry}.")
        return registry, user, password

    def read(self, bookmark: dict, limit: int | None = None) -> Batch:
        self._touched_logs = False
        lines: list[dict] = []
        trail = dict(bookmark.get("cloudtrail") or {})
        seen = {region: dict(ids) for region, ids in (bookmark.get("cloudtrail_seen") or {}).items()}
        logs = dict(bookmark.get("logs") or {})
        regions = self._regions()
        # IAM, sign-in and the other global services record their changes in us-east-1 only, whatever the home
        # region: read that region too, for those services alone, or a new admin user would never be seen.
        reads = [(region, None) for region in regions] + (
            [(GLOBAL_REGION, GLOBAL_SOURCES)] if GLOBAL_REGION not in regions else [])
        for region, only in reads:
            start = str(trail.get(region) or _iso(self._clock() - LOOKBACK_S))
            trail[region], seen[region] = self._read_trail(region, start, lines, limit, seen.get(region), only)
            if limit and len(lines) >= limit:
                break
        flow = dict(bookmark.get("flow") or {})
        if not limit and self._clock() - float(flow.get("at") or 0) >= FLOW_EVERY_S:
            try:
                flow = {"at": self._clock(), "networks": self._flow_logs()}
            except SourceError as exc:  # an older role: the activity log is still read, the rest says why not
                flow = {"at": self._clock(), "networks": list(flow.get("networks") or []), "error": str(exc)[:300]}
        self.flow_logs = list(flow.get("networks") or [])
        self.flow_error = str(flow.get("error") or "")
        if not (limit and len(lines) >= limit):
            # The log groups the customer named, then each network's traffic log that goes to the log service (NET.2).
            groups = [(group, "") for group in self._groups()] + [
                (f["group"], f["vpc"]) for f in self.flow_logs if f.get("destination") == "logs" and f.get("group")]
            for group, vpc in dict.fromkeys(groups):
                start = int(logs.get(group) or (self._clock() - LOOKBACK_S) * 1000)
                logs[group] = self._read_group(group, start, lines, limit, f"vpcflow:{vpc}" if vpc else "")
                if limit and len(lines) >= limit:
                    break
        kept = bookmark if limit else {"cloudtrail": trail, "cloudtrail_seen": seen, "logs": logs, "flow": flow}
        return Batch(lines[:limit] if limit else lines, kept)

    def _flow_logs(self) -> list[dict]:
        """Each network (VPC) in scope and where its traffic log goes: "logs" (the log service, which WhiteHats reads),
        "s3" (a bucket, whose files WhiteHats never reads), or "none". Read again every FLOW_EVERY_S."""
        tag = (self.config.get("tag_key"), self.config.get("tag_value"))
        out = []
        for region in self._regions():
            filters = [{"Name": f"tag:{tag[0]}", "Values": [tag[1]]}] if all(tag) else []
            vpcs = self._call("ec2", "describe_vpcs", region=region, **({"Filters": filters} if filters else {}))
            logs = self._call("ec2", "describe_flow_logs", region=region).get("FlowLogs") or []
            for vpc in vpcs.get("Vpcs") or []:
                vid = vpc["VpcId"]
                mine = [f for f in logs if f.get("ResourceId") == vid and f.get("FlowLogStatus") == "ACTIVE"]
                to_logs = next((f for f in mine if f.get("LogDestinationType") == "cloud-watch-logs"), None)
                name = next((t["Value"] for t in vpc.get("Tags") or [] if t.get("Key") == "Name"), vid)
                out.append({"vpc": vid, "name": name, "region": region,
                            "destination": "logs" if to_logs else ("s3" if mine else "none"),
                            "group": (to_logs or {}).get("LogGroupName", "")})
        return out

    def _sample(self) -> tuple[int, str]:
        try:
            return len(self.read({}, limit=5).lines), ""
        except SourceError as exc:
            return 0, str(exc)

    def _read_probe(self, name: str, sample: int, error: str, needle: str) -> Probe:
        if error and (needle in error or "filter_log_events" not in error):
            return Probe(name, False, error)
        return Probe(name, True, f"Read {sample} recent event(s).")

    def _logs_probe(self, sample: int, error: str) -> Probe:
        if error and "filter_log_events" in error:
            return Probe("logs:FilterLogEvents", False, error)
        if self._touched_logs:
            return Probe("logs:FilterLogEvents", True, f"Read {sample} recent event(s).")
        if error:
            return Probe("logs:FilterLogEvents", False, error)
        return self._probe("logs:FilterLogEvents", self._probe_logs)

    def _probe(self, name: str, call: Callable[[], object]) -> Probe:
        try:
            call()
        except SourceError as exc:
            text = str(exc)
            if _missing(text) and not _denied(text):
                return Probe(name, True, f"{name} is allowed.")
            return Probe(name, False, text)
        return Probe(name, True, f"{name} is allowed.")

    def credentials(self) -> dict:
        """The read-only role's short-lived credentials (AccessKeyId, SecretAccessKey, SessionToken), for a checker
        that runs in its own container (cloud_settings: Prowler). Signs in first if needed."""
        self._session()
        return dict(self._credentials)

    def _session(self) -> str:
        """The assumed role's identity. Signs in once."""
        if self._identity:
            return self._identity
        role = (self.config.get("role_arn") or "").strip()
        if not role:
            raise SourceError("AWS needs a read-only role. Long-lived access keys are not accepted.")
        external = (self.config.get("external_id") or "").strip()
        if not external:
            # Without it, anyone who learns the role ARN could have WhiteHats read that account for them.
            raise SourceError("AWS needs the external id the role was created with (the ExternalId in the "
                              "WhiteHats CloudFormation template).")
        params = {"RoleArn": role, "RoleSessionName": "whitehats-reader", "ExternalId": external}
        answer = self._call("sts", "assume_role", **params)
        self._credentials = answer["Credentials"]
        self._identity = answer["AssumedRoleUser"]["Arn"]
        return self._identity

    def _call(self, service: str, method: str, *, region: str = "", **kwargs: Any) -> dict:
        """One AWS call through the guard, in `region` (the home region when not given)."""
        reason = blocked(service, method)
        if reason:
            raise SourceError(reason)
        if service != "sts":
            self._session()
        client = self._cached(service, region or self._region())

        def attempt() -> dict:
            self._pacer.wait()
            return getattr(client, method)(**kwargs)

        try:
            return with_retries(attempt, aws_wait_hint, sleep=self._sleep)
        except _aws_errors() as exc:
            label = "AWS sign-in" if method == "assume_role" else f"{service}.{method}"
            where = f" in {region}" if region and region != self._region() else ""
            raise SourceError(f"{label}{where} failed: {self._public(exc)}") from exc

    def _cached(self, service: str, region: str) -> Any:
        key = (service, region)
        if key not in self._cache:
            creds = None if service == "sts" else self._credentials
            self._cache[key] = self._clients(service, region, creds)
        return self._cache[key]

    def _public(self, exc: Exception) -> str:
        text = str(exc)
        external = (self.config.get("external_id") or "").strip()
        if external:
            text = text.replace(external, "[external id]")
        return text

    def _region(self) -> str:
        return self.config.get("region") or "ap-southeast-2"

    def _regions(self) -> list[str]:
        """The home region first, then the other regions the customer named, each once."""
        named = [part.strip() for part in self.config.get("regions", "").split(",") if part.strip()]
        return list(dict.fromkeys([self._region(), *named]))

    def _groups(self) -> list[str]:
        return [part.strip() for part in self.config.get("log_groups", "").split(",") if part.strip()]

    def _load_tags(self) -> list[dict]:
        """Every tagged resource in scope, in every configured region. The tag search is regional."""
        if self._mappings is not None:
            return self._mappings
        found: dict[str, dict] = {}
        for region in self._regions():
            for item in self._tags_in(region):
                found.setdefault(item["ResourceARN"], item)  # a global resource can be listed in each region
        self._mappings = list(found.values())
        return self._mappings

    def _tags_in(self, region: str) -> list[dict]:
        kwargs: dict[str, Any] = {"ResourcesPerPage": self.page_size}
        if self.config.get("tag_key") and self.config.get("tag_value"):
            kwargs["TagFilters"] = [{"Key": self.config["tag_key"], "Values": [self.config["tag_value"]]}]
        found: list[dict] = []
        for _ in range(MAX_PAGES):
            page = self._call("resourcegroupstaggingapi", "get_resources", region=region, **kwargs)
            found.extend(page.get("ResourceTagMappingList") or [])
            token = page.get("PaginationToken")
            if not token:
                return found
            kwargs["PaginationToken"] = token
        raise SourceError(f"Tag search in {region} still had another page after {MAX_PAGES} pages.")

    def _probe_role(self) -> None:
        resource = self._identity.split(":")[5]
        name = resource.split("/")[1] if "/" in resource else resource
        self._call("iam", "get_role", RoleName=name)

    def _probe_functions(self) -> None:
        self._call("lambda", "list_functions", MaxItems=1)

    def _probe_apis(self) -> None:
        self._call("apigatewayv2", "get_apis", MaxResults="1")

    def _probe_tables(self) -> None:
        self._call("dynamodb", "list_tables", Limit=1)

    def _probe_bucket(self) -> None:
        buckets = [item["ResourceARN"].split(":::")[-1] for item in self._load_tags()
                   if item["ResourceARN"].startswith("arn:aws:s3:::")]
        self._call("s3", "get_bucket_location", Bucket=buckets[0] if buckets else "whitehats-probe-absent")

    def _probe_queues(self) -> None:
        self._call("sqs", "list_queues", MaxResults=1)

    def _probe_logs(self) -> None:
        group = self._groups()[0] if self._groups() else "whitehats-no-such-group"
        self._call("logs", "filter_log_events", logGroupName=group, limit=1)

    def _read_trail(self, region: str, start: str, lines: list[dict], limit: int | None,
                    seen: dict[str, float] | None, only: frozenset[str] | None = None) -> tuple[str, dict[str, float]]:
        """Read the activity history from TRAIL_OVERLAP_S before `start`, skipping event ids in `seen` (id -> event
        time). Returns the next bookmark and the ids still inside the overlap window, to skip next time. A bookmark
        from before the overlap existed (`seen` None) is read from `start` once, so nothing already read comes again."""
        from_ = _iso(_epoch(start) - TRAIL_OVERLAP_S) if seen is not None else start
        # Changes only: what WhiteHats raises and maps from is what changed the account. Reads (WhiteHats' own among
        # them) are most of a busy account's activity, and would bury a change under thousands of lookups.
        kwargs: dict[str, Any] = {"StartTime": from_, "MaxResults": min(self.page_size, TRAIL_PAGE_MAX),
                                  "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]}
        newest = _epoch(start)
        seen = dict(seen or {})
        # AWS answers newest first; changes are handed on in the order they happened, so an undo and a redo of the
        # same thing in one read leave it as it really is (the redo last).
        found: list[tuple[float, dict]] = []

        def done() -> tuple[str, dict[str, float]]:
            found.reverse()  # AWS's newest-first order, turned round, so events of the same second come oldest first
            found.sort(key=lambda item: item[0])
            lines.extend(line for _when, line in found)
            keep_after = newest - TRAIL_OVERLAP_S
            return _iso(newest + 1) if newest > _epoch(start) else start, {
                k: t for k, t in seen.items() if t >= keep_after}

        for _ in range(MAX_PAGES):
            page = self._call("cloudtrail", "lookup_events", region=region, **kwargs)
            for event in page.get("Events") or []:
                when = _epoch(event.get("EventTime") or start)
                key = str(event.get("EventId") or "")
                if key and key in seen:
                    continue
                if key:
                    seen[key] = when
                newest = max(newest, when)
                if only is not None and event.get("EventSource") not in only:
                    continue  # another service's change in a region read for the global services alone
                found.append((when, {"source": f"cloudtrail:{region}/{event.get('EventName', '')}",
                                      "message": event.get("CloudTrailEvent") or ""}))
                if limit and len(lines) + len(found) >= limit:
                    return done()
            token = page.get("NextToken")
            if not token:
                return done()
            kwargs["NextToken"] = token
        # Still more after MAX_PAGES: AWS answers newest first, so what is left is the oldest of the window. Keep what
        # was read and move on, rather than fail on every read and never move the bookmark again.
        log.warning("CloudTrail in %s had more than %d pages of changes; older ones in this window were skipped",
                    region, MAX_PAGES)
        return done()

    def _read_group(self, group: str, start: int, lines: list[dict], limit: int | None, source: str = "") -> int:
        self._touched_logs = True
        kwargs: dict[str, Any] = {"logGroupName": group, "startTime": start, "limit": self.page_size}
        newest = start
        for _ in range(MAX_PAGES):
            page = self._call("logs", "filter_log_events", **kwargs)
            for event in page.get("events") or []:
                lines.append({"source": source or f"cloudwatch:{group}/{event.get('logStreamName', '')}",
                              "message": event.get("message", "")})
                newest = max(newest, int(event.get("timestamp", start)) + 1)
                if limit and len(lines) >= limit:
                    return newest
            if not page.get("nextToken"):
                return newest
            kwargs["nextToken"] = page["nextToken"]
        raise SourceError(f"Reading {group} still had another page after {MAX_PAGES} pages.")

