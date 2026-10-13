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

Point `floorplan serve` at a plan folder:

```powershell
floorplan serve C:\Users\CheeC\code\riverside-39c
```

It starts a server on http://127.0.0.1:8765 and opens your browser. Options:

- `--sheet PATH` picks the source image when the folder has more than one.
- `--port N` uses another port.
- `--no-browser` starts the server without opening a tab.

To try it without a plan of your own, run `floorplan serve examples/sample`.

### What goes in a plan folder

| File | What it is |
| --- | --- |
| `plan.json` | The base plan, usually traced from the sheet. Steps are measured against it. |
| `plan-step1.json`, `plan-step2.json`, ... | Saved ideas, each an edit of the base. |
| `plan-merged.json` | The result of merging steps. |
| any `.jpg` or `.png` not starting with `plan` | The source sheet. A file whose name starts with `sheet` wins if there are several. |

Each plan file is saved with a matching `.svg`, `.png`, and `-overlay.png`. The overlay
is only written when the sheet is the same pixel size as the plan's canvas.

The server holds your unsaved edits in memory, so reloading the page doesn't lose them.
Closing the server does.

## Tour

The toolbar runs across the top, the drawing fills the middle, and the side panel is on
the right. **Panel** (or `\`) hides the side panel to give the drawing more room. The status
bar along the bottom shows a hint for the current tool, the cursor position in feet and
inches, what the cursor snapped to, the zoom, and the open file (a dot means unsaved).

### Tools

- **Select (V).** Click an object to select it and see its fields in the side panel. Drag
  to move it. Drag a handle to reshape it.
  - Wall ends that meet another wall move together. Hold Alt to pull one free.
  - Shift locks a move to one axis.
  - Double-click a wall to split it in two.
  - Drag a counter outline's corner to move it. Drag the dot in the middle of an edge to add
    a corner there.
  - Drag empty space to pan. The mouse wheel zooms.
- **Wall (W).** Click each corner. Shift keeps a segment straight. Double-click,
  right-click, Enter, or Esc stops.
- **Door (D)** and **Window (N).** Click on a wall to drop one there. Drag it along the wall
  to move it, and drag its end dots to change the width. With a door selected, F flips the
  swing and H flips the hinge side.
- **Counter (C).** Drag a rectangle, or click two opposite corners, for a box counter.
- **Outline (P).** Click each corner of a counter of any shape, such as an L. Double-click
  or Enter closes it. Backspace removes the last corner.
- **Fixture (X).** Pick a type and a rotation in the toolbar, then click to place it. The
  click point becomes the fixture's `x`, `y` corner, which it rotates around. Types are refrigerator,
  range, oven, dishwasher, toilet, tub, soaking tub, sink, washer, dryer, washer/dryer,
  stool, and generic. Press R to rotate the next placement, or R on a selected fixture to
  turn it 90°.
- **Label (T).** Type the text in the toolbar box, then click where it goes. The text stays
  in the box, so you can drop the same label several times.
- **Erase (E).** Drag a rectangle, or click two corners, over printed ink on the sheet.
  The region whites out that ink in the editor and in every saved overlay. It's stored in
  the plan, so it can be moved, resized, or deleted later.
- **Scale (K).** Click two points a known distance apart, then type the real length, such
  as `26'5"`. This sets how many sheet pixels make an inch.

Snapping is on by default. Points snap to wall ends and wall faces, then to a 1-inch grid.
Both can be turned off under **View** in the side panel, which also controls the sheet
image, its opacity, and whether erase regions are shown.

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

### Starting a new plan from an image

Under **New plan from a sheet image** in the side panel, choose the image, say which
compass direction is up on the sheet, give a pixels-per-inch value if you know it, and
name the file. The editor copies the image into the folder as `sheet.png` (or `.jpg`) and
starts an empty plan the size of the image. Fix the scale with the Scale tool if you guessed.

**Auto-trace walls** then finds the long, dark, solid bars on the sheet and offers them as
walls:

1. Click **Preview** to see what it found, drawn over the sheet.
2. Adjust the sliders and preview again:
   - **Ink darker than** is the gray level (0 to 255) that counts as ink.
   - **Thinnest wall** and **Thickest wall** set the bar thickness range, in pixels. Raise
     the thinnest to skip window and cabinet lines. Lower it to pick up thin partitions.
   - **Shortest wall** drops short bars, in pixels.
3. Click **Apply** to add the walls. **Replace existing walls** clears the old ones first.
4. Clean up by hand. Expect to delete stray bars around text and fixtures, join corners,
   and add the thin walls the tracer skipped.

On the 220 Riverside 39C sheet, the default settings found 154 walls. They covered at least
80% of the length of 84% of the thick walls in the hand-traced plan. Dropping the thinnest
wall to 1 px found 488 walls and covered 93%, but needed much more cleanup.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| V, W, D, N, C, P, X, T, E, K | Select, Wall, Door, Window, Counter, Outline, Fixture, Label, Erase, Scale |
| Delete or Backspace | Delete the selection |
| Ctrl+Z, Ctrl+Y (or Ctrl+Shift+Z) | Undo, redo |
| Ctrl+S | Save |
| Esc | Cancel the drawing in progress; press again to go back to Select, then again to clear the selection |
| Arrow keys | Nudge the selection 1″ (Shift: 12″, Alt: ¼″) |
| Enter | Finish an outline or a run of walls |
| R | Rotate the selected fixture 90° (Shift+R the other way), or the next fixture to place |
| F, H | Flip the selected door's swing or hinge side |
| Z | Zoom to the selection |
| 0 | Fit the whole sheet in view |
| + and - | Zoom in and out |
| Space + drag | Pan with any tool |
| \ | Show or hide the side panel |

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
    { "id": "w1", "x1": 0, "y1": 0, "x2": 240, "y2": 0, "thickness": 5 }
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
- **walls** are center lines with a thickness.
- **openings** sit on a wall. `offset` is the distance from the wall's start `(x1, y1)` to
  the near edge of the opening. Doors hinge at the `start` or `end` side of the opening and
  swing `cw` or `ccw`. Openings must fit on the wall and not overlap each other.
- **counters** are either a box (`x`, `y`, `w`, `h`, with an optional overhang on one
  compass edge) or an outline of at least three `points`.
- **fixtures** are boxes with a `type` and an optional rotation `rot` in degrees, turned
  clockwise on screen about the `x`, `y` corner.
- **labels** are text centered at `x`, `y`. `size_px` is the font size on the sheet.
- **erase** regions are boxes that white out the sheet under them in the editor and in
  overlays.

## Driving the editor from Cursor (MCP)

`floorplan mcp` is an [MCP](https://modelcontextprotocol.io) server that gives Cursor (or
any MCP client) full control of the editor. It talks to the running editor's API, so
Cursor's edits land in the same document your browser shows. An open browser updates
live: the plan redraws, the object Cursor touched is selected, and the status bar says
what Cursor did.

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
8765, add `"env": { "FLOORPLAN_URL": "http://127.0.0.1:9000" }`.

Then ask Cursor things like "open the editor on C:\plans\39C, delete the tub's front
wall, put a toilet there facing east, and save it as a step." The tools:

| Area | Tools |
| --- | --- |
| Editor | `editor_status`, `start_editor` (starts `floorplan serve` in the background if needed, or switches folders), `stop_editor` |
| Files and images | `open_plan` (a name in the folder, or any plan JSON on disk), `import_file` (copy a plan or a sheet image into the folder), `new_plan_from_image` (with optional auto-trace) |
| Looking | `view_image` (overlay, sheet, plan alone, or any PNG in the folder, cropped to a region in inches), `get_plan`, `find_objects` (by position, region, type, or text) |
| Editing | `add_object`, `update_object`, `delete_objects`, `move_object`, `move_wall_end`, `split_wall`, `edit_counter_outline`, `flip_door`, `rotate_fixture`, `set_plan_properties` (name, notes, compass, scale), `trace_walls`, and `apply_ops` for any batch of edit operations as one undo step |
| History and saving | `undo`, `redo`, `revert`, `save` (overwrite, next step, or a new name), `merge_steps` |
| The page | `control_browser`: open the page, select, zoom to an object or region, fit, switch tools, toggle the sheet, erase regions, snapping, grid, and side panel, or show a message |

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
floorplan overlay plan.json sheet.jpg [--out FILE] [--erase-from plan.json]
floorplan merge plan.json plan-step1.json plan-step2.json --out plan-merged.json
floorplan trace sheet.jpg --out plan.json --px-per-inch 1.03 --up south
floorplan serve [FOLDER] [--sheet IMAGE] [--port 8765] [--no-browser]
floorplan mcp
```

`overlay --erase-from` takes the traced base plan and whites out the sheet wherever the
new plan removed or moved an object, in addition to the plan's own erase regions. `trace`
accepts the same `--dark`, `--min-thickness`, `--max-thickness`, and `--min-length`
settings as the editor's sliders, and `--no-walls` makes an empty plan.

## Development

```powershell
C:\Users\CheeC\code\.venv\Scripts\python.exe -m pytest
```

- The engine lives in `floorplan_editor/engine`: schema, rendering, overlays, merging,
  edit operations, step folders, and tracing.
- The FastAPI server is `floorplan_editor/server/app.py`. `/api/events` streams changes and
  UI commands to open browsers.
- The MCP server is `floorplan_editor/mcp_server.py`.
- The browser editor is plain JavaScript modules in `floorplan_editor/web`, with no build
  step.

## License

MIT
