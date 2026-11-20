"""Calls the AWS connector will not make.

S3 object reads and DynamoDB item reads are refused even when the role would allow them.
Any other call that is not on the read list is refused too, so a write never runs.
"""

from __future__ import annotations

_DATA = "WhiteHats refuses to read {what}. It reads configuration and logs only."

REFUSAL: dict[tuple[str, str], str] = {
    ("s3", "get_object"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_acl"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_attributes"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_legal_hold"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_retention"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_tagging"): _DATA.format(what="S3 objects"),
    ("s3", "get_object_torrent"): _DATA.format(what="S3 objects"),
    ("s3", "select_object_content"): _DATA.format(what="S3 objects"),
    ("dynamodb", "get_item"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "batch_get_item"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "query"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "scan"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "execute_statement"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "batch_execute_statement"): _DATA.format(what="DynamoDB items"),
    ("dynamodb", "execute_transaction"): _DATA.format(what="DynamoDB items"),
    ("sqs", "receive_message"): _DATA.format(what="SQS messages"),
}

# Every call this connector may make, with the IAM action it needs. assume_role is the sign-in, not a write.
# The CloudFormation template grants exactly these actions (tests/test_aws_connector.py checks it).
ALLOWED: dict[tuple[str, str], str] = {
    ("sts", "assume_role"): "sts:AssumeRole",
    ("resourcegroupstaggingapi", "get_resources"): "tag:GetResources",
    ("iam", "get_role"): "iam:GetRole",
    ("iam", "list_role_policies"): "iam:ListRolePolicies",
    ("iam", "get_role_policy"): "iam:GetRolePolicy",
    ("iam", "list_attached_role_policies"): "iam:ListAttachedRolePolicies",
    ("lambda", "list_functions"): "lambda:ListFunctions",
    # Only for functions that run a container image: it is the one call that names the image and its digest.
    ("lambda", "get_function"): "lambda:GetFunction",
    # A pull token for the registry the image is in, so the image can be scanned (VS.5c).
    ("ecr", "get_authorization_token"): "ecr:GetAuthorizationToken",
    ("apigatewayv2", "get_apis"): "apigateway:GET",
    # An HTTP API's settings and its stages' rate limits (under /apis/*, which the template already grants).
    ("apigatewayv2", "get_api"): "apigateway:GET",
    ("apigatewayv2", "get_stages"): "apigateway:GET",
    ("dynamodb", "list_tables"): "dynamodb:ListTables",
    ("dynamodb", "describe_table"): "dynamodb:DescribeTable",
    ("s3", "get_bucket_location"): "s3:GetBucketLocation",
    ("sqs", "list_queues"): "sqs:ListQueues",
    ("sqs", "get_queue_attributes"): "sqs:GetQueueAttributes",
    ("ec2", "describe_instances"): "ec2:DescribeInstances",
    ("ec2", "describe_vpcs"): "ec2:DescribeVpcs",
    # Firewall rules and what they are attached to, for the outgoing-rules check (NET.1).
    ("ec2", "describe_security_groups"): "ec2:DescribeSecurityGroups",
    ("ec2", "describe_network_interfaces"): "ec2:DescribeNetworkInterfaces",
    # Where each network's traffic log goes, so a log-service one is read and the rest are recommended (NET.2).
    ("ec2", "describe_flow_logs"): "ec2:DescribeFlowLogs",
    # Amazon's own findings, read only where the customer has GuardDuty or Security Hub on (AMZ.1).
    ("guardduty", "list_detectors"): "guardduty:ListDetectors",
    ("guardduty", "list_findings"): "guardduty:ListFindings",
    ("guardduty", "get_findings"): "guardduty:GetFindings",
    ("securityhub", "describe_hub"): "securityhub:DescribeHub",
    ("securityhub", "get_findings"): "securityhub:GetFindings",
    ("rds", "describe_db_instances"): "rds:DescribeDBInstances",
    ("rds", "describe_db_clusters"): "rds:DescribeDBClusters",
    ("cloudtrail", "lookup_events"): "cloudtrail:LookupEvents",
    ("logs", "filter_log_events"): "logs:FilterLogEvents",
}
# Docker, not this connector, uses these with the pull token: they read an image's layers, never write one.
IMAGE_PULL = ("ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability")
# apigateway:GET is one action for every API Gateway read, so the template limits it to the API list.
APIGATEWAY_RESOURCES = ("arn:aws:apigateway:*::/apis", "arn:aws:apigateway:*::/apis/*")


def blocked(service: str, method: str) -> str | None:
    """Why this call must not run, or None when the connector may make it."""
    reason = REFUSAL.get((service, method))
    if reason:
        return reason
    if (service, method) not in ALLOWED:
        return f"WhiteHats does not call {service}.{method}. This connector only reads configuration and logs."
    return None
