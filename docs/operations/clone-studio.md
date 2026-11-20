# Clone Studio

Clone Studio shows your estate as a map: your services, data stores, message queues, identities, logs and the outside services you depend on, and how they connect.
WhiteHats uses this map, called a clone, to decide where to watch.

Open it from **Clone Studio** in the menu.

## Drawn from your connections

In this version WhiteHats draws the map from your connections, and the Studio only shows it.
Press **Connect a source** and choose AWS, GitHub or CloudWatch logs.
If the project has no map yet, one is started for it, named after the project.
The connection is placed on the map and its setup opens on the right.
Once it is connected, what it can see appears inside it, with no drawing by hand.

An Engineer or Project admin in the project can do this, and so can an Account owner or Account admin.
Everyone else can look, read what each thing is, and export the map.

While the map is drawn from connections, the Studio shows the scene only.
The node library, the plan view, and the buttons that draw, import or undo are hidden, and the server refuses a drawing sent by a person.
The **Search the map** box over the canvas is marked **Coming soon**: finding things and correcting them by hand arrive in a later release.
You can still mark crown jewels, press **Find where to watch**, and accept or reject each sense point.

An installation that needs drawing by hand sets `WH_STUDIO_HAND_EDITING=1` and restarts.
The rest of this page describes the Studio with drawing by hand switched on.

## What is on the screen

- **The bar at the top** names the clone you are looking at, its environment and its version, and holds the buttons: **New clone**, **Fit to screen**, **Lay out again**, **Export** and **Import a clone**. It also says whether the latest change is saved.
- **The node library** on the left lists every kind of thing a clone can contain, grouped by family. Search it by name, vendor or a common nickname, for example "parameter store" or "bucket". Select a kind to read what it is and what WhiteHats needs to know about it. Drag a kind onto the canvas, or select it and choose **Place on the canvas**.
- **The canvas** in the middle draws the clone. Boxes with a title, such as an AWS account or a region, contain the things inside them. Lines are the connections between things.
- **The inspector** on the right shows whatever you selected: its details, its properties grouped by how much they matter, where it came from, and whether it is complete.
- **Problems** at the bottom lists everything that still needs attention. Select a line to jump to that item.

## Two ways to look at a clone

The switch at the top right of the canvas chooses the view.

- **Scene** draws the clone as the isometric picture on the website: the whole estate stands on one white platform labelled with the clone's name and environment, zones are dashed floors with low glass walls, and every kind of thing has its own shape.
  A service is a box, a data store is a stack of discs, a queue is a flat stack of plates, a repository is a hatched stack of slabs, a secret store is a hatched box with a key, and a public edge, such as a load balancer, is a six sided prism that stands outside the platform.
  People and identity services float above their place on a thin dashed line.
  Point at anything to read, in plain words, what it is and why it matters.
- **Plan** is the flat map described below.
  It is the view for dense clones, for exact editing and for the keyboard.

A wide screen opens the scene.
A phone opens the plan, and so does the first press of the Tab key before the mouse has been used.
Your own choice is remembered in this browser and wins over both.
Everything you can do in one view you can do in the other, and what you build in one is there in the other.

In the scene, a solid tinted teal is watched, a solid tinted red has a finding, and a dashed footprint under it means it is still a Draft.
The busiest solids stand tallest, and the busiest of all, the one with the most connections, is the tall deep teal tower.
The Key button over the bottom left corner of the scene says all of this in words.
A target ring with a small robot beside it is a sense point: a solid ring is accepted, a dashed hollow ring is suggested and waiting for a person, and a ring with a bar through it was rejected.
Sense points are real and saved with the clone.
The **Where to watch** bar over the top left of the canvas (closed until you open it) shows the share of attack paths that have an accepted sense point on them.
Open it, mark what matters most (Criticality: Crown jewel in the side panel), and press **Find where to watch**: the best places come back ranked, each with its reason in plain words, with no model needed.
Accept or reject each one, or select a node and press **Place a sense point on** it to add your own.
The percentage follows every choice.
If you change the clone afterwards the panel says the points were worked out on an older version; **Run again** keeps your decisions.
The plan view shows the same points in the list and in words on each card.

Names stay 12 pixels tall.
A name never sits on another name, on a solid or on a person; when its own place is taken it moves a little way off and a thin line joins it to its solid, and when there is no room it is left out (point at the solid to read it).
Zoom in and more names appear.
When you zoom far out, the scene draws plain boxes and hides the names, so a clone of 500 things stays smooth.
The scene follows the light or dark setting of your computer.

## Draft and Described

Every item on the canvas is either:

- **Draft** (dashed outline): some required details are missing. The inspector says which ones.
- **Described** (solid outline): every required detail is filled in.

An item whose kind is not in the library says **Type not in library**, and it appears under Problems.

## Moving around the map

- Drag the empty canvas to move around. Scroll, or use the **+** and **−** buttons, to zoom.
- When you zoom out, the boxes first show only their names, in larger text, and then only their icons. Text never gets too small to read.
- **Fit to screen** shows the whole clone at once. **Lay out again** redraws it.
- With the keyboard: press Tab to move between items, Enter to select one, and Escape to clear the selection. Press **?** for the full list of keys.

## Importing and exporting

**Import a clone** reads a clone document (a `.json` file) and opens it.
Only people who may edit the canvas in the project (Engineers and Project admins) see this button.
The import refuses a file that is not a clone document, a file that holds a password or key, and a file that points at another company's stored credentials, and it says why.

**Export** downloads the clone as a document you can keep, review or import again.

## Importing an estate

**Import an estate** (next to Import a clone) fills a clone from where the estate is already written down, instead of drawing it by hand.
Nothing it reads reaches the clone until you say so.

1. Choose the clone, or start a new one by giving it a name.
2. Choose the source: a Terraform state file, or what this project's connections found (Connectors, then Find resources).
3. **Read it and compare** opens a list of changes: each resource, boundary and connection that would be added, updated or removed.
4. Accept or reject each change, or **Accept all that are ready**. A change you reject is not offered again unless it changes.
5. **Apply** makes the next version of the clone from what you accepted.

The screen shows how much of the clone came from an import, now and after your accepted changes.
Reading the same source again, with nothing new in it, opens no import at all.
Hand work is safe: an import never removes anything you drew, and an update changes only what the source says, not the notes, criticality or extra properties you added.

**Needs a type.** When the node library has no type for a resource, the change is held.
A clone never holds a Generic node, so the change stays on its own list until you pick the nearest library type for it (the Generic "what is this" text is left behind) or reject it.
A connection waits for the resources at both ends: if one end is not accepted, the connection is left out and the result says how many.

**Not drawn.** Settings that only configure another resource (a policy, an encryption setting) are counted and listed, not drawn as boxes.

The state file is read once and not kept.
Values that look like secrets, such as tokens and passwords, are left out and never reach a change set or a clone.

## On a smaller screen

Below a laptop width, the library opens from the **Library** button and slides over the map, and the inspector opens from the bottom when you select something.
On a phone, the Studio is for viewing only: editing needs a wider screen.

## Building by hand

A person who can edit the clone (an Engineer or a Project admin in the project) builds and changes it on the canvas. Everyone else sees a **Read only** note that says why, and can still look, search the library and export.

**New clone** starts a blank map. Give it a short name. The canvas then says it is empty.

**Place.** Drag a type from the library and drop it where you want it: it lands under the pointer, and inside a container if you drop it on one. A new item starts as **Draft**. Without a mouse, press Enter on a type in the library: it goes to the middle of the view, or into the container you have selected. A Generic type is a placeholder and cannot be placed; the library shows it dimmed.

**Connect.** Drag from the dot on a node and drop anywhere on another node. While you drag, items that take a typed connection from this one are ringed, and items that only take a Generic relation fade. A window asks which kind it is and lists what the library allows between the two. A kind is refused, with the reason, when the two types do not allow it. **Generic relation** is always allowed. To change a connection later, select it and choose another kind in the side panel; the same rules apply. Without a mouse, select a node and press **C**.

**Move, resize, group.** Drag a node to move it. Drop it on a container to put it inside, or on the bare canvas to take it out. A container holds what is inside it, moves with it, and grows to fit what you drop on it. Drag its corners to resize it; it never shrinks past what it holds. Boundary types (an account, a VPC, a subnet, a trust zone) are containers. To wrap items you already have, select them and choose **Group in a container** (or press Ctrl+G). Removing a container keeps what it held.

**Select many.** Shift-click or Ctrl-click to add items, Shift-drag on the empty canvas to select a box, Ctrl+A to select everything. With several items selected the side panel offers **Line up** (left, centre, right, top, middle, bottom), **Spread across** and **Spread down** (three or more), **Group in a container** and **Remove**. **Snap to the grid** on the toolbar keeps places on an 8 px grid.

**Undo and redo.** The toolbar buttons, Ctrl+Z, and Ctrl+Shift+Z (or Ctrl+Y). Undo saves the earlier drawing as one more version; nothing is ever deleted from the history.

**Settings, owner and notes.** Select an item and use the side panel: name, owner, notes, the container it sits in (**Inside**, the keyboard way to move something into a container), criticality, then the properties grouped into Required, Recommended, Optional, Credential and Filled by import. Fill the required ones and the item becomes **Described**. Credentials are not typed in here. They stay in the vault. Typing in one field is saved as one version when you pause.

**Versions.** Every change is saved at once as the next version, with who made it (the signed-in person, never something the browser claims) and a reason in words, such as "Placed Amazon RDS database instance" or "Undid: Moved orders-api". **Version history** on the toolbar lists them, and **Restore as newest** brings an old one back as a new version. A change the server refuses (for example a connection the library forbids) is put back to the last saved version, and the notice says why.

**Keys.** Every action has a key; press **?** for the list. Arrow keys move the selection 16 px (Shift: 64 px), Alt and arrow keys resize a container, Delete removes (a notice offers Undo), F2 goes to the name field, Shift+Enter adds a focused node to the selection.

## What comes next

The Network view, and commenting on saved versions, arrive in later releases of the Studio.
