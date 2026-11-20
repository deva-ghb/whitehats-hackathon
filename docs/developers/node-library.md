# Node library

The node library is the catalogue of things a clone can contain: one YAML file per type in `library/types/`, with a deploy guide template for each in `library/guides/`.
The shape of a type is `library/schemas/node-type.schema.json`.

## What is in it

86 types cover every node and group in the two design templates and the proving ground, plus security groups, virtual machines and RDS databases for the Terraform importer, and each of the 15 families also has one Generic type.
Run the resolver to see the count by family:

```sh
cd library && uv run python -m library.resolve
```

Types are per product (`data.s3`, `data.blob`), as the templates use them.
Where two names meant the same thing they were merged: one `boundary.vpc` with a `cloud` property for AWS VPCs and Azure VNets (design 8.4), one `service.frontend` for web apps, one `delivery.terraform` with a `backend` property that covers Terraform Cloud and S3 state, and one `endpoint.customer` for customers and patients.
GuardDuty is `control.guardduty`, in the security controls family (design 7.13).

## Rules every type follows

`uv run python -m library.validate` checks each file, and with no arguments the whole library:

- The file is named after the type id (`data.s3.yaml` holds `id: data.s3`).
- Every edge it allows is an edge type the clone document knows.
- Every ATT&CK id is in MITRE's published list and is not retired. A retired id is refused with the id that replaced it.
- The deploy guide it names exists under `guides/` and has the five sections from design 13.4: Prerequisites, Customer-side steps, Runner-side steps, Verification and Rollback.
- No property key appears twice.
- Each family has exactly one Generic type, named `generic.<family>` (for example `generic.code-and-delivery`), marked `generic: true`. Only Generic types use that prefix.

## Edges

An edge `A <type> B` is allowed only when `A`'s type lists it in `allowed_out` and `B`'s type lists it in `allowed_in`.
The direction follows design 6.3. In particular, `A routes_through B` means A is reached through the gateway B (a service routes through its load balancer, which routes through its CDN).
`contained_in` points from anything to a boundary.
`depends_on` is the general relation and every type accepts it.
A Generic relation (`generic_relation`) is always allowed, even when neither type lists it. It is not written on the types themselves.
Generic types accept every edge both ways, so a team is never blocked.

## Resolving a clone

```sh
cd library && uv run python -m library.resolve                       # every example
cd library && uv run python -m library.resolve path/to/one.clone.json
```

The resolver first validates the clone document, then checks every node and group:

- its type is in the library at the version it pins (`repo.bitbucket@3`);
- it is not a Generic type (a Generic node is a placeholder waiting for a proper type);
- every property it sets is declared by the type and fits it (a choice from the list, a number, a true or false, a list, an `http` or `https` URL);
- a secret-tier property is never written in; it is bound as the node's `credential`.
- any node may also carry the routing properties `arn`, `url`, `account`, `region`, `tags`, `resource_group`, `repo` and `pipeline`, which say where a thing is rather than what it is, so a connection can send it to the projects whose scope holds it. A type that declares one of these keys itself checks it its own way.

It then checks every edge against both ends.
It prints one line per item with the type and family, then each problem, and exits non-zero when there is any problem.

The examples are `library/examples/template-a.clone.json`, `template-b.clone.json` and `proving-ground.clone.json`.
The proving-ground example is the default estate from checklist section 3.1, without the optional network add-on.

## Search

`library/library/search.py` finds types the way people ask for them (design 9.2):

```sh
cd library && uv run python -m library.search "dynamo db"
cd library && uv run python -m library.search kubernetes --cloud azure
```

- It matches the display name, aliases, the id, vendors and the family.
  A whole-phrase match beats a type-ahead prefix, which beats finding every word somewhere in the type, so "amazon queue" finds SQS through its vendor and an alias.
- It allows a common typing mistake: one in a word of 4 to 7 letters, two in a longer word, counting swapped neighbours as one ("gaurdduty", "terrafrom").
  Spaces do not matter ("cloud watch", "dynamo db").
- A pasted AWS ARN or Azure resource id is recognised by `library/library/resource_ids.py`, which maps the service or resource type to library types and keeps the region, account, subscription, resource group and name for pre-filling a node.
- Context: on a canvas for one cloud, that cloud's types rank first; inside a VPC, VNet, subnet or Kubernetes cluster, network, compute, service, data and messaging types rank higher; while drawing an edge, types the source can connect to rank higher.
- Generic types count half, so they lead only when nothing specific matches or the query asks for "generic" or "other".
- Facets count the matches by family, cloud, vendor, whether an importer can find the type, and whether it has a WhiteHat mode; the same keys filter the results.

The platform serves it at `GET /api/library/search` (see the [API](api.md)); the canvas cloud ranks, and `provider` filters.

`library/tests/search_queries.yaml` holds 100 queries written from the user's side before the search was built: aliases, vendor names, misspellings, pasted ARNs and Azure ids, and context.
The test fails if fewer than 95 find their type in the top 3, and prints every miss.
Add a query whenever someone fails to find a type.

## ATT&CK ids

The validator checks ids against `library/data/attack-enterprise.json` (ATT&CK enterprise 20.0).
When MITRE publishes a new version, download `enterprise-attack.json` from the attack-stix-data repository and rebuild the file:

```sh
cd library && uv run python -m library.attack ~/Downloads/enterprise-attack.json data/attack-enterprise.json
uv run python -m library.validate
```

Then fix each type the validator refuses.

## Adding a type

1. Copy the closest type in the same family and change the id, names, aliases and vendors.
2. List properties by tier: required stays small, because a node stays Draft until every required property is filled.
3. Put the least access the connection test needs in the secret property's `scopes_hint`.
4. Write `guides/<id>.md` with the five sections. Use `{{ node.name }}` and `{{ properties.<key> }}` for values the Studio fills in.
5. Run `uv run python -m library.validate` and `uv run pytest`.

A type's `version` goes up whenever its properties or edges change. Nodes pin a version and move to a new one explicitly.
