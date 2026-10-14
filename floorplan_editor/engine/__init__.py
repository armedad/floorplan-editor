"""Floor plan engine: schema, rendering, overlays, merging, tracing, and edit operations."""

from .model import PlanError, checked, is_building, load_plan, save_plan, validate_plan
from .render import Canvas, render_files, scene, to_svg, draw_png
from .overlay import apply_erase_regions, overlay_file
from .merge import merge_plans

__all__ = [
    "PlanError",
    "Canvas",
    "apply_erase_regions",
    "checked",
    "draw_png",
    "is_building",
    "load_plan",
    "merge_plans",
    "overlay_file",
    "render_files",
    "save_plan",
    "scene",
    "to_svg",
    "validate_plan",
]
