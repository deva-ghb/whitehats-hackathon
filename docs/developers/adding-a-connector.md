# Adding a connector

A connector is how WhiteHats reads one kind of system.
Every connector meets the same contract, and the contract tests prove it against recorded answers before it ships.
A connector only reads.
It never decides what to do with what it read.

## The contract

| Part | In the code |
| --- | --- |
| Least access | `permissions`: each permission it needs, with what it is used for. None may write. |
| Test connection | `test()`: signs in, says who the source thinks we are, and returns one probe per permission, each ok or not, in plain words. |
| Discover | `discover()`: everything in scope, as clone nodes and edges with provenance `imported`, each a type the node library holds. |
| Sync | `read(bookmark)`: everything new since the bookmark, across every page, and the bookmark to start from next time. With a `limit` it reads a sample and keeps the bookmark where it was. |
| Events | `handle_event(headers, body)`: a pushed event, checked by its signature (`signature_matches` in `app/connectors/webhooks.py`). The route keeps each delivery once (`remember_delivery`). A source that is only polled leaves the default, which refuses. |
| Limits | `with_retries` and `Pacer` in `app/connectors/retry.py`: bounded attempts, backoff, the provider's own wait hint, and calls kept under its published rate. |
| Health | `health()` in `app/connectors/base.py`: last read, items read, lag and last error. A source that never read, has not read for 15 minutes, or failed is a blind spot. |

The caller stores the bookmark (`app/connectors/bookmarks.py`), feeds the lines to the log analyzer, and places discovered nodes.
Errors a person must act on are raised as `SourceError` with a plain message and never a secret.

## 1. Write the class

Add a module to `backend/app/connectors/` and register it in `REGISTRY` in `app/connectors/__init__.py`.
`cloudwatch.py` is the example to copy.
Take the AWS clients, the clock and `sleep` as arguments, so the tests can replay recordings and wait no real time.

## 2. Record its answers

Write a recording script next to `tests/record_cloudwatch.py`.
It runs the connector through a `Recorder` from `tests/connector_kit.py`, which saves every call and answer as JSON under `tests/recordings/connectors/<key>/`.
The recorder replaces temporary credentials with placeholders and real account ids with `111122223333`.
Read the files before committing them.

Record these scenarios:

| File | What the connector does |
| --- | --- |
| `test-ok.json` | `test()`, every probe passing |
| `signin-refused.json` | `test()` with a sign-in the source refuses |
| `read-twice.json` | `read({})` over more than one page, then `read()` from the bookmark it returned |
| `throttled.json` | `read({})` told once to slow down |
| `discover-ok.json` | `discover()` |

A source that takes pushed events also records `events-forged.json` (a forged delivery makes no call), and its `Case` names the webhook secret and one real delivery to replay.
An HTTP source (not boto) records through `HttpRecorder` and replays through `HttpReplay`, both in `tests/connector_kit.py`; it takes a `Transport` from `app/connectors/http.py` as an argument.
`tests/record_github.py` is the example.

Record from a real account where you can.
A sandbox or a simulator is the fallback, and the recording's `about` line says which it was.

## 3. Add its contract case

Add a `Case` to `CASES` in `tests/connector_contract.py`: how to build the connector against a recording, and how many items a recording's answers hold.
`tests/test_connector_contract.py` then runs it through every check, and fails if a registered connector has no case.

## 4. Say what its data is about

A connection is set up once per account and attached to projects, each with a scope (`connectors/scope.py`).
Identity, endpoint and ticketing connections are account-wide: every project uses them, and they are never attached.
Cloud logs connections narrow by accounts, regions, tags and resource groups; code connections by repositories; CI/CD connections by repositories and pipelines.
A new connection starts attached to Main with everything in scope, so a one-project account works without setup.

Give every discovered node the properties that scopes match on: `arn` or `url` (the resource's identity, the same in every project), `account`, `region`, `tags`, `resource_group`, `repo`, `pipeline`.
`scope.split` then sends each node only to the projects whose scope holds it.
The node library accepts these keys on any type, and the contract test fails if a discovered node has no `arn` or `url`, or if a scope naming its account does not keep it.
Raise a finding from an event with `routing.raise_finding`: it lands once in each matching project, with `Signal.resource` set, so the account counts it once.
Log lines do not name their resource yet, so a sync matches the scope against the connection itself (`routing.connection_resource`) and runs only the WhiteHats of the matching projects.

## 5. Document it

Add a setup guide to `docs/connectors/` with the exact permissions, matching `permissions` in the class.
