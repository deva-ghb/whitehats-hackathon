# Importing an estate

An importer reads an estate from somewhere it is already written down and hands over nodes, groups and edges.
A person reviews them in the Studio (**Import an estate**), and only what they accept reaches the clone.
This page is for engineers changing that path.

## Pieces

| Piece | Where | What it does |
| --- | --- | --- |
| Terraform state importer | `app/services/terraform_state.py`, `terraform_types.py`, `terraform_access.py` | Managed AWS and Azure resources become nodes, groups and edges. Pure: no file, no network. |
| Repository scan | `app/services/repo_scan.py`, `repo_terraform.py`, `repo_manifests.py`, `repo_pipeline.py`, `repo_estate.py`, `repo_import.py`; files read by `connectors/github.py` (`repository_files`) | The Terraform, manifests, lock files, Dockerfiles and workflows of a repository become services, runtimes, stacks and connections. Pure apart from the file reader. |
| Connection discovery | `app/services/connector_status.py` (`project_view`) | What a project's attached connections found, already limited to the project's scope. |
| Change set | `app/services/changesets.py` | Compares an import with a clone document and builds one item per change. Applies the accepted items. Pure. |
| Storage and rules | `app/services/imports.py`, `app/models.py` (`ChangeSet`) | Keeps the items and each person's decisions; applies through the normal clone checks. |
| Routes | `app/routers/imports.py` | `/api/imports` (see the API page). |
| Screen | `frontend/src/pages/ImportReview.jsx`, `studio/ChangeSetView.jsx`, `studio/importing.js` | Start, review, apply. |

## Rules the code keeps

- **Provenance on every item.** Nodes, groups and edges say where they came from (`imported`, `discovered` or `drawn`, and a source such as `terraform:aws_lambda_function.shop`). Edges gained the field in PG.3; an edge with none counts as "not recorded".
- **Safe next to hand work.** An import only proposes removing what an import put there (provenance source starts with the importer's own name). An update keeps what a person added.
- **Repeatable.** Accepting everything and reading the same source again gives no change set. A rejected item is not offered again unless its content changes. An accepted connection that is left out because one end was rejected counts as rejected.
- **No secrets.** A secret is a real value, not a name: Terraform's own `sensitive_attributes` (an SSM parameter's `value`) and any string that looks like a secret (`services/terraform_secrets.py`). A reference to a secret, such as an SSM parameter's path or an ARN, is kept: it is what draws the edge from a function to the parameter it reads. The importer drops secret-looking values and any attribute it has no property for. `imports.open_change_set` scans the items with the clone document's secret finder before storing them. The raw state file is never stored.
- **Same checks as a Studio save.** Applying runs `services.clones.document_problems`, the check behind `POST /api/clones`. If it refuses, nothing is saved and the reasons come back.
- **Resources that only configure another resource** (`terraform_types.SETTINGS`) are counted (`left_out`) and not drawn.
- **Policies become connections.** `terraform_access.py` reads inline role policies: each service that runs as the role reads, writes, publishes to or consumes from the resources the policy names. A test checks that every edge it can make is one the library allows between a function and the target type.

## Connections a state spells out as attributes

Each rule reads one attribute that names another resource and draws one edge.
A reference that names nothing in the state makes no edge.

| Edge | Read from | Why it is a rule |
| --- | --- | --- |
| `calls`, API to function | An API integration's or authorizer's target address. API Gateway is given the function's invoke address (`invoke_arn`), so the function is found by that address as well as by its ARN. | The integration and the authorizer are the only places the state says the API reaches a function. |
| `depends_on`, queue to dead-letter queue | The queue's redrive policy (`deadLetterTargetArn`). The queue's `dlq` property is filled with the dead-letter queue's name. | The state holds this only as an attribute of the queue. |
| `reads`, function to image repository | The function's image address (`image_uri`), without its tag or digest, matched to a repository's `repository_url`. | A function run from an image cannot start without it. |
| `depends_on`, resource to key | A table's encryption block, a parameter's `key_id`, and the same attribute on queues, log groups, functions, trails and databases, when it names a key in the state. | The resource cannot be read without the key. |

A dead-letter queue makes no `consumes_from` edge from a role policy.
The policy that lets a worker receive from its queue usually lets it receive from the dead-letter queue too, so a person can look at stuck messages, and that is not consuming.
A trigger from the dead-letter queue (an event source mapping) is still consumption.

Two resources that share a name (an HTTP API and the function behind it) keep their names in the clone.
The review screen adds the kind in brackets to each, only while the names collide, so a connection reads "shop-api (Amazon API Gateway) calls shop-api (Serverless function)".

Where the state gives no value for a required property, the property is left empty and the node stays Draft.
An image function has no runtime in the state (Terraform records an empty text), and a key with no alias resource has no alias.
`scripts/check_state_import.py` lists each of these.
A key with an alias is named by it; without one it is named by its Terraform label and what it is (`data key`).
The repository scan (below) reads the runtime from the Dockerfile.

## The repository scan (P5.2)

The scan reads one repository's default branch through the GitHub connector: one file tree, then each file it asks for as a blob (a file over 1 MB or not UTF-8 text is skipped and listed).
Only reads: every call is a GET, besides the App's sign-in.
The scan itself has no network; a recorded repository (`tests/recordings/connectors/github/scan-ok.json`) and the live one go through the same code.

How each kind of file is read, and with what (no regular expressions are used anywhere in it):

| Files | Read with | Gives |
| --- | --- | --- |
| `*.tf` | `python-hcl2` (a Terraform parser, added to `pyproject.toml` and `uv.lock`; it is pure Python and needs no network at test time) | Functions, queues, tables, image repositories, buckets, with their properties. `repo_terraform.py` follows variables, locals, module inputs and outputs, `count`, and `${...}` text. Anything it cannot know (a call, a computed value) stays unknown and is never guessed; a `sensitive` variable is never read; a module whose count is 0, or whose source is outside the repository, is reported. |
| `pyproject.toml`, `uv.lock`, `poetry.lock`, `requirements.txt`, `package.json`, `package-lock.json` | `tomllib`, `json` and string splitting | A project: language, version, direct dependencies by name, what the lock file pins. Option lines in a requirements file (index addresses with logins) are never read. |
| `Dockerfile` | line scan (last `FROM` is the image that runs) | The base image, and from it a runtime for an image function: `python:3.12` is `python3.12`. |
| `.github/workflows/*.yml` | `yaml.safe_load` | Roles the workflow assumes, whether it uses stored keys or OIDC, whether actions are pinned to a full commit, the token's permission, self-hosted runners, secret names (names only), the scripts its steps run and the image pushes. |

What becomes a connection: a function `deploys_from` the repository (the project whose folder the function's Terraform names as its code; the file that says so is the connection's source), the repository `built_by` its workflows, the workflows `authenticates_via` the role they assume (the role the Terraform declares under that name, else one made from the ARN), and `pushes_image_to` an image repository that a step, or a script a step runs, pushes to.
A folder with a Dockerfile and no function becomes a service; a project nothing deploys is counted, not drawn.
Every item's provenance names the file it came from (`repo:owner/name:path`).

A bucket named with an account id (`${account}`) is named only when a workflow's role names the account; otherwise it is listed as not drawn.

Scanning a clone that already holds some of this (from a state or a connection) never adds a second copy: an item is matched by id, ARN or kind and name, and the scan only fills what the clone's item lacks, including a note.
Read the state first, then the repository, then the state again: a bucket the state names only in a policy or a trail has no node until the scan puts it in the clone (`import_state_with_pending`, `with_pending_resolved`), and the second read of the state then draws the connections that were waiting.

## Where imported Generic nodes are stored

They are not stored in a clone.
The clone save refuses a Generic type (`library.resolve`: a Generic node does not count as resolved, and the P1 exit check and the Studio say the same), and an import must not be the way around that.
A resource with no library type becomes a **held** item in the change set.
It stays visible on the review screen's *Needs a type* list, with the Generic "what is this" text (for example `Terraform resource aws_kinesis_stream`).
A person accepts it only after choosing the nearest library type, or rejects it.
The properties the new type does not declare (the Generic text) are left behind; where the thing is (`arn`, `region`) stays.
If the importer still only knows Generic on the next read, the type a person chose is kept.

The held item lives in the stored change set (an `open` set keeps it until applied or discarded).
When the library later gets a proper type, the next import maps the resource to it and proposes it as an ordinary change.
The request-a-type flow (P1.4) is where a held item would also file the request.

## Matching two sources

A Terraform state and a connection's discovery name the same resource differently.
For `connections` imports, `changesets.align` drops a discovered node whose `arn` property equals one already in the clone, and points its connections at the clone's own id.
The ARN is the only thing the match reads, so the state importer carries it on: functions, buckets, log groups, SSM parameters and the HTTP API, as well as queues, tables, roles, keys, image repositories, trails, load balancers, event rules and databases (`terraform_types.MAPPINGS`, the `arn` attribute).
Virtual machines, networks and flow logs do not carry it yet.
It is the attribute the provider documents (`arn`), exactly as the state holds it, never one built from parts.
A resource whose state leaves `arn` out gets no `arn` property and is not matched.
(`execution_arn` and `invoke_arn` are other things: API Gateway's address for permissions and for calling a function. They are not the resource's ARN and are not used.)
A scan of a repository matches the same way (`changesets.enrich`, by id, ARN or kind and name).
Import the Terraform state first; the other direction is not matched yet.

## Checking a real state file

```sh
cd platform/backend
uv run python scripts/check_state_import.py <path to a state file>
```

It prints counts and provenance, whether the result resolves against the node library, and searches the import for every value in the state that sits under a secret-sounding key or looks like a secret.
It prints key paths only, never values, and exits 1 on a leak or an unresolved clone.
Keep real state files in the git-ignored `secrets/` folder (they hold secret values).

## Comparing an import with a hand-built clone

The proving ground is the one estate that exists both ways: drawn by hand (`e2e/tests/proving-ground-by-hand.spec.js`) and imported from its real Terraform state.
`e2e/tests/proving-ground-compare.spec.js` and `e2e/compare_clones.py` put them side by side (see [Testing](testing.md)).
What an import of a state can never give is listed in the PG.3 journal: no object-store buckets that Terraform did not create, no network boundary, no owner or notes, no connection that comes from reading the repository, and no names a person would choose.
