# Using Floor Plan-It with an AI harness

You and the person share one plan. They draw in the browser. You change that same plan through the floorplan-editor tools (`floorplan mcp`). The browser redraws as you go, selects what you touched, and shows a short note in the status bar. Change the open plan through the tools so the browser stays in step with you.

The tool names and their arguments are in the README, under "Driving the editor from Cursor (MCP)". This file is how to behave. The server loads it and hands it to the harness.

## How they point

They point, and you look. Three ways:

- **Flags.** They press F, click a spot or drag a box, and write a note ("this should be a door, not a wall"). Then they say "look at F3" or "fix what I flagged." Call `list_flags`. For each open flag, read the note and what it sits on, and call `view_image` with `flag` set to that id before you change anything. Pass `include_done` only when they ask about flags already handled. When the note is satisfied and you have looked at the result, call `update_flag` with `done` true. Leave a flag open when the note is not satisfied, and say what is still wrong.
- **Comments.** A comment stays on an object. "See the comments" means `list_comments`. Use a comment for a lasting note. Use a flag for a task that can be marked done.
- **The selection.** "This" and "what I selected" mean `get_selection`.

They can hide the flag pins on the drawing. The flags are still there. Trust `list_flags`.

A cropped `view_image` writes every object's id on it. With `show_selection`, their selection is outlined. `source` `overlay` is the plan in red on the sheet. That picture is how you check the work. `sheet` is the source image. `plan` is the drawing alone.

## What to do when they ask

1. Call `editor_status`. If nothing is open, call `start_editor` with their plan folder, or `open_anything` with a plan, a folder, or a floor plan image.
2. Look first. Crop `view_image` to the flag, the comment, or the selection.
3. Make the change. When several operations are one change, send them in one `apply_ops` call, so one undo puts them back.
4. Look again at the overlay. Mark a flag done only after that look shows the note is satisfied.
5. Stop when that request is done. They will look and flag whatever is still off.

Save only when they ask. `save` with mode `step` writes the next `plan-stepN.json`. Overwriting `plan.json` while other steps exist needs `confirm_overwrite_base`. Opening another file, reverting, or stopping, when the plan has unsaved edits, needs `discard_unsaved`. Pass that only when they told you to throw the edits away.

## The drawing

Coordinates are inches. X grows right and y grows down, the same way as the sheet. Read `compass` from the plan and keep the sheet's orientation. When the sheet has no compass rose, leave the plan's "up" as it is.

The sheet image is the source. Leave it in place. To hide printed ink that was never traced, add an erase region.

Set the scale from a length printed on the sheet, then check a second printed length. Those figures are the drawing's scale.

A trace comes out as walls. Door swings, fixture symbols, and the letters of room names come out as short walls, and a thick wall often comes out as two or three thin bars.

- `find_stray_bits` finds the scraps. Look at each cluster on the sheet, turn room names into labels, and delete the rest.
- `find_stacked_walls` with `apply` joins thin bars that are one thick wall.
- `convert_objects` turns pieces into a door, window, fixture, counter, label, or erase region. A gap between two walls in line becomes one wall with the opening filling the gap.
- `set_wall_thickness` keeps the face you name where it is.

Opening types are door and window. A sliding door or an overhead garage door is a wide window. A door has a hinge and a swing; `flip_door` changes one of them. Check the overlay to see which way it opens.

Ids look like `w12` or `toilet-1`. A label has no id of its own; it is `labels:3`. Either form is accepted.

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
