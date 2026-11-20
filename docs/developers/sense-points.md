# Sense points

`app.services.sense_points.analyse` reads one clone graph and returns attack paths, choke points and a ranked list of where a WhiteHat should watch. It does not call a model. The same graph always returns the same list.

An entry is a public edge, a repo, a pipeline or a registry, or a node marked as a public exposure, a webhook or a persona. A crown jewel is a node marked `crown-jewel`, or one whose data classification includes PHI, PCI or secret. A path walks from an entry to a jewel in the direction of attack.
`ATTACK_FLOW` gives every edge type in `library/schemas/clone.schema.json` one direction, with the reason beside it:

| Direction | Edge types | Meaning |
| --- | --- | --- |
| forward | `calls`, `publishes_to`, `reads`, `writes`, `built_by`, `pushes_image_to`, `runs_as`, `stores_secret_in`, `emits_telemetry_to` | the attack moves from `from` to `to` |
| reverse | `consumes_from`, `deploys_from`, `routes_through`, `depends_on` | the edge is stored pointing at the thing the attack comes from |
| both | `runs_on`, `authenticates_via`, `replicates_to`, `backs_up_to`, `peers_with`, `syncs_with`, `generic_relation` | each side can reach the other (a host and its services, an identity provider and its apps, a copy and its original); an unknown relation assumes the worst |
| none | `contained_in` | sharing a group grants nothing by itself |

A test fails when the schema gains an edge type with no entry, and an edge of an unlisted type is refused with a `ValueError`.

A path holds at most six nodes.
The search stops after 2000 paths that reach a jewel, or after 200 000 steps (one step is one node visited on one partial path), whichever comes first, so a dense graph with no reachable jewel still stops in well under a second.
`is_complete` is false when either limit stopped the search, so a screen can say the list may be partial. Its weight is the number of distinct ATT&CK techniques on its nodes and edges, plus one for each node with `auth` false, `tls` false, or `wide_iam` true. A node that lists `attack_techniques` uses that list. Otherwise the techniques come from its library type.

A path that goes through a service acts with that service's identity, so each path also lists `acts_as`: the identities (`runs_as` targets) of its steps that are not steps themselves.
An identity in `acts_as` is on the path for watching: it can be a gap, it can be suggested, and an accepted sense point on it watches every path through the service.
It is not a step, so the paths, their weights and the choke points are the same as without it.

The result also lists `entry_points`, `crown_jewels`, and `coverage_gaps` (nodes on a path, or acted as on a path, that do not already emit telemetry and are not controls).

A node that already emits telemetry, or that is a control, is not suggested. The rest are ranked by greedy set cover: each pick covers the most remaining path weight per unit of `watch_cost` (1 when the cost is absent), where a path counts its weight and at least 1, so a path through a missing control or more techniques counts for more than a safe one (R-056). Then more paths, then the lower cost, then the node id that sorts first. Each suggestion says the weight it covers (`weight`). Choke points are ordered the same way: the summed weight of the paths through them, then how many paths. Coverage stays a share of paths, so "covers 8 of 9" means paths.

## Modes

Every suggestion carries `mode` (the one it is stored with) and `modes` (every WhiteHat mode that can work from that node), from `sense_points.modes(node)`.
A node that lists its own `modes` uses them. Otherwise the node library says: the modes of the kind's `default_sense_points` first, then the rest of its `whitehat_modes`. A kind the library does not know is watched by `telemetry`, with low confidence.
A sense point is one read-only way in to a node (a repository, a log group, an account), and every mode listed can work from that same way in: a repository feeds the code, secrets and supply chain WhiteHats.
The stored row keeps one `mode` and the API's `points[].modes` lists them all; the screens show the one mode today.

## Measured against the proving ground (P6.4)

`scripts/measure_sense_points.py` takes an exported clone (the hand-built proving ground, or the twin an import made), marks the crown jewels a person marks, asks for the list exactly as the route stores it (`sense_point_store.suggest`), and says for each weakness in the proving ground's scenario catalogue whether a suggestion of a right mode is on its place or next to it:

```sh
cd platform/backend
uv run python scripts/measure_sense_points.py ../e2e/screenshots/P2.5/proving-ground-by-hand.clone.json
```

`scripts/sense_point_weaknesses.json` is the mapping: for each weakness, the catalogue's own word for the sensor, the node library modes that word means, and its place (a node, or an edge between two nodes) named by library type and name, so one mapping serves the hand-built clone and an imported twin. On is the place itself; next to is a node that is the next or previous step of one attack path the analysis found.
The judged N is five (the five the Where to watch browser journey accepts), and the share must be at least 80 percent; the greedy cover's size, nine and the whole list are reported too, and each is given a second time counting only the first mode of a suggestion.
`tests/test_sense_point_reality.py` runs it on the hand-built clone, checks the mapping against the catalogue when it is on the machine, checks the result is the same in any order of the parts, and fails when the analysis, its tests or the library name a scenario or a string that only a scenario patch holds.
The scenario ids live only in the mapping and its test; the analysis never sees one.

## Stored sense points (P6.3)

`services/sense_point_store.py` keeps the analysis' answer as rows in `sense_points`, one per node per saved drawing (`clone_version_id` plus `node_key` is unique), with the project and company on every row.
A row has `status` (suggested, accepted, rejected), `origin` (analysis, or manual for one a person placed), `rank`, `paths` (the attack paths through the node when it was suggested), the mode and a rule-based `reason`, and `decided_by`.
The reason is built from the clone and the node library (how many paths the node is on, and the library's own reason for watching a node of its kind); no model is involved, so it is there with no model connected.
Suggestions are the greedy cover (the picks that cover every path) followed by `also_on_paths`, the other unwatched nodes on the same paths, most paths first, so a person has a choice beyond the minimum.

The routes (`routers/sense_points.py`), all scoped to the clone's project:

| Route | Needs | Does |
| --- | --- | --- |
| `GET /api/clones/{id}/sense-points` | `see_project` | The stored points, the version they are pinned to, the counts, and the coverage |
| `POST /api/clones/{id}/sense-points/analyse` | `edit_model` | Analyse the latest drawing and store the suggestions. Accepted, rejected and hand placed points are kept (carried over to the new version); old suggestions are replaced |
| `POST /api/clones/{id}/sense-points` | `edit_model` | Place one by hand on a node: accepted at once, never replaced by an analysis. Refused (409) when the points are from an older version: run the analysis again first |
| `PATCH /api/sense-points/{id}` | `edit_model` | Set accepted, rejected or suggested (a hand placed point can only stay accepted) |
| `DELETE /api/sense-points/{id}` | `edit_model` | Remove a hand placed point |

Every call answers with the whole overview, and decisions are written to the audit trail (`sense_points.analyse`, `.accepted`, `.rejected`, `.suggested`, `.place`, `.remove`).
The coverage is worked out on every read from the pinned version's paths and the accepted points, so it can never disagree with them: `share` is the share of attack paths with at least one accepted sense point on them (a suggestion or a rejection covers nothing); `paths_total`, `paths_watched`, `entry_points`, `crown_jewels` and `is_complete` come with it.
The company export and the offboarding delete already include the table (`services/tenancy.py`).
In the Studio, `studio/useSensePoints.js` holds the answer, `studio/sensepanel.jsx` is the "Where to watch" panel (both views), the scene draws each point as a ring with the robot, and the flat plan marks the card in words ("Watched", "Watch suggested", "Watch rejected").
