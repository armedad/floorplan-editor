# Using Floor Plan-It with an AI harness

You and the person share one plan. They draw in the browser. You change that same plan through the floorplan-editor tools (`floorplan mcp`). The browser redraws as you go, selects what you touched, and shows a short note in the status bar. Change the open plan through the tools so the browser stays in step with you.

The tool names and their arguments are in the README, under "Driving the editor from Cursor (MCP)". This file is how to behave. The server loads it and hands it to the harness.

The editor holds the plan in memory. The `plan.json` on disk is stale until a save. Read the live plan with `get_plan`. Do not write `plan.json` on disk while the editor has that file open.

## How they point

They point, and you look. Three ways:

- **Flags.** They press F, click a spot or drag a box, and write a note ("this should be a door, not a wall"). Then they say "look at F3" or "fix what I flagged." Call `list_flags`. For each open flag, read the note and what it sits on, and call `view_image` with `flag` set to that id before you change anything. Pass `include_done` only when they ask about flags already handled. When the note is satisfied and you have looked at the result, call `update_flag` with `done` true in that same pass. Do not wait to be asked. Leave a flag open when the note is not satisfied, and say what is still wrong.
- **Comments.** A comment stays on an object. "See the comments" means `list_comments`. Use a comment for a lasting note. Use a flag for a task that can be marked done.
- **The selection.** "This" and "what I selected" mean `get_selection`.

They can hide the flag pins on the drawing. The flags are still there. Trust `list_flags`.

A cropped `view_image` writes every object's id on it. With `show_selection`, their selection is outlined. `source` `overlay` is the plan in red on the sheet. That picture is how you check the work. `sheet` is the source image. `plan` is the drawing alone.

## What to do when they ask

1. Call `editor_status`. If nothing is open, call `start_editor` with their plan folder, or `open_anything` with a plan or a folder. For a new sheet image, follow "Tracing a sheet" below. Do not let `open_anything` auto-trace a dimensioned sheet.
2. Look first. Crop `view_image` to the flag, the comment, or the selection.
3. Make the change. When several operations are one change, send them in one `apply_ops` call, so one undo puts them back.
4. Look again at the overlay, with the new object highlighted. Mark a flag done only after that look shows the note is satisfied.
5. Stop when that request is done. They will look and flag whatever is still off.

Save only when they ask. `save` with mode `step` writes the next `plan-stepN.json`. Overwriting `plan.json` while other steps exist needs `confirm_overwrite_base`. Opening another file, reverting, or stopping, when the plan has unsaved edits, needs `discard_unsaved`. Pass that only when they told you to throw the edits away.

## The drawing

Coordinates are inches. X grows right and y grows down, the same way as the sheet. Read `compass` from the plan and keep the sheet's orientation. Set `up` from the north arrow on the sheet. When the sheet has no compass rose, leave the plan's "up" as it is.

The sheet image is the source. Leave it in place. To hide printed ink that was never traced, add an erase region. Do not paint over the sheet, and do not generate a replacement drawing.

Set the scale from a length printed on the sheet, then check a second printed length. Those figures are the drawing's scale, not a survey. On a sheet with a graphic scale bar, measure center to center of the end ticks and divide by the labeled length. Do not take the scale from wall thickness. On a double-line sheet the solid bars are the two faces closed up, and that thickness is not a wall.

The folder's sheet is the first image whose name does not start with `plan` or `_`. A crop or a preview left in the folder becomes the sheet. Keep only the sheet image and the plan files in that folder.

Ids look like `w12` or `toilet-1`. A label has no id of its own; it is `labels:3`. Either form is accepted.

## Tracing a sheet

A permit or construction sheet is not a listing plan. The Bellevue lower floor is the sheet that showed this. Its walls are two thin black lines a few pixels apart, with white between them. Dimension lines, ticks, and the scale bar are thin too. The tracer only keeps long solid bars. It never emits a door or a window.

Do not open that kind of sheet through `open_anything`. That call traces with the default settings, guesses the scale from bar thickness, and saves `plan.json`. The guess is wrong, and the default trace either keeps the dimension lines or drops the building.

`trace_walls` with `apply` false previews a count. Use it before applying. What the settings do on a double-line sheet:

- Raising `min_thickness_px` until the dimension lines disappear also drops the walls. The walls are hairlines. A minimum of 2 keeps the measurement lines. A minimum of 4 or more finds almost nothing.
- Closing the white gap with a dilate joins the dimension lines into thick bars. Do not do that.
- The cavity between the two faces is the wall. Pair parallel hairlines whose separation is a wall thickness, which overlap along most of the shorter line, and emit one wall on the centerline, as thick as that separation. Drop a pair that already sits under a solid wall.

Leave out of the trace: dimension lines, extension lines, ticks, foot-and-inch text, room names, notes, door numbers, stair treads, revision clouds, the title block, the north arrow, and the scale bar. Stair treads are a stack of short parallel lines. The scale bar and the north arrow can fall inside the page and still get traced. Delete them by where they sit, after you have looked.

After the walls are in, look at the overlay. Red sits on the heavy lines only. A heavy double line with no red was missed. Red on a word or a dimension string gets deleted.

Then do openings, then walk the rooms, then the fixtures. A plan that stops at the walls is a house with no way into the rooms.

## Doors and windows

A door is a gap in a wall with a quarter-circle swing. A window is a flat break in an outside wall: no swing, one lighter line across the gap for the glass, and often a hexagon on the dimension string outside the break. Put an opening on the wall. Do not trace the arc, the glass line, the hexagon, or the circled number as a wall. The circled number is a tag.

The tracer will not find these. Searching only for a gap between two traced walls misses most of them. One jamb is often a few inches of wall, shorter than the tracer keeps, so the swing hangs off the end of the longer piece. Extend that wall across the swing (`move_wall_end`), then put the door on it. A closet drawn with lighter lines may not have been traced at all. Trace those walls, then put the door on them.

The hinge is the center of the quarter circle, and it lies on the wall. The radius is the width. The arc says which side it opens. `offset` is inches from the wall's start `(x1, y1)`. `offset` plus `width` must fit on the wall. If the opening is a gap between two collinear walls, `merge_walls` first, then measure the offset on the merged wall. A later merge that moves the start shifts every offset on that wall. Check the wall ends before trusting an offset you stored earlier.

Y grows down, so a clockwise swing is clockwise on the sheet. On a wall that runs to the right, a swing toward the bottom of the sheet is `cw` when the hinge is at the start, and a swing toward the top is `ccw`. `hinge` `end` reverses the closed leaf, so the clockwise side flips with it. `flip_door` changes swing or hinge. A closet door swings out of the closet. If the arc is inside the closet, the hinge is on the wrong end.

A double door is two doors in one opening, hinged at the outer jambs, swinging the same way into the room. A sliding door or an overhead garage door is a wide window.

Window glass is lighter than the wall ink, so a wall-ink threshold misses it. Look for the hexagon. If both faces of the wall continue through the break, it is a trace gap, not an opening. Join it. A window spans the whole glass, not a short piece of it.

Keep an opening only when the overlay shows it on the printed mark. Highlight the new id and crop tight. The red arc has to lie on the printed quarter circle. The red window has to sit in the glass break. Flip the swing if the side is wrong. Delete the opening if it lands on a dimension line, a stair, a note, or a crawl-access hatch. A crawl-access note is not a door.

Do not trust a pixel score alone. Dark pixels near an ideal arc also fall on dimension ticks, notes, and stair treads. Fitting a circle to the arc usually fails: the flat part of a quarter circle looks like a straight run, and the door-number circle ruins the fit. A description of a busy crop often says the mark is on the dimension text when it is on the wall. Look at a tighter overlay before you delete a real door or keep a false one.

## Rooms

After the walls and openings are in, walk every room.

A bedroom, bath, closet, office, or utility room is closed: walls all the way around, and every way in is a door. An outside wall of a finished room is not a long blank run. The breaks are windows or doors. A hall, a stair, or an open play room can stay open. Crawl space and knee space are not rooms, so do not put a door on a crawl-access note, but do close the walls the sheet draws around them. Do not stop while a closed room is still open. Do not invent a wall the sheet does not draw.

## What is not a wall

These get traced and should come back out: the edge of a tub, a stair tread, a closet rod, a dimension tick, a door tag. Put the fixture on the symbol instead. A full bath gets a tub, a toilet, and a sink. A half bath gets a toilet and a sink on a counter. Built-in cabinets are counters. A diagonal fireplace is a wall along the printed diagonal. There is no fireplace fixture type.

`find_stray_bits` finds traced letters and symbol scraps. Look at each cluster on the sheet, turn room names into labels, and delete the rest. `find_stacked_walls` with `apply` joins thin bars that are one thick wall. `convert_objects` turns pieces into a door, window, fixture, counter, label, or erase region. `set_wall_thickness` keeps the face you name where it is.

## Flags, again

Work the open flags one at a time. Read the note, look at the flag on the overlay, make the change, look again.

- The note is satisfied only when the overlay shows the fix. A door flag is satisfied when the red arc lies on the printed swing. A window flag is satisfied when the opening covers the glass. A "not a wall" flag is satisfied when that object is gone and, if it was a fixture, the fixture is on the symbol. A "walls not closed" flag is satisfied when the walls meet.
- Mark it done then, with `update_flag` and `done` true. Same pass as the fix.
- If the fix is in the right place but does not sit on the ink, leave the flag open and say so. Do not mark a guess done.

## Connecting a harness

Run the server over stdio:

```json
{
  "mcpServers": {
    "floorplan-editor": {
      "command": "python",
      "args": ["-m", "floorplan_editor", "mcp"]
    }
  }
}
```

Point `command` at the Python that has the package installed. The server talks to the editor at `http://127.0.0.1:35667`. Set `FLOORPLAN_URL` to use another port.

The plan folder is their drawing. This repository is the editor. Leave their plan out of this repo.
