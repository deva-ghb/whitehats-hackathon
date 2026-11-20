# Clone document

A clone is one versioned JSON document: the nodes, edges, groups, access paths and credential references for an estate. The schema is `library/schemas/clone.schema.json` (schema version 1). `library_version` is the node library the type names came from, not the document schema.

Check a document from the repository root:

```sh
cd library && uv run pytest
```

The worked examples are `library/examples/template-a.clone.json` (preventive health on Azure) and `library/examples/template-b.clone.json` (fintech on AWS), taken from the node lists in the design, and `library/examples/proving-ground.clone.json`, the proving-ground estate.
Groups, like nodes, may carry `properties` (a network's CIDR ranges, for example), held to the same secret rules.
Whether every type in a document is one the library holds is a separate check: see [Node library](node-library.md).

What the check refuses:

- A node with no type, or no environment (`prod`, `staging`, `dev`, `dr`, `shared`).
- An edge whose type is not in the design's list (`calls`, `reads`, `deploys_from`, `generic_relation`, and the rest). `generic_relation` is the escape hatch: it is always allowed, and node types do not list it.
- A duplicate id across nodes, edges, groups, access paths and credential references.
- A secret value in a property: a live-style key, a private key block, or a URL that embeds a password. A property whose name ends in `password`, `secret`, `token`, `api_key`, `private_key` or `access_key` must be a `vault://` reference.
- A location, access path, credential id, edge end or group parent that does not exist in the same document.

Where the Studio draws an item is part of the document: a node or group may carry `layout` (`x` and `y`, in pixels, counted from the top left corner of the group it sits in, or of the canvas; a group also has `width` and `height`). Moving a group therefore moves what is inside it. A clone with no layouts is laid out automatically; the first edit in the Studio stores the places it drew. A node and a group may also carry `owner` and `notes`.
A group may not be its own ancestor (`parent` loops are refused).

Credential values never go in the document. A node may point at a `credential_refs` id or at a `vault://` URI directly.

## Node types

A node type is a YAML file in `library/types/`. The shape is `library/schemas/node-type.schema.json`. Every type needs at least one MITRE ATT&CK technique id. An edge rule may only name an edge type the clone document already allows (`calls`, `deploys_from`, and the rest).

```sh
cd library && uv run python -m library.validate
```

That checks every file in `library/types/` and exits non-zero, with the file name and the reason, when a type is missing ATT&CK ids or names an unknown edge.

## Provenance

Nodes, groups and, since PG.3, edges may carry `provenance`: `{ "kind": "imported" | "discovered" | "drawn", "source": "..." }`. An edge drawn in the Studio says `drawn`. An edge with no provenance (older clones) counts as "not recorded" when the share of a clone that came from an import is worked out. A clone never holds a Generic node: see [Importing an estate](importing.md) for where an imported one waits.

## Importing Terraform state

`app.services.terraform_state.import_state` reads a Terraform state file (version 3 or 4) and returns nodes, groups and edges. Managed AWS and Azure resources are kept. A data source is skipped, and so is any other provider. An event source mapping and an API integration become edges (`consumes_from`, `calls`) rather than their own nodes. A function's role is `runs_as`. A function in a subnet, and an Azure resource in a resource group, are `contained_in`. Subnets and virtual networks record their parent group.

Provenance is `imported`, and the source is the Terraform address. Each resource type maps to a node library type and to properties that type declares, in `app.services.terraform_types` (for example `aws_lambda_function` becomes `service.function@2` with `platform`, `runtime`, `memory_mb` and `region`). An imported clone therefore resolves against the library. A resource with no mapping becomes `generic.services@1`, and its `what_is_this` names the Terraform resource type, so it shows as a placeholder waiting for a proper type. Network and gateway load balancers are placeholders for now; application load balancers are `edge.alb@1`.

Environment variables, passwords and any value that looks like a secret are not copied. `change_set(previous, current)` is empty when the two imports describe the same estate. The change set is not stored. When an endpoint accepts it, that endpoint belongs to one project.

## How the Studio edits a clone

The Studio's code is split so that how the clone is drawn can change without touching how it is edited.

| File (`platform/frontend/src/studio/`) | Owns | Knows about drawing? |
| --- | --- | --- |
| `edit.js`, `geometry.js` | Every rule: place, move, resize, group, align, reparent, connect, change a connection, remove. Plain functions from a document to a document, in the clone's own coordinates | No |
| `session.js` | The draft, the saved versions (each with a reason), undo and redo, putting back a change the server refuses | No |
| `editor.js` | The controller: selection, what each action does to the document, the inspector's actions. It never imports a drawing library | No |
| `shortcuts.jsx` | Every key | No |
| `FlatCanvas.jsx`, `nodes.jsx`, `edges.jsx`, `layout.js` | The flat plan view: React Flow, the automatic layout, how a node and an edge look | Yes (this is the one surface) |
| `Workspace.jsx` | Puts the bar, the library, the surface, the inspector and the dialogs side by side | Only through the surface |

A surface tells the editor what the person did, in ground units (the clone's `layout` coordinates, absolute): `onSelect(ids)`, `onSelectEdge(edge)`, `onBlank()`, `onMoveEnd(Map id to {x, y})`, `onResizeEnd(id, box)`, `onConnect(from, to)`, `onDropType(type, point)` and `onBoxes(Map id to box)`, the boxes it drew. The editor asks the surface for `pointAt(clientX, clientY)`, `viewCentre()`, `fit()`, `relayout()` and `reveal(id)`. A surface that draws the estate in another projection only has to turn the pointer into a ground point (`pointAt`), draw the boxes it is given, and report the same events. The first edit of an automatically laid out clone stores the places the surface reported with `onBoxes`.

The pure parts have unit tests that run without a browser: `cd platform/frontend && npm test`.

## The isometric scene (STUDIO.2)

`studio/scene/IsoCanvas.jsx` is the second surface.
It reports the same events and answers the same calls as the flat plan, and `Workspace.jsx` swaps one for the other (`useStudioView`: the person's remembered choice, else the plan on a phone or after a first Tab before any pointer, else the scene).
Both surfaces draw the same stored `layout`, so a clone built in one view is the same clone in the other.
A clone with no stored places is laid out by `scene/placement.js` for the scene and by ELK for the plan; the first edit stores the places of whichever surface is open.
The scene packs a zone's nodes into one compact block (kept in order by kind and name) and packs that block and the zones inside it together with `scene/packing.js` (MaxRects, best short side fit), choosing the row width whose block is smallest and squarest on the screen, so a small zone tucks in beside a large one and a busy zone is a block, never a column.
Public edges and visitors form a block of their own at the left corner of the platform, and partners, repositories and delivery tools one at the right corner.
Ground x counts for less than ground y on the screen (`UNIT_X` = 160, `UNIT_Y` = 96 ground pixels to a drawing unit): a card on the plan is 232 by 72 pixels, three times as wide as deep, and a solid is about as wide as deep, so this brings the two views into step.

| File (`platform/frontend/src/studio/scene/`) | Owns |
| --- | --- |
| `project.js` | The projection ported from `website/brand/draw.py`: ground pixels to drawing units (`UNIT_X` = 160 along x, `UNIT_Y` = 96 along y) to screen, and back exactly. Everything stands on one plane (`BASE`, the top of the platform), so a pointer is always one ground point |
| `shapes.js`, `recipes.js` | SVG drawing functions for the shapes (box, cylinder, discs, stack, hexagonal prism, wedge, pyramid, person, robot, sense point, platform, zone) and which solid each kind of node is. Colour comes from CSS classes, so the theme changes it |
| `placement.js`, `packing.js`, `model.js` | Where things stand (automatic places, the platform under them), how rectangles are packed, and what is drawn: solids, zones, connections, outlines, the order to paint them in, how tall each solid stands, which one is the tower, and how high each person floats |
| `hit.js`, `labels.js` | Hit testing on convex outlines, and which names are shown and where (see below) |
| `useSceneView.js`, `useSceneInput.js` | The view (pan, zoom, fit) and the pointer and keys |
| `Layers.jsx`, `Overlays.jsx`, `scene.css` | The parts the scene is drawn from, what sits over it, and the palette (`--sc-*` tokens, light and dark) |
| `sensepoints.js` | Sense points as drawn data and the state tint of each node. The Studio hands the scene the stored list (`studio/useSensePoints.js`, see sense-points.md) |

The node families map to these solids: services are boxes (a function is a smaller box with a bolt, a frontend a low wide box); compute is a hatched stack of slabs; data stores are stacked discs (object storage and caches are flatter); messaging is a flat stack of plates; network edges are hexagonal prisms and stand outside, to the left; people and identity services are a floating badge with a dashed drop line; secret stores are a hatched box with a key; repositories are hatched stacks of slabs and delivery tools are slabs, outside, to the right; SaaS is a wedge, outside, to the right; observability is a box with a dotted top; security controls are pyramids; AI is a deep teal box; boundaries are zones; and a Generic or unknown type is a ghost box.
Heights and tints say something.
In a clone of eight nodes or more the busiest solids stand tallest (the busiest 15 percent at 1.45 times their height, the next 30 percent at 1.2 times, counting connections), and the busiest of all, when it takes part in at least three connections, is the deep teal tower, as in the website's picture (a finding still turns it red).
An AI service is a white box with a deep teal top.
The hover card says how many connections a solid has.
Hatching is the site's: the side of a service, a secret store, a repository or a registry, and the top of a pipeline.
A person floats at least 3.4 units above its place, higher when that is the only way its badge clears the solids and the other badges.

Names (`labels.js`): every name is 12 pixels on the screen.
The most important are placed first (the selected one, then the tower and anything with a sense point or a finding, then zones and people, then the rest from the front of the picture to the back), each in the nearest of its places: under its solid, above it, beside it, then a little way off with a thin leader line back to the solid.
A name never covers another name, a solid, a person badge, a sense point or the platform's own name; one with no free place is left out, and the hover card, the inspector and the Problems list always have it.
A zone's name sits above its back corner or slides along its two back walls (never past their length), and as a last resort lies over solids but never over a badge or another name.
The platform's own name lies along the front left face of the slab, shrinks with the slab and goes when it would be under 11 pixels.
A key (the Key button over the bottom left corner, open by default on a wide pane) says in words what each look means.

The shortcuts and the keyboard handler learned one extra item selector (`[data-scene-item]`) and, in the scene, arrow keys that follow the screen rather than the ground axes.

