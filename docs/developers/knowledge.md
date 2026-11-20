# The knowledge base (KB.1)

WhiteHats keeps its own copy of [OSV.dev](https://osv.dev) for the ecosystems in scope, PyPI and npm first.
The agents look advisory facts up here and cite them; a model never supplies an advisory id, an affected range or a fixed version from memory.

## Where the data comes from

OSV publishes, for each ecosystem, a bulk export (`<ecosystem>/all.zip`, every record) and a change list (`<ecosystem>/modified_id.csv`, one line per record, `<modified>,<id>`, newest first) in a public bucket.
`app/knowledge/osv.py` loads an ecosystem from its bulk export the first time, streaming the zip to disk (npm's is about 220 MB, PyPI's about 37 MB).
Every later refresh reads the change list down to the newest change already stored and downloads only those records, so a refresh with nothing new downloads no record.
Measured on real data: PyPI loaded 26,742 records in 21 seconds; the second refresh fetched none in 0.3 seconds. The PyPI copy takes about 120 MB in SQLite.

Each record is stored whole (`advisories.record`), with an index of the packages it names (`advisory_packages`, names normalised the way each index matches them: PyPI folds case and treats `-`, `_` and `.` alike, npm is exact) and of its aliases (`advisory_aliases`). `knowledge_sources` keeps each ecosystem's bookmark (the newest change stored), its record count, and how the last refresh went.

The data is OSV.dev's, under CC BY 4.0. Every lookup answer and every finding raised from it carries the attribution "Vulnerability data from OSV.dev (https://osv.dev), licensed under CC BY 4.0.", the record's modified time and its address.

## Refreshing

The `whitehats-knowledge` thread refreshes every hour the ecosystems in `WH_KNOWLEDGE_ECOSYSTEMS` (production: `PyPI,npm`; elsewhere empty, so a development or test server never downloads the bulk exports by itself).
The platform operator can start a refresh at once: `POST /api/knowledge/refresh` with `{"ecosystems": ["PyPI"]}` (permission `operate_platform`).
`WH_OSV_BUCKET` points the refresh at another copy of the bucket, such as a fake in a test.
A failed refresh keeps what was stored and records the reason in `knowledge_sources.last_error`.

After a refresh that changed anything, the whole copy is written to `knowledge.db` in the data folder: a SQLite file the Runner reads with no network (`osv.export_copy`). The lookups work on it the same way.

## The lookup

`app/knowledge/lookup.py` is the one lookup, used by the confirmation step, Big Hat and SC.1:

- `by_package(db, ecosystem, name, version=None)`: the advisories that name the package, or only those that cover the version. Versions are compared in the ecosystem's own order (`versions.py`: PEP 440 for PyPI through `packaging`, semantic versioning for npm, parsed with plain string splitting), reading both the `versions` list and the `ranges` events (`introduced`, `fixed`, `last_affected`, `limit`). Withdrawn records are left out.
- `by_id(db, id)`: by the record's own id or any alias, so a CVE finds the GHSA that names it.
- `changed_since(db, time)`: what was added or changed after a time, oldest first.
- `cited_ids(text)` and `unknown_ids(db, text)`: the advisory ids a text names, and those the knowledge base does not hold.

Each answer is the record's facts: id, aliases, summary, details, severity, the affected ranges and fixed versions for the package asked about, affected functions where the record names them, references, published and modified times, source, address, licence and attribution.

Routes, for anyone in an account (`use_account`): `GET /api/knowledge` (the copy's state and the attribution), `GET /api/knowledge/packages?ecosystem=&name=&version=`, `GET /api/knowledge/advisories/<id>`, `GET /api/knowledge/changes?since=`.

## Who uses it

- Confirmation (`app/confirmation`): a dependency finding is sent to the model with `check.advisory`, the record's id, aliases, a short summary, affected ranges and fixed versions, kept small enough for the input budget. The system message says to name an id, a range or a fixed version only when `check.advisory` gives it. A confirmation whose explanation names an advisory id the knowledge base does not hold is kept as `unconfirmed`, with the reason, never shown as confirmed.
- Big Hat: a question that names advisory ids gets an "Advisories" section with their facts, cited as `[ADV-<id>]`; an id not in the knowledge base is said to be so. An answer that names an id the knowledge base does not hold gets a note saying that part is unchecked.
- New advisories (`watch.py`): each service node can carry `packages`, the exact versions its code uses (`PyPI:pyyaml==5.3.1`), read from the manifests and lock files by the repository import. After each refresh, every advisory added or changed is matched against them in the latest version of every clone, and a match becomes a live signal on that node (source `whitehats:advisory`), once per advisory and node while it stays open. A new advisory becomes a finding with no code change.

## Tests

`tests/test_knowledge.py`: the lookups give the same advisories, aliases, ranges, versions and fixed versions as the public OSV API on a recording (`tests/fixtures/osv/recorded.json`, made by `tests/record_osv.py`) and, with `OSV_LIVE=1`, against the live API; a second refresh with nothing new downloads no record; a failed refresh keeps the copy; the Runner's copy answers with the network switched off; versions in each ecosystem's order; a new advisory for a package `shop-api` uses is a finding on `shop-api` within one refresh; the advisory facts reach the model inside the prompt budget and an invented id is flagged; Big Hat's section; who may read and refresh.
