# Decisions API v0

A WhiteHat calls the Decisions API on a chunk of code, a manifest, or Terraform before any model is involved. The call returns `clean` or `suspect`. A suspect carries the rule id, the evidence span, the CWE, and a score. v0 has no HTTP route. Confirmation, the next step (below), is the only place a model is allowed, and this package does not call one.

## What a run returns

`decide(root)` judges a whole tree. `decide(root, diff)` judges the unified diff, and keeps a finding only when its span overlaps a changed line. `root` is a directory. `diff` is the text `git diff` prints.

A change cannot switch a scanner off (R-052). Suppression comments in the code are data, never instructions: Semgrep runs with `--disable-nosem`, gitleaks with `--ignore-gitleaks-allow` and always with our own config (gitleaks' default rules, so a repository's `.gitleaks.toml` is not read), and a check that a `checkov:skip` comment skipped is reported like a failed one (Checkov runs without `--quiet`, which would leave skipped checks out). When the tree has `.gitleaksignore`, `.gitleaks.toml`, `.semgrepignore`, `.checkov.yaml` or `.checkov.yml` at its top, the container runner mounts an empty file over it, so the engine reads nothing from it; the repository is never changed. `tests/test_decisions_suppression.py` runs each engine against these (it needs Docker).

A person can accept a finding. Pass `acceptances=load_acceptances(path)` (from `decisions_api.acceptances`) and a covered finding keeps its place in the result with status `accepted`, so it stays visible and stops escalating. The file is YAML: `accepted:` then entries with `rule_id`, `path`, `resource`, `reason`, `accepted_by` and `accepted_on`. An entry missing any of those is refused. Read the file from the baseline, never from the tree under judgement. The proving ground's file is `accepted-findings.yaml`.

The verdict is `suspect` when an open result is above low. It is `clean` when nothing open is above low. Low results stay in the list so a reviewer can see them. They do not escalate.

```python
from app.decisions_api import decide

decision = decide(repo, diff_text)
decision.verdict          # "clean" or "suspect"
decision.escalating       # open results above low
decision.to_dict()        # verdict, suspects (with score), engines, changed paths
```

Each suspect has:

| Field | Meaning |
| --- | --- |
| `engine` | `semgrep`, `gitleaks`, `osv-scanner`, `checkov`, `trivy`, or `whitehats` |
| `rule_id` | The engine's id, or a `whitehats.*` id for a house rule |
| `evidence` | `path`, `start_line`, `end_line`, and `resource` (a Terraform address when there is one) |
| `cwe` | One or more `CWE-n` ids |
| `severity` | `low`, `medium`, `high`, or `critical` |
| `score` | 10, 40, 70, or 90, in that same order |
| `status` | `open`, or `accepted` when a person has accepted it |

Dependency scores use the CVSS v3 bands: 9.0 and above critical, 7.0 high, 4.0 medium, otherwise low.

A person can pass `acceptances`. An acceptance matches the rule id, the path, and the resource address. The suspect stays in the list and stops escalating. That is how a known high check, such as code signing on an image-based Lambda (Checkov `CKV_AWS_272`, which Lambda rejects for `package_type = Image`), can be recorded without hiding it.

## Engines

Each engine is a pinned image and runs in its own container. The tag is the version. Semgrep, OSV-Scanner and Trivy need the network to fetch the rule packs and the advisory databases. gitleaks and Checkov run with no network. A hung engine is stopped at 45 seconds (`WH_DECISIONS_ENGINE_TIMEOUT`); Trivy's image download has its own 5 minute limit, since a large base image seen for the first time takes over a minute. The engines run together, so a diff is not waiting on them one after another.

| Engine | Image | What it reads |
| --- | --- | --- |
| Semgrep | `semgrep/semgrep:1.182.1` | Public packs `p/python` and `p/secrets` |
| gitleaks | `ghcr.io/gitleaks/gitleaks:v8.30.1` | The image's default secret rules |
| OSV-Scanner | `ghcr.io/google/osv-scanner:v2.7.0` | Lockfiles, and PEP 621 dependencies written out as a requirements file when a manifest has no lockfile. A tree with no package source is clean, not an engine failure |
| Checkov | `bridgecrew/checkov:3.3.30` | Terraform and Dockerfiles |
| Trivy | `aquasec/trivy:0.57.1` | The base image of each Dockerfile the change touches (the last stage's `FROM`). Docker pulls the image and saves it to a file, and Trivy reads only that file, so the scanner never reaches the Docker engine. Only weaknesses with a fix count. One suspect per image (`trivy.base-image-weaknesses`), with the worst severity, how many there are and the worst five named, on the `FROM` line; the resource is the image with its digest, so an acceptance holds for exactly that image |

Trivy also scans the images that are running (`app/services/image_scans.py`, VS.5c). Discovery records on each function node the image Lambda resolved, with its digest. A digest no one has scanned is scanned within ten minutes, and every image again each day; the scan is stored per digest (`image_scans`). The first scan of a digest raises one finding on the function (how many weaknesses have a fix, the worst severity, the worst ten by advisory, package, installed and fixed version); a later scan raises one finding per advisory that was not there before; a function that runs another digest has the old digest's findings closed ("image replaced"). A private image is pulled with a token from the project's AWS connection, written into a Docker settings folder made for that pull and removed after it. `docker login` is not used: with no credential store named it falls back to the machine's (the macOS keychain through Docker Desktop), so the token would outlive the scan.

Open-source Checkov does not print a severity. `app/decisions_api/checkov_policies.py` maps each check id we have seen to a severity and a CWE, taken from the published policy pages. A check that is not in the table is medium, so an unknown check is escalated rather than dropped. The secret value from gitleaks, and the matched source line from Semgrep, are not copied onto the suspect.

Semgrep's rule cache is `~/.cache/whitehats/semgrep` (or `WH_SEMGREP_CACHE`). It is not part of the repository.

## House rules

Maintained engines do not express every class we care about. The rest live in `app/decisions_api/rules/`. A rule names the weakness class, not a repository, a function, or a file from one product. It carries examples that must match and examples that must not. `tests/test_decisions_rules.py` runs those examples, and `tests/test_decisions_boundaries.py` fails if a rule mentions a scenario id, a scenario file name, or a string that exists only in a scenario patch.

| Id | Class |
| --- | --- |
| `whitehats.secrets.provider-live-key` | A credential is written into the code instead of read from a secret store. The prefix is `pgsk_live_` plus a mixed letter and digit tail of at least 16 characters. A word-only tail is a placeholder. |
| `whitehats.diff.owner-check-removed` | A guard that checked who owns a record was removed. |
| `whitehats.diff.integrity-check-removed` | An integrity or signature check was removed or commented out. |
| `whitehats.code.storage-key` | Untrusted input is used as an object storage key. |
| `whitehats.code.unsafe-deserialize` | Untrusted data is passed to an unsafe deserialiser. |
| `whitehats.code.sensitive-output` | A credential or a whole personal record is written to a log or to stdout. |
| `whitehats.code.swallowed-error` | A broad error handler discards the error. |
| `whitehats.image.unpinned-base` | A container is built from a moving tag, so a rebuild can pull a different image. |

To add a rule, subclass `FileRule` or `ChangeRule` in that folder, set `id`, `title`, `weakness`, `cwe`, `severity`, `must_match`, and `must_not_match`, and add the `RULE` object to `RULES` in `rules/run.py`. File rules see the file text. Change rules see the added and removed lines, which is the only way to notice something that was taken away. Do not copy an example from a proving-ground patch.

## Confirmation (step 2)

`app/confirmation/` is the only step of a scan that calls a model. It sends each escalating suspect, and nothing else, through the model gateway (`services/llm.py`, purpose `triage`, so every call is on the usage ledger).

```python
from app.confirmation import confirm, gateway_ask

confirmations = confirm(decision, repo, gateway_ask(db, org_id), diff=diff_text, facts=service_facts)
```

Each call carries one suspect: the rule that fired, the flagged lines, 20 lines on each side, the lines the change removed near them, and short facts about the service. Read the facts from somewhere the change cannot touch (the clone, or the baseline), never from the tree under judgement.

Secret values never leave for the model (R-051). `app/confirmation/mask.py` replaces each secret-looking word on every line of the evidence, and in the service facts, with `[hidden <kind>, <n> characters]` before the message is built. A word is hidden when it starts with a known credential prefix (cloud access key id, provider live key, GitHub or Slack token, private key header), when it is a literal value given to a name that says secret (key, secret, token, password and the like), or when it is long and random-looking. Placeholders such as `changeme` and code such as `os.environ["TOKEN"]` stay visible, so the model can still tell a planted key from a fake one. The masking uses plain string tests, no regular expressions.

Evidence is data, never instructions (R-030):

- The instructions are only in the system message. The user message is one JSON object, so code and comments arrive as JSON strings and cannot close a delimiter or open a new section.
- The system message says that text in the evidence which claims the code was reviewed, is safe, or asks for a rejection is not evidence of safety.
- The answer must match a strict JSON schema: `verdict` (confirmed or rejected), `category`, `severity`, `confidence` and `explanation`. An answer that does not match is treated as no answer.

What comes back:

| Status | When | Severity |
| --- | --- | --- |
| `confirmed` | The model confirmed it | The higher of the Decisions API's and the model's |
| `rejected` | The model rejected a medium or low result | Unchanged; recorded as noise, the finding closes |
| `disputed` | The model rejected a high or critical result | Unchanged; stays open for a person |
| `unconfirmed` | No model answered, or the answer broke the format | Unchanged; the finding is reported, never dropped |

The model can raise a severity. It can never lower one, and it can never close a high or critical result. Only a person can. `tests/test_confirmation.py` checks this against a list of hostile and broken answers.

Budget per call: at most 3,000 input tokens (system and user message, by the gateway's estimate) and 1,600 output tokens. When the evidence is too big, the context narrows from 20 lines to 10, 5 and 0 on each side, then the facts go, then the flagged lines are cut.

`scripts/confirm_proving_ground.py --repo <proving-ground clone>` runs both steps over the baseline and every scenario, applying each with the proving ground's own scenario tool in a temporary copy. `--misleading` adds an "already reviewed, safe" comment above every finding first. `--offline` asks no model. It needs Docker and, for the model, the Azure settings in `.env`.

## How a change reaches it (VS.1)

`app/changes/` is the change trigger.
The GitHub connector reads pushes and pull requests, by webhook and by a poll every minute.
`changes.record` keeps one `ChangeEvent` for each head commit, in the same transaction that moves the connection's bookmark.
The row is unique on connection, repository and head commit, so the webhook and the poll can both report a commit and it is judged once.
Only repositories that a project's scope holds are kept.

`changes.process_pending` judges the waiting changes.
For each branch it takes the newest waiting change and marks the older ones `superseded`, because its diff starts before them.
The diff is taken against the last commit judged on that branch, then (if GitHub has lost that commit) against where the branch began: the default branch, or the parent of the oldest commit on the default branch.
`app/changes/fetch.py` reads that diff and each file it touches into a temporary folder, six at a time, and `decide(root, diff)` judges them.
The result is stored on the row: `status` (`decided`, `incomplete` when an engine did not run, `failed` after three tries), `verdict`, `decision` (what `Decision.to_dict()` returned), the files read and skipped, and the times.

The webhook route starts a run after it has answered.
A thread in `main.py` looks for polled changes every 10 seconds, on its own thread so a slow engine never delays the poll.

A later step reads the stored changes: confirmation (`confirm`, VS.3) needs the tree and the diff, which `fetched(source, repo, base, head)` produces again for a stored row; VS.4 turns confirmed findings into live signals and the check on the pull request.
Neither is part of this step.
No acceptance list is passed yet, so a known finding that the diff touches is reported until acceptances are wired in.

## Scan plans and the weekly floor (SCAN.1)

Where to look is planned; what is found comes only from a scanner in the catalogue (`app/scanning/catalogue.py`: Semgrep, gitleaks, OSV-Scanner, Checkov, Trivy, each with its pinned image and licence). The house rules always run; a plan cannot switch a check off.

A plan is plain data: a trigger, who made it, and targets. A target is one file of an attached repository or one image on the twin, the catalogue scanners to run on it, and a one-line reason. `app/scanning/plan.py` checks it before anything runs and refuses a scanner outside the catalogue, a file this run cannot read or a repository the project has not attached, an image that is not on the twin, a target without a reason, and any other key (`options`, `flags`, `args` and the like): a scanner's options live only in its module. A refused plan is stored with its refusal, and every file in scope is scanned with its default scanners instead, so a bad plan never means less scanning.

For a push, `app/scanning/planner.py` targets the changed files, each with the scanners that read it (`catalogue.defaults`), and the image of each service whose code changed, read from the twin: a service node carries `code_repo` and `code_folder` from the repository import, and `image` from discovery. A push that only touches `worker/` plans `worker/` files and the payments-worker image, nothing else. The plan is stored (`scan_plans`), the decision runs each scanner on its own files only (`decide(..., planned=...)`), and the push's findings point at the plan (`signals.scan_plan_id`).

The weekly floor (`app/scanning/floor.py`, checked hourly, each repository once a day) reads every attached repository's default branch in full and runs every file through its default scanners, and asks the image worker to scan every image on the twin. A floor finding has the same fingerprint as a push finding for the same weakness in the same file, so a weakness is one finding whoever found it; one found only by the floor is opened by it and points at the floor run. A complete floor run closes what is no longer there ("no longer found by the weekly scan"); a run in which an engine failed closes nothing. No model call makes a finding: the floor calls none, and the confirmation step only judges what scanners found.

## A full inventory per service and version (SCAN.2)

Every part of a service, direct and hidden, comes from two places (`app/scanning/inventory.py`): the exact versions its lock file pins (`packages`, from the repository import), and the image it runs (`image`, from discovery), else the one it is built from (`base_image`), else, for a zip Lambda function, the AWS runtime image it runs on (`public.ecr.aws/lambda/python:3.12-arm64` for `python3.12`). An image's parts are a CycloneDX inventory Trivy makes once per digest (`inventories`); an image named by tag is made again after a day, since a tag can move. A part the manifest does not name (`direct_dependencies`) is hidden: shop-api names no third-party package, and its runtime holds 118 parts, among them boto3, botocore, urllib3 and pip.

The image worker scans the stored inventory (`trivy sbom`), so a daily rescan of an unchanged version reads what is stored and pulls nothing. Lock-file parts are matched against our OSV.dev copy (KB.1). One image is one finding per project, naming every service that runs it. `GET /api/clones/{id}/nodes/{node}/inventory` lists a service's parts (only what is stored: making an inventory pulls an image, which is the worker's job); `?format=cyclonedx` gives the stored CycloneDX document.

## "Not affected" is a VEX statement (SCAN.5)

A finding never disappears because someone said it does not apply. On the Overview's code findings, a person with the triage permission picks one of OpenVEX's five justifications (in everyday words), writes one line, and may set an end date (`POST /api/signals/{id}/vex`). That is kept as a statement (`vex_statements`: the weakness, the product, the status, the justification, the reason, who decided and when) and applied (`app/scanning/vex.py`): the finding stays listed, marked "Not affected" with the reason and the name, and a push no longer escalates it (it becomes an acceptance with status `not_affected`, so it is reported and does not fail the check). A whole-file check counts it as still present, so it is never closed as fixed. Withdrawing it (`POST /api/vex/{id}/withdraw`) or reaching its end date stops it applying, and the finding is open again on the next pass; the statement stays in the history.

A supplier's OpenVEX document (`POST /api/vex/supplier`) is checked against the official OpenVEX schema (`app/scanning/openvex_json_schema_0.2.0.json`, from openvex/spec) and kept statement by statement with the supplier named. Statements about packages go to Trivy as input when it scans an image's stored inventory (`--vex`, with `--show-suppressed`): what they cover comes back marked not affected, worded from the statement, listed but not counted, never left out. `GET /api/vex.json` is the project's VEX as an OpenVEX document (valid against the schema, and `vexctl` reads it); `GET /api/vex` adds every statement with its state and how many findings are fixed, not affected and still open.

## What v0 does not do

The Decisions API does not call a model, store a decision in the database, or open a route. Confirmation does not store its results or feed rejections back into the rules yet.
