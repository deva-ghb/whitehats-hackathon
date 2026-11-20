# AWS

Reads one AWS account through a role the customer creates. WhiteHats assumes that role with an external id. It discovers tagged resources and reads CloudTrail and CloudWatch from a bookmark. It does not accept a long-lived access key.

The role is created from [aws-readonly-role.yaml](aws-readonly-role.yaml). In the CloudFormation console, choose Create stack, upload that file, and fill in the three parameters. The stack output is the role ARN.

## 1. Create the role

| Parameter | What to enter |
| --- | --- |
| RoleName | A name in your account, for example `whitehats-readonly`. |
| TrustedPrincipal | The ARN of the WhiteHats runner that will assume the role. |
| ExternalId | A long random string. Enter the same value in WhiteHats. It is not an AWS key. |

The template grants only these reads:

| Permission | Used for |
| --- | --- |
| sts:AssumeRole | The runner signs in. This is on the trust policy, and only with the external id. |
| tag:GetResources | List resources in scope from their tags. |
| iam:GetRole | Read a role and the names of its policies. |
| lambda:ListFunctions | List Lambda functions and their configuration. Environment values are not stored. |
| lambda:GetFunction | For a function that runs a container image only: read which image, with its digest. Never called for a zip function, whose answer would include a link to its code. |
| apigateway:GET | List HTTP and WebSocket APIs. Allowed only on `/apis` and `/apis/*`, so API keys and exported API definitions cannot be read. |
| dynamodb:ListTables | List DynamoDB tables. |
| dynamodb:DescribeTable | Read a table's configuration. |
| s3:GetBucketLocation | Read which region a bucket is in. |
| sqs:ListQueues | List SQS queues. |
| sqs:GetQueueAttributes | Read a queue's configuration. |
| ec2:DescribeInstances | Read an instance's size, operating system, public address and instance profile. |
| ec2:DescribeVpcs | Read a VPC's address ranges. |
| ec2:DescribeSecurityGroups | Read firewall rules, incoming and outgoing. |
| ec2:DescribeNetworkInterfaces | See which firewall rules are in use. |
| guardduty:ListDetectors, guardduty:ListFindings, guardduty:GetFindings | Read GuardDuty's open high and critical findings, where it is switched on. |
| securityhub:DescribeHub, securityhub:GetFindings | Read the high and critical findings Security Hub gathered, where it is switched on. |
| ec2:DescribeFlowLogs | See where each network's traffic log goes, to read it or recommend switching it on. |
| rds:DescribeDBInstances | Read a database's engine, encryption, public access and backups. The database is never opened. |
| rds:DescribeDBClusters | The same for an Aurora cluster. |
| cloudtrail:LookupEvents | Read new CloudTrail management events. |
| logs:FilterLogEvents | Read new CloudWatch log events. |
| ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer, ecr:BatchCheckLayerAvailability | Pull the image a function runs, to scan it for known weaknesses. Nothing can be pushed or deleted. The pull token is kept in a settings folder of the scan's own and removed when the pull is done. |

Images are scanned when a function first runs a new digest (within ten minutes of discovery seeing it) and again every day, so an advisory published later becomes a finding with no code change. A function's findings close when it moves to another image. See `platform/docs/developers/decisions.md` (Trivy) for how a finding is built.

The same template denies S3 object reads, DynamoDB item reads, SQS message reads, Parameter Store values, Secrets Manager values and KMS decrypt. WhiteHats also refuses those calls in code, before a request is sent, even if a role would allow them.

## 2. Connect

| Field | Example |
| --- | --- |
| AWS region | `ap-southeast-2` |
| Tag key and value | `project` = `whitehats-proving-ground` |
| Log groups | `/aws/lambda/shop-api` |
| Read-only role ARN | the stack output |
| External ID | the value from the template. Required: the connection is not saved without it. |
| Other regions | `us-east-1, eu-west-1`, optional |

Discovery lists what the tag query lists, plus an account node and one node for each region that holds something. A resource with no tag in scope is left out. The tag query is run in the home region and in every other region you name, and each resource is described in its own region.

Each resource becomes a node of its own type: S3 buckets, Lambda functions, DynamoDB tables, SQS queues, log groups, trails, API Gateway APIs, ECR repositories, Parameter Store parameters, IAM roles, EC2 instances (inside their VPC when the VPC is tagged too), VPCs, EventBridge rules and buses, and RDS databases and Aurora clusters. IAM roles name their inline and attached policies.

A resource WhiteHats has no type for yet, for example a security group or an IAM policy, is still shown. It appears as a Generic node in the closest family, saying what it is (for example "AWS ec2 security-group"), so a person can see it and ask for a proper type. One such resource never stops the rest of discovery.

CloudTrail is read with `LookupEvents`, and each log group with `FilterLogEvents`. The bookmark is just after the newest event. The next read starts there, so an event is not returned twice.

## What you see

A failed sign-in names the denial in plain words. A missing permission is reported on its own, and the rest of the check still runs. Object and item reads are refused even when you ask for them.
