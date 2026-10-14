# floorplan-editor

Edit architectural floor plans as data, in a local browser editor.

A plan is a JSON file of walls, doors, windows, counters, fixtures, labels, and erase
regions, measured in inches and laid over the marketing sheet or drawing it came from.
The editor runs as a small Python server on your machine and uses your browser as the
screen. Every save writes the plan's JSON plus an SVG, a PNG, and an overlay of the plan in
red on the source sheet, so you can check each change against the original.

It handles the usual remodel sketching jobs: delete or stretch a wall, draw an L-shaped
counter, drop a door and flip its swing, place and rotate a toilet or washer/dryer, white
out printed ink you are replacing, save each idea as its own step, and merge the steps
you like into one plan.

## Install

You need Python 3.10 or newer. The package installs into any virtual environment.

Chee's machine uses the shared venv at `C:\Users\CheeC\code\.venv`:

```powershell
C:\Users\CheeC\code\.venv\Scripts\python.exe -m pip install -e C:\Users\CheeC\code\floorplan-editor[dev]
```

That puts a `floorplan` command in `C:\Users\CheeC\code\.venv\Scripts`. Either activate the
venv (`C:\Users\CheeC\code\.venv\Scripts\Activate.ps1`) or call
`C:\Users\CheeC\code\.venv\Scripts\floorplan.exe` directly.

### Setting up your own copy

If you are not on Chee's machine, make a venv of your own. Clone the repo, create a venv
inside it, and install.

Windows (PowerShell):

```powershell
git clone https://github.com/armedad/floorplan-editor.git
cd floorplan-editor
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\floorplan.exe serve examples\sample
```

macOS or Linux:

```bash
git clone https://github.com/armedad/floorplan-editor.git
cd floorplan-editor
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/floorplan serve examples/sample
```

Add `[dev]` after the `.` (`-e ".[dev]"`) if you want to run the tests. If `py` or `python3`
is missing, install Python from [python.org](https://www.python.org/downloads/) first. You
only create the venv once; after that, the last line is all you need to start the editor.

## Run the editor

```powershell
floorplan serve
```

It starts a server on http://127.0.0.1:35667 and opens your browser. You can also give it a
folder to start in (`floorplan serve C:\Users\CheeC\code\riverside-39c`). Options:

- `--sheet PATH` picks the source image when the folder has more than one.
- `--port N` uses another port.
- `--no-browser` starts the server without opening a tab.

To try it without a plan of your own, run `floorplan serve examples/sample`.

### Opening things

There is one **Open…** button (Ctrl+O), and you can also drop files anywhere on the window.
Pick whatever you have, and the editor works out what it is:

- **A plan `.json`** opens in its folder, along with that folder's other steps and sheet.
- **A folder** opens its newest step, or `plan.json` if there are no steps.
- **A floor plan image** (`.png` or `.jpg`) becomes a project. The editor copies it into a
  new folder under `~/floorplans` and traces the walls. It also guesses the scale from how
  thick the walls are (most interior walls are about 4½″). Check the scale against a printed
  dimension with **Scale (K)**, which blinks for you after a new trace.
- **The same image again** reopens the project already made from it, instead of making a
  second copy. The same goes for an image whose folder already has plans next to it.
- **A plan and an image dropped together** become a project of their own.

The arrow next to Open lists recent folders and other ways to open: a folder, a typed
path, or the browser's own file picker when the system dialog can't appear. Set
`FLOORPLAN_WORKSPACE` to put new projects somewhere other than `~/floorplans`.

### What goes in a plan folder

| File | What it is |
| --- | --- |
| `plan.json` | The base plan, usually traced from the sheet. Steps are measured against it. |
| `plan-step1.json`, `plan-step2.json`, ... | Saved ideas, each an edit of the base. |
| `plan-merged.json` | The result of merging steps. |
| any `.jpg` or `.png` not starting with `plan` | The source sheet. A file whose name starts with `sheet` wins if there are several. In a building, each floor names its own sheet (`floor-2.png`, ...). |
| `plan-export.jpg` | Every floor stacked in one picture, from **Export JPEG**. |

Each plan file is saved with a matching `.svg`, `.png`, and `-overlay.png` (a building gets
a stacked `.png` and `-overlay.png`). The overlay is only written when the sheet is the same
pixel size as the plan's canvas.

The server holds your unsaved edits in memory, so reloading the page doesn't lose them.
Closing the server does.

## Tour

The toolbar runs across the top, the drawing fills the middle, and the side panel is on
the right, in folding sections: the selection's properties, flags, the folder and its
steps, view settings, and auto-trace. `\` (or the ◧ button) hides the panel. A hint along
the bottom of the drawing says what the current tool expects next, and the status bar
shows the cursor position in feet and inches, what the cursor snapped to, the zoom, and
the open file (a dot means unsaved).

Every action is reachable more than one way, and each shows its shortcut wherever it
appears:

- **The action bar** floats next to whatever is selected, with the things you can do to
  it: type its size, move it by an exact distance, flip a door, split a wall, duplicate,
  flag, delete.
- **Right-click** the drawing for the same actions, plus "drop a flag here" and "paste
  here".
- **Commands (Ctrl+K)** lists every command with its shortcut. Type a few letters to find
  one ("del", "scale", "flag").

Hover over any button or setting for a tooltip with its shortcut. Tooltips on the harder
ones (tracing, converting, thickness, steps and merging) have a **Learn more** link that
expands into a fuller explanation; F1 expands it from the keyboard.

### Selecting

- Click an object to select it. Shift+click (or Ctrl+click) adds or removes one. Click
  empty space to clear the selection.
- **Ctrl+drag** draws a box to select an area, and **Shift+drag** adds the box to the
  selection. Either one works starting anywhere, even on top of walls. Dragging left to
  right selects what is fully inside the box (blue). Dragging right to left selects
  anything the box touches (green, dashed).
- Ctrl+A selects everything. Esc clears the selection.
- Drag an object to move it, along with everything else selected. Wall ends that meet
  another wall move together; hold Alt to pull one free. Pressing Shift after the drag
  starts locks it to one axis.
- Drag empty space to pan (or Space+drag or middle-drag anywhere). The mouse wheel zooms.
  The cursor shows what a drag will do: a hand pans, arrows move, a crosshair draws a box.

### Exact sizes and positions

Select something and its measurements appear on the drawing, the way a CAD program shows
driving dimensions. Click any measurement, type the value, and press Enter:

- **Walls** show their length and the clear distance to the nearest parallel wall on each
  side. Typing a clear distance moves the wall.
- **Doors and windows** show their width and the distance from each corner of their wall.
- **Counters, fixtures, and erase regions** show width and depth, plus the clear distance to
  the wall on each side. **Outlines** show every edge.

Each measurement has its own color, shared by its line, its label, and its field in the side
panel. Hovering one says what it measures and what typing a value changes, and lights up the
wall a clear distance is measured to. Each label starts in the nearest empty spot, clear of
other labels, walls and fixtures, ink on the sheet, and the action bar; when that's off its
line, a dotted line in its color ties it back. A label can also be dragged anywhere; dropping
it back where it was lets it find its own spot again. A press that doesn't move is a click and
opens the box. Dragged labels stay put for the session.

When a size changes, something has to stay put. The box offers the choices by name ("left
end", "middle", "right end") and remembers your pick for the next time. The same fields,
with the same choices, are in the side panel.

Anywhere a length is asked for, type it the way you'd say it: `126`, `10'6"`, `10'-6"`,
`10 ft 6 in`, `6 1/2"`, `320cm`, or arithmetic such as `10' - 4.5` or `(30 + 2) * 2`.
Plain numbers are inches. In the side panel, the arrow keys nudge a field by 1″ (Shift 1',
Alt ¼″), and dragging a field's label sideways scrubs the value.

**Move… (M)** moves the selection by an exact amount: `6, -18` is 6″ right and 18″ up, and
`3'<90` is 3' at 90° (straight up). The arrow keys nudge the selection 1″ (Shift 12″, Alt ¼″).
**Duplicate (Ctrl+D)** copies the selection 1' over, and **Copy/Paste (Ctrl+C, Ctrl+V)**
pastes at the pointer.

### Tools

- **Select (V).** Select, move, and reshape. Drag a handle to reshape: wall ends, door
  edges, counter corners, a fixture's corner (size) or its yellow dot (rotation). Drag the
  dot in the middle of an outline's edge to add a corner; Alt+click a corner to remove it.
  Double-click a wall to split it there.
- **Wall (W).** Click each corner. After the first click you can type instead of clicking:
  a length goes toward the pointer (`10'6"`), a length and angle goes that way (`8'<90`,
  degrees counterclockwise from right), and `48, -12` goes right and up by those amounts.
  A corner snaps to the nearest wall, counter, or fixture, and the segment straightens when it is close to horizontal or vertical. Hold Ctrl to place it exactly where the pointer is; Shift forces it straight even from farther away. Double-click, Enter, or Esc stops.
- **Door (D)** and **Window (N).** Click on a wall to drop one there. The click snaps to the nearest wall; hold Ctrl to use only the wall under the pointer. Type a number first
  to change the width. Click in the gap between two walls in line (a traced doorway) and
  the two walls join into one with the door filling the gap. With a door selected, S flips
  the swing and H flips the hinge side.
- **Counter (C).** Drag a rectangle, or click one corner and type `width, depth` (`25, 60`).
  Corners snap to nearby walls, counters, and fixtures. Hold Ctrl to place one exactly where the pointer is.
- **Outline (P).** Click each corner of a counter of any shape, such as an L, or type the
  edges like walls. Corners snap the same way walls do. Double-click or Enter closes it. Backspace removes the last corner.
- **Fixture (X).** Pick a type and a rotation in the toolbar, then click to place it at its
  usual size, or drag a box over the printed symbol to fit it. The point snaps to a nearby wall, counter, or fixture; hold Ctrl to place it exactly. A fixture's center is the
  point it rotates around. Types are refrigerator,
  range, oven, dishwasher, toilet, tub, soaking tub, shower, sink, washer, dryer,
  washer/dryer, stool, fireplace (hatched), cabinet (a tall cabinet or pantry, drawn with a diagonal), upper
  cabinet, and generic. Base cabinets and islands are counters. R turns the next placement,
  or a selected fixture, 90°.
- **Label (T).** Click where the label goes and type it. The point snaps to a nearby wall, counter, or fixture; hold Ctrl to place it exactly. Text typed in the toolbar box is
  reused for every click, for dropping the same label several times.
- **Erase (E).** Drag a rectangle over printed ink on the sheet. Corners snap like a counter; hold Ctrl to place them exactly. The region whites out that
  ink in the editor and in every saved overlay. It's stored in the plan, so it can be
  moved, resized, or deleted later.
- **Scale (K).** Click both ends of a length printed on the sheet, then type that length,
  such as `26'5"`. This sets how many sheet pixels make an inch.
- **Measure (M).** Drag to measure a distance. Each end snaps to the nearest wall, counter,
  or fixture, and the line straightens when it is close to horizontal or vertical. Hold Ctrl to measure exactly where you drag. Moving
  the pointer shows a dotted line to the first edge in each direction; a click keeps the
  horizontal and vertical ones. Drag an end to move that end, or the middle to slide the
  whole line. M measures when nothing is selected, and moves the selection when something is.
- **Flag (F).** Drop a numbered flag; see the next section.

Snapping is on by default. While you add a wall, door, window, counter, outline, fixture, label, or erase region, the point snaps to the nearest wall, counter, or fixture, and a new wall or outline side straightens when it is close to horizontal or vertical. Hold Ctrl to place it exactly where the pointer is. Editing a point snaps to wall ends and wall faces, then to a 1-inch grid.
Both can be turned off under **View** in the side panel, which also controls the sheet
image, its opacity, the measurements on the selection, object ids, and whether erase
regions are shown.

### Cleaning up a trace: convert, merge, and thickness

The tracer sees ink, not meaning, so a traced plan is all walls: door leaves, fixture
outlines, and the letters of room names come out as short walls, and a thick wall often
comes out as two or three thin ones side by side. These tools turn them into what they are.

- **Convert… (Shift+C).** Select the pieces (Ctrl+drag a box around them, or Shift+click) and
  choose what they are. Each choice has a one-key shortcut while the menu is open:
  - **W, one wall.** Walls merge into one; anything else becomes a wall along the
    selection's long side.
  - **D, a door** or **N, a window.** If the selection includes the two walls on either side
    of a gap, or sits in front of a gap, the walls join and the opening fills the gap.
    Otherwise it goes on the nearest wall, as wide as the selection. A door swings toward
    the side the selected leaf was drawn on. Long walls in the selection are kept.
  - **X, a fixture.** Pick the type; the list is sorted by how well each type's usual size
    matches the selection, and digits 1 to 9 pick from it. The fixture fills the
    selection's box, turned to match.
  - **C, a box counter**, **P, a counter outline**, **T, a label** (asks for the text and sizes
    it to the selection), or **E, an erase region** that whites out the printed ink.

  The selected pieces are removed and the new object is selected. Ctrl+Z undoes it.
- **Merge (J).** Two or more walls running the same way become one: the outer faces of the
  pieces set the new thickness, and the longest piece keeps its id. Doors and windows on
  the pieces move to the merged wall, and walls joined to the pieces stay joined.
- **Thickness (Shift+T)** sets one thickness for every selected wall. The side panel has the
  same field, with **Stays put** to choose which face holds still (top or bottom for walls
  running across, left or right for walls running up and down) so a wall can thicken into a
  room without moving the other side.
- **Clean up a trace**, under **Auto-trace walls** in the side panel:
  - **Select stacked walls** selects walls that are really one: thin bars side by side, or
    one wall broken in two along its length. Breaks where another wall joins are left alone.
  - **Merge stacked (Shift+J)** merges each of those groups. With walls selected, it only
    looks among them.
  - **Select stray bits** selects short, thin walls that touch no real wall, which are
    almost always traced letters or symbol pieces. Delete them, or Convert them to a label
    or an erase region.
- **Object ids (I)** shows every object's id on the drawing, so you can say "convert w74
  and w75 into a door" to Cursor. **Ctrl+Shift+C** copies the selected ids. Cursor can also
  see what you have selected, so "make what I selected a toilet" works too.

### Flags: pointing Cursor at things

A flag is a numbered pin. Press **F** and click a spot to drop one, or drag to flag an area.
Shift+F flags whatever is selected. Type a note if you like ("make this toilet 2 inches
narrower"); then in Cursor, say "look at F3" instead of describing where things are.

Cursor reads each flag's note, where it is, and what it sits on (`list_flags`), can crop a
picture around it (`view_image` with `flag`), and can drop its own flags to show you what it
means. Flags stay with the folder, in `flags.json`. They aren't part of any plan, so they
survive saving steps and merging, and never appear in saved drawings.

The **Flags** section of the side panel lists them. Click a number to zoom to it, ✓ to mark
it done, ⧉ to copy a one-line reference to paste into a chat, and × to remove it. **Copy
open flags for Cursor** copies them all at once. Drag a pin to move it.

### Comments on objects

Select any one object (a wall, door, counter, fixture, label, or erase region) and press
**Ctrl+/**, or click **Comment…** on its action bar or **Add a comment…** in its
properties. Type the comment and press Enter. Each comment gets an id (`c1`, `c2`, ...), and
the object gets a purple bubble showing how many comments it has. Click a bubble to select
the object and read its comments in the side panel, where you can **Edit** or **Delete**
each one (clearing the text also deletes it). The **Comments** section of the side panel
lists every comment in the plan; click an object's name to zoom to it. **Comment markers**
in the View section hides the bubbles.

Comments belong to their object and are saved in the plan file with it:

- They move when the object moves.
- Merging walls or converting pieces moves the comments onto the result.
- Deleting an object deletes its comments.
- Duplicates, pasted copies, and the new half of a split wall start without comments.
- Adding, editing, and deleting a comment are edits like any other: Ctrl+Z undoes them,
  and they need a save.
- When steps are merged, comments merge on their own, so one step commenting on a wall
  while another moves it is fine.

Cursor sees comments too. It can list them (all of them, or the ones on particular
objects or on your selection), add its own (marked "Cursor"), edit, move, and delete them.
Use flags to point at a place for a task you'll mark done; use comments for lasting notes
about a thing.

### Steps and merging

Treat `plan.json` as the drawing as it exists today, and save each idea as a step.

1. Open `plan.json`, make one change, and click **Save as plan-stepN** in the toolbar.
   The editor switches to the new step.
2. Click `plan.json` in the side panel's file list to start the next idea from the base.
   Or keep editing the step and save another step on top of it.
3. Check the boxes next to the steps you want and click **Merge checked**. The result is
   `plan-merged.json`, opened for you.
4. **View overlay** opens the current file's overlay on the sheet. Each file in the list
   also has `png` and `overlay` links.

A merge applies each step's changes relative to `plan.json`, matched by object id. If
two steps change the same object in different ways, the merge stops and names both
steps, so nothing is silently overwritten. **Save** overwrites the open file,
**Save as…** writes a new name, and **Revert** drops unsaved edits.

### Buildings: several floors in one file

One plan file can hold every floor of a building. Each floor keeps its own sheet image,
scale, flags, and comments.

- The bar over the top left of the drawing shows the building's name and a tab for each
  floor, top floor first. Click a tab, or press **PageUp** and **PageDown**, to flip floors.
  Each floor remembers where you were zoomed. Every tool and panel works on the floor
  you're looking at.
- **+ Floor** adds a floor to the open file. It can come from a floor plan image, which is
  traced into walls (the scale is guessed from the walls, or copied from the current floor).
  It can also come from a plan `.json`, bringing its floor or every floor of a building, or
  from a plan folder, using its newest plan. The new floor's sheet is copied into this
  folder as `floor-N.png`. Adding a floor to a one-floor plan turns the file into a building.
- Right-click a tab (or double-click it) to rename, reorder, or remove a floor. Click the
  building's name to rename it. All of this is undoable and needs a save.
- **Export JPEG** (Ctrl+E) writes `<file>-export.jpg` in the folder. Every floor is drawn
  clean on white at one scale, stacked top floor first with its name above it, and the
  building's name is the title.
- **Save to a new folder…** (under **Folder and steps**) copies the open file, with every
  floor's sheet image and flags, into a new folder as `plan.json` and opens it there. A
  bare name makes the folder next to the current one.
- Steps work on buildings the same way: **Save as plan-stepN** snapshots the whole
  building. Merging matches floors by id, so one step can edit Floor 2 while another adds a
  floor. Saving a building writes a stacked `.png` and overlay instead of an `.svg`.

### Starting a new plan from an image

Usually you just open or drop the image (see [Opening things](#opening-things)): it is
copied into a project folder, traced, and scaled for you. To control the details, use
**New plan (manual setup)…** under **Folder and steps**: choose the image, say which compass
direction is up on the sheet, give a pixels-per-inch value if you know it (leave it empty to
guess from the walls), and choose whether to trace.

**Auto-trace walls** in the side panel re-runs the tracer with your own settings. It finds
the long, dark, solid bars on the sheet and offers them as walls:

1. Click **Preview** to see what it found, drawn over the sheet.
2. Adjust the sliders and preview again:
   - **Ink darker than** is the gray level (0 to 255) that counts as ink.
   - **Thinnest wall** and **Thickest wall** set the bar thickness range, in pixels. Raise
     the thinnest to skip window and cabinet lines. Lower it to pick up thin partitions.
   - **Shortest wall** drops short bars, in pixels.
   - **Merge bars side by side into thick walls** joins bars that lie side by side, about
     the same length, into one thicker wall. It's on by default.
3. Click **Add them** to add the walls. **Replace existing walls** clears the old ones first.
4. Clean up (see [Cleaning up a trace](#cleaning-up-a-trace-convert-merge-and-thickness)):
   select and delete the stray bits, convert door leaves and fixture outlines, join corners,
   and add the thin walls the tracer skipped.

On the 220 Riverside 39C sheet, the default settings found 152 walls. They covered at least
80% of the length of 84% of the thick walls in the hand-traced plan. **Select stray bits**
finds 55 of them in 14 clusters, nearly all letters of the captions. Dropping the thinnest
wall to 1 px found 488 walls and covered 93%, but needed much more cleanup.

## Keyboard shortcuts

Ctrl+K shows all of these, searchable.

| Key | Action |
| --- | --- |
| V, W, D, N, C, P, X, T, E, K, M, F | Select, Wall, Door, Window, Counter, Outline, Fixture, Label, Erase, Scale, Measure, Flag |
| Ctrl+O | Open a plan, folder, or image |
| Ctrl+S, Ctrl+Shift+S | Save, save as the next step |
| PageUp, PageDown | Go up or down a floor |
| Ctrl+E | Export a JPEG of every floor, stacked |
| Ctrl+Z, Ctrl+Y (or Ctrl+Shift+Z) | Undo, redo |
| Ctrl+A | Select everything |
| Delete or Backspace | Delete the selection |
| Ctrl+D | Duplicate the selection |
| Ctrl+C, Ctrl+V | Copy, paste at the pointer |
| Enter | Type the selection's size (or a flag's note); finish an outline or a run of walls |
| M | Measure when nothing is selected; move the selection by an exact distance when something is |
| Arrow keys | Nudge the selection 1″ (Shift: 12″, Alt: ¼″) |
| Digits while drawing | Type the next wall length, a counter's size, or a door's width |
| R | Rotate the selected fixture 90° (Shift+R the other way), or the next fixture to place |
| S, H | Flip the selected door's swing or hinge side |
| Shift+F | Flag the selection |
| Ctrl+/ | Comment on the selected object |
| Shift+C | Convert the selection to a wall, door, window, fixture, counter, label, or erase region |
| J, Shift+J | Merge the selected walls into one; merge every group of stacked walls |
| Shift+T | Set the selected walls' thickness |
| I, Ctrl+Shift+C | Show or hide object ids; copy the selected ids |
| Esc | Cancel the drawing in progress; press again to go back to Select, then again to clear the selection |
| Z | Zoom to the selection |
| 0 | Fit the whole sheet in view |
| + and - | Zoom in and out |
| Ctrl+drag, Shift+drag | Select with a box; add a box to the selection (Select tool) |
| Drag empty space | Pan (Select tool) |
| Space + drag, middle-drag | Pan with any tool |
| Shift+D | Show or hide measurements on the selection |
| \ | Show or hide the side panel |
| Ctrl+K or ? | Every command, searchable |
| F1 | Expand the tooltip's Learn more |

## Plan file format

Coordinates are inches. X increases to the right and Y increases downward, the same as
the sheet image. All lists are optional, and every object except a label has an `id` that
is unique across the whole plan.

```json
{
  "name": "Sample flat",
  "notes": "Free text.",
  "units": "inches",
  "compass": { "up": "north", "right": "east", "down": "south", "left": "west" },
  "canvas": { "width_px": 1024, "height_px": 640, "px_per_inch": 1.0, "origin_px": [0, 0] },
  "walls": [
    { "id": "w1", "x1": 0, "y1": 0, "x2": 240, "y2": 0, "thickness": 5,
      "comments": [{ "id": "c1", "text": "Shaft wall; check the thickness.", "by": "user", "created": "2026-10-13T15:21:33+00:00" }] }
  ],
  "openings": [
    { "id": "door-1", "wall": "w1", "type": "door", "offset": 30, "width": 32, "hinge": "start", "swing": "ccw" },
    { "id": "win-1", "wall": "w1", "type": "window", "offset": 120, "width": 48 }
  ],
  "counters": [
    { "id": "island", "x": 60, "y": 60, "w": 72, "h": 30, "overhang": { "edge": "south", "inches": 12 } },
    { "id": "kitchen-l", "points": [[0, 0], [100, 0], [100, 24], [24, 24], [24, 80], [0, 80]] }
  ],
  "fixtures": [
    { "id": "wc", "type": "toilet", "x": 200, "y": 20, "w": 20, "h": 28, "rot": 90 }
  ],
  "labels": [
    { "text": "PAN", "x": 150, "y": 100, "size_px": 10 }
  ],
  "erase": [
    { "id": "erase-1", "x": 180, "y": 90, "w": 50, "h": 26 }
  ]
}
```

- **compass** says which direction each side of the sheet faces. It must be a clockwise
  rotation of north, east, south, west.
- **canvas** ties inches to sheet pixels: pixel = `origin_px + inches × px_per_inch`.
  `width_px` and `height_px` are the sheet's size.
- **walls** are center lines with a thickness. `"kind": "glass_rail"` makes one a glass
  railing, drawn as a thin outline with a line down the middle. In the editor, select
  walls and tick Glass rail in the side panel.
- **openings** sit on a wall. `offset` is the distance from the wall's start `(x1, y1)` to
  the near edge of the opening. Doors hinge at the `start` or `end` side of the opening and
  swing `cw` or `ccw`. Openings must fit on the wall and not overlap each other.
- **counters** are either a box (`x`, `y`, `w`, `h`, with an optional overhang on one
  compass edge) or an outline of at least three `points`.
- **fixtures** are boxes centered on `x`, `y`, with a `type` and an optional rotation `rot`
  in degrees, turned clockwise on screen about that center.
- **labels** are text centered at `x`, `y`. `size_px` is the font size on the sheet.
- **erase** regions are boxes that white out the sheet under them in the editor and in
  overlays.
- **comments** can be added to any object. Each has an `id` (unique across the plan), `text`,
  and optionally `by` (`user` or `cursor`), `created`, and `edited` (ISO times). They aren't
  drawn in saved pictures.
- **sheet** (optional) names the plan's sheet image in its folder.

A building wraps several such plans, listed bottom floor first. Each floor also has an
`id` (unique in the file) and usually its own `sheet`:

```json
{
  "name": "Chestnut Corner",
  "floors": [
    { "id": "floor-1", "name": "Floor 1", "sheet": "sheet.png", "units": "inches", "compass": {}, "canvas": {}, "walls": [] },
    { "id": "floor-2", "name": "Floor 2", "sheet": "floor-2.png", "units": "inches", "compass": {}, "canvas": {}, "walls": [] }
  ]
}
```

(`compass` and `canvas` are shortened here; each floor has full ones like above.) Floor
flags live in `flags.json` for the first floor and `flags-floor-N.json` for the others.

## Driving the editor from Cursor (MCP)

`floorplan mcp` is an [MCP](https://modelcontextprotocol.io) server that gives Cursor (or
any MCP client) full control of the editor. It talks to the running editor's API, so
Cursor's edits land in the same document your browser shows. An open browser updates
live: the plan redraws, the object Cursor touched is selected, and the status bar says
what Cursor did.

How a harness should behave — flags, comments, the selection, when to look, and when to
save — is [AGENTS.md](AGENTS.md). The server loads that file and gives it to the harness
as its instructions. The editor's About box links to the same page.

Add it to `~/.cursor/mcp.json` (on Chee's machine it's already there):

```json
{
  "mcpServers": {
    "floorplan-editor": {
      "command": "C:\\Users\\CheeC\\code\\.venv\\Scripts\\python.exe",
      "args": ["-m", "floorplan_editor", "mcp"]
    }
  }
}
```

With your own venv, point `command` at that venv's Python. To use a port other than
35667, add `"env": { "FLOORPLAN_URL": "http://127.0.0.1:9000" }`.

Then ask Cursor things like "open the editor on C:\plans\39C, delete the tub's front
wall, put a toilet there facing east, and save it as a step," or drop flags in the
browser and say "do what F1 and F2 say." The tools:

| Area | Tools |
| --- | --- |
| Editor | `editor_status`, `start_editor` (starts `floorplan serve` in the background if needed, or switches folders), `stop_editor` |
| Files and images | `open_anything` (a plan, folder, or image, recognized the same way as the browser's Open), `open_plan` (a name in the folder, or any plan JSON on disk), `import_file` (copy a plan or a sheet image into the folder), `new_plan_from_image` (with optional auto-trace) |
| Flags | `list_flags` (each flag's note, place, and what it sits on or near), `add_flag`, `update_flag` (note, done, position), `delete_flags` |
| Comments | `list_comments` (all, or filtered by objects, the selection, comment ids, text, or author; each with the object it's on), `add_comment` (on one object, or the one selected), `update_comment` (text, or move it to another object), `delete_comments` |
| Looking | `view_image` (overlay, sheet, plan alone, or any PNG in the folder, cropped to a region in inches or around a flag, with flags drawn on and commented objects tagged with their comment ids in purple; crops label every object's id, and `highlight` or the user's selection is outlined in blue), `get_plan`, `find_objects` (by position, region, type, or text), `get_objects` (full objects by id) |
| What you selected | `get_selection` (the objects selected in the browser, their box, and whether the walls run the same way); `control_browser` with `ids` selects objects for you to look at |
| Cleaning up a trace | `convert_objects` (ids or the selection into a wall, door, window, fixture, counter, outline, label, or erase region), `merge_walls`, `set_wall_thickness` (with the face that stays put), `find_stacked_walls` (and `apply` to merge them), `find_stray_bits` (and `delete`) |
| Editing | `add_object`, `update_object`, `delete_objects`, `move_object`, `move_wall_end`, `split_wall`, `edit_counter_outline`, `flip_door`, `rotate_fixture`, `set_plan_properties` (name, notes, compass, scale), `trace_walls` (merging stacked bars unless `merge_stacked` is off), and `apply_ops` for any batch of edit operations as one undo step |
| Floors | `floors` (list, switch, add from a plan or a traced image, rename, move, delete, rename the building), `export_jpeg` (every floor stacked in one picture) |
| History and saving | `undo`, `redo`, `revert`, `save` (overwrite, next step, a new name, or a new folder with every floor's sheet), `merge_steps` |
| The page | `control_browser`: open the page, select, zoom to an object or region, fit, switch tools, toggle the sheet, erase regions, snapping, grid, and side panel, or show a message |

Ids can be bare (`w12`, `toilet-1`) or kind-qualified (`walls:w12`, `labels:3`; labels have
no id, so they go by their place in the list). A typical cleanup: Cursor calls
`find_stray_bits`, looks at each cluster with `view_image(region=box, source="sheet")`, and
converts the room names to labels; then `find_stacked_walls` with `apply`; then, for each
doorway, `convert_objects(to="door", ids=[the two walls beside it])`.

Edits stay in memory until a save, just like in the browser. Tools that would throw away
unsaved edits (opening another file or folder, reverting, stopping) refuse unless you
pass `discard_unsaved`. Saving over `plan.json` while steps exist needs
`confirm_overwrite_base`.

`start_editor` runs the editor as its own background process; `stop_editor` ends it. Its
output goes to `floorplan-editor.log` in your temp folder.

## Command line

The same engine is available without the browser:

```powershell
floorplan validate plan.json
floorplan render plan.json [--out-dir DIR]
floorplan export plan.json [--out FILE.jpg]
floorplan overlay plan.json sheet.jpg [--out FILE] [--erase-from plan.json]
floorplan merge plan.json plan-step1.json plan-step2.json --out plan-merged.json
floorplan trace sheet.jpg --out plan.json --px-per-inch 1.03 --up south
floorplan serve [FOLDER] [--sheet IMAGE] [--port 35667] [--no-browser]
floorplan mcp
```

`export` draws every floor clean, stacked top floor first, in one JPEG; `render` of a
building writes the same stack as a PNG. `overlay` takes a one-floor plan.
`overlay --erase-from` takes the traced base plan and whites out the sheet wherever the
new plan removed or moved an object, in addition to the plan's own erase regions. `trace`
accepts the same `--dark`, `--min-thickness`, `--max-thickness`, and `--min-length`
settings as the editor's sliders, `--no-merge` keeps side-by-side bars as separate walls,
and `--no-walls` makes an empty plan.

## Development

```powershell
C:\Users\CheeC\code\.venv\Scripts\python.exe -m pytest
```

`tests/test_regression.py` tries every edit operation, the read-only API, and the colors in a saved picture, and fails if an operation is added or removed without a case. `tests/web/regression.test.mjs` covers snapping, axis lock, and clicking an object.

- The engine lives in `floorplan_editor/engine`: schema, rendering, overlays, merging,
  edit operations, step folders, and tracing. `analyze.py` finds stacked walls, stray bits,
  and doorway gaps; the `convert`, `merge_walls`, `merge_stacked`, and `set_wall_thickness`
  operations in `edit.py` use it.
- The FastAPI server is `floorplan_editor/server/app.py`. `/api/events` streams changes and
  UI commands to open browsers.
- The MCP server is `floorplan_editor/mcp_server.py`.
- The browser editor is plain JavaScript modules in `floorplan_editor/web`, with no build
  step. `app.js` holds the state, drawing, and pointer handling; `commands.js` is the one
  list of commands behind the shortcuts, the action bar, the right-click menu, and Ctrl+K;
  `panels.js` builds the side panel; `measure.js` works out the measurements shown on a
  selection; `open.js` handles Open and dropped files; `flags.js` draws and lists flags;
  `convert.js` has the Convert menu, merging, and thickness.
- `geom.js` and `measure.js` have their own tests under `tests/web`, which pytest runs
  through Node when it is installed.

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
