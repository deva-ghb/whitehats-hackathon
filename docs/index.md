# WhiteHats documentation

Everything an operations or engineering team needs to run, extend and support WhiteHats.
Every page here is also available inside the product under **Help**, and each screen links to the page that explains it.

## For WhiteHats operators

- [Onboarding a company](operations/tenant-onboarding.md): from signed contract to go-live, check by check.
- [Offboarding a company](operations/offboarding.md): export, retention window, deletion certificate.
- [AI models](operations/ai-models.md): connecting Azure OpenAI and other models, budgets and policy.
- [Running in production](operations/production.md): settings, database, TLS, the background worker.
- [Backups and upgrades](operations/backups-and-upgrades.md): what to back up, how upgrades migrate the database.
- [Troubleshooting](operations/troubleshooting.md): the problems we have seen and how to fix them.
- [Clone Studio](operations/clone-studio.md): the map of your estate, what each part of the screen shows, and importing a clone.

## Deploying WhiteHats into a customer environment

- [Collectors](deploy/collectors.md): log collectors, network analyzers and process monitors on Kubernetes, Docker or Linux.

## Connectors

- Cloud logs: [AWS CloudWatch](connectors/aws-cloudwatch.md), [Azure Monitor](connectors/azure-monitor.md), [Google Cloud Logging](connectors/gcp-logging.md).
- [AWS](connectors/aws.md): account, tagged resources, CloudTrail and CloudWatch, through a read-only role.
- Identity: [Okta](connectors/okta.md), [Microsoft Entra ID](connectors/entra-id.md), [Google Workspace](connectors/google-workspace.md).
- Code and work: [GitHub](connectors/github.md), [Jira](connectors/jira.md), [CrowdStrike](connectors/crowdstrike.md).
- [Alerts](connectors/alerts.md): Slack, Microsoft Teams and email.

## For developers

- [Architecture](developers/architecture.md): how the parts fit and where the code lives.
- [Threat model](developers/threat-model.md): what WhiteHats itself holds, who would want it, and the control.
- [Adding a WhiteHat](developers/adding-a-whitehat.md): write a new specialist in one file.
- [API](developers/api.md): authentication, the main endpoints, the interactive reference.
- [Testing](developers/testing.md): running the suite and what it covers.
- [Clone document](developers/clone-document.md): the schema for a versioned estate, and what it refuses.
- [Node library](developers/node-library.md): the types a clone can contain, the rules each one follows, and how a clone is resolved against them.
- [Importing an estate](developers/importing.md): how a Terraform state or a discovery becomes a change set a person reviews, and where Generic nodes go.
- [Sense points](developers/sense-points.md): how a clone graph is ranked into places a WhiteHat should watch.
- [Decisions](developers/decisions/): canvas engine, vault, Runner isolation, the Runner to Big Hat protocol, and the Decisions API.
- [Decisions API](developers/decisions.md): how a scan returns clean or suspect, the pinned engines, the house rules, and how a model confirms a suspect without being able to lower it.
