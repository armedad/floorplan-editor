"""Command line: validate, render, export, overlay, merge, trace, serve, mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import PlanError, checked, is_building, load_plan, overlay_file, render_files, save_plan
from .engine.building import export_image, merge_any


def _emit_errors(exc: PlanError) -> int:
    for line in exc.errors:
        print(line, file=sys.stderr)
    return 1


def _cmd_merge(args) -> int:
    try:
        base = checked(load_plan(args.base))
        steps = [(path.name, checked(load_plan(path))) for path in args.steps]
        merged = merge_any(base, steps)
        merged["name"] = args.name or merged.get("name", "merged")
        merged["notes"] = "Merged from " + ", ".join(path.name for path in args.steps) + f" on base {args.base.name}."
        checked(merged)
    except PlanError as exc:
        return _emit_errors(exc)
    save_plan(merged, args.out)
    print(args.out)
    return 0


def _cmd_trace(args) -> int:
    from .engine.trace import TraceSettings, new_plan_from_image

    settings = TraceSettings(
        dark=args.dark,
        min_thickness_px=args.min_thickness,
        max_thickness_px=args.max_thickness,
        min_length_px=args.min_length,
        merge=not args.no_merge,
    )
    plan = new_plan_from_image(
        args.image,
        up=args.up,
        px_per_inch=args.px_per_inch,
        trace=not args.no_walls,
        settings=settings,
    )
    try:
        checked(plan)
    except PlanError as exc:
        return _emit_errors(exc)
    save_plan(plan, args.out)
    print(f"{args.out} ({len(plan.get('walls', []))} walls)")
    return 0


def _cmd_serve(args) -> int:
    from .server.app import serve

    serve(args.folder, sheet=args.sheet, port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="floorplan", description="Validate, render, overlay, merge, trace, or edit floor plan JSON files."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate_cmd = sub.add_parser("validate", help="check a plan file")
    validate_cmd.add_argument("plan", type=Path)

    render_cmd = sub.add_parser("render", help="write <name>.svg and <name>.png (a building: every floor stacked in <name>.png)")
    render_cmd.add_argument("plan", type=Path)
    render_cmd.add_argument("--out-dir", type=Path)

    export_cmd = sub.add_parser("export", help="write <name>-export.jpg: every floor drawn clean, stacked top floor first")
    export_cmd.add_argument("plan", type=Path)
    export_cmd.add_argument("--out", type=Path)

    overlay_cmd = sub.add_parser("overlay", help="draw the plan in red over the source sheet")
    overlay_cmd.add_argument("plan", type=Path)
    overlay_cmd.add_argument("source", type=Path)
    overlay_cmd.add_argument("--out", type=Path)
    overlay_cmd.add_argument(
        "--erase-from",
        type=Path,
        help="plan traced from the source; objects the plan removed or changed are whited out on the source",
    )

    merge_cmd = sub.add_parser("merge", help="combine steps built from the same base")
    merge_cmd.add_argument("base", type=Path)
    merge_cmd.add_argument("steps", type=Path, nargs="+")
    merge_cmd.add_argument("--out", type=Path, required=True)
    merge_cmd.add_argument("--name")

    trace_cmd = sub.add_parser("trace", help="start a plan from a sheet image, tracing its walls")
    trace_cmd.add_argument("image", type=Path)
    trace_cmd.add_argument("--out", type=Path, required=True)
    trace_cmd.add_argument("--px-per-inch", type=float, default=1.0)
    trace_cmd.add_argument("--up", default="north", choices=["north", "east", "south", "west"])
    trace_cmd.add_argument("--dark", type=int, default=110, help="gray level below which a pixel is ink (0-255)")
    trace_cmd.add_argument("--min-thickness", type=int, default=3, help="thinnest wall, in pixels")
    trace_cmd.add_argument("--max-thickness", type=int, default=24, help="thickest wall, in pixels")
    trace_cmd.add_argument("--min-length", type=int, default=12, help="shortest wall, in pixels")
    trace_cmd.add_argument("--no-walls", action="store_true", help="create an empty plan sized to the image")
    trace_cmd.add_argument("--no-merge", action="store_true", help="keep bars side by side as separate thin walls")

    serve_cmd = sub.add_parser("serve", help="open the browser editor on a plan folder")
    serve_cmd.add_argument("folder", type=Path, nargs="?", default=Path.cwd())
    serve_cmd.add_argument("--sheet", type=Path, help="source sheet image (default: the image in the folder)")
    serve_cmd.add_argument("--port", type=int, default=35667)
    serve_cmd.add_argument("--no-browser", action="store_true")

    sub.add_parser("mcp", help="run the MCP server (stdio) that lets Cursor drive the editor")

    args = parser.parse_args(argv)

    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    if args.cmd == "merge":
        return _cmd_merge(args)
    if args.cmd == "trace":
        return _cmd_trace(args)
    if args.cmd == "serve":
        return _cmd_serve(args)

    try:
        plan = checked(load_plan(args.plan))
    except PlanError as exc:
        return _emit_errors(exc)

    if args.cmd == "validate":
        print("OK")
        return 0

    stem = args.plan.stem
    if args.cmd == "export":
        out = args.out or args.plan.with_name(f"{stem}-export.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        export_image(plan).save(out, "JPEG", quality=92)
        print(out)
        return 0
    if args.cmd == "render":
        out_dir = args.out_dir or args.plan.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        png_path = out_dir / f"{stem}.png"
        if is_building(plan):
            export_image(plan).save(png_path)
            print(png_path)
            return 0
        svg_path = out_dir / f"{stem}.svg"
        render_files(plan, svg_path, png_path)
        print(svg_path)
        print(png_path)
        return 0

    if is_building(plan):
        print("overlay draws one floor; open the building in the editor (floorplan serve) to see each floor's overlay", file=sys.stderr)
        return 1
    out = args.out or args.plan.with_name(f"{stem}-overlay.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        erase_from = checked(load_plan(args.erase_from)) if args.erase_from else None
        overlay_file(plan, args.source, out, erase_from)
    except PlanError as exc:
        return _emit_errors(exc)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
