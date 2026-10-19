"""Flags: numbered markers on the sheet that people and agents use to point at places.

Flags live in the plan folder's flags.json, apart from the plan files, so they survive
steps, merges, and undo, and never show up in rendered plans. Each flag is stored in
sheet pixels; recalibrating the scale changes its inches but not where it sits.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FLAGS_NAME = "flags.json"
FIRST_FLOOR = "floor-1"


def flags_name(floor_id: str | None) -> str:
    """Each floor of a building keeps its own flags; the first floor shares flags.json with the
    one-floor plan it grew from."""
    if not floor_id or floor_id == FIRST_FLOOR:
        return FLAGS_NAME
    return f"flags-{floor_id}.json"


class FlagError(ValueError):
    pass


class FlagStore:
    def __init__(self, folder: Path, name: str = FLAGS_NAME):
        self.path = Path(folder) / name
        self.flags: list[dict] = []
        self.next = 1
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.flags = [f for f in data.get("flags", []) if isinstance(f, dict) and "id" in f]
                self.next = int(data.get("next", len(self.flags) + 1))
            except (ValueError, TypeError, AttributeError):
                self.flags, self.next = [], 1

    def save(self) -> None:
        if not self.flags and not self.path.exists():
            return
        self.path.write_text(json.dumps({"next": self.next, "flags": self.flags}, indent=2), encoding="utf-8")

    def get(self, flag_id: str) -> dict:
        for flag in self.flags:
            if flag["id"].lower() == str(flag_id).lower():
                return flag
        raise FlagError(f"no flag {flag_id!r}")

    def add(self, px: list[float], *, size: list[float] | None = None, note: str = "", target=None, by: str = "user", rule: str | None = None) -> dict:
        flag = {
            "id": f"F{self.next}",
            "px": [round(float(px[0]), 2), round(float(px[1]), 2)],
            "note": note.strip(),
            "done": False,
            "by": by,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if rule:
            flag["rule"] = str(rule)
        if size and float(size[0]) > 0 and float(size[1]) > 0:
            flag["size"] = [round(float(size[0]), 2), round(float(size[1]), 2)]
        if target:
            flag["target"] = list(target)
        self.next += 1
        self.flags.append(flag)
        self.save()
        return flag

    def update(self, flag_id: str, **fields) -> dict:
        flag = self.get(flag_id)
        if fields.get("note") is not None:
            flag["note"] = str(fields["note"]).strip()
        if fields.get("done") is not None:
            flag["done"] = bool(fields["done"])
        if fields.get("px") is not None:
            flag["px"] = [round(float(v), 2) for v in fields["px"]]
        if "target" in fields:
            if fields["target"]:
                flag["target"] = list(fields["target"])
            else:
                flag.pop("target", None)
        self.save()
        return flag

    def delete(self, ids: list[str]) -> list[str]:
        gone = [self.get(i)["id"] for i in ids]
        self.flags = [f for f in self.flags if f["id"] not in gone]
        self.save()
        return gone

    def clear_done(self) -> list[str]:
        return self.delete([f["id"] for f in self.flags if f.get("done") and f.get("by") != "pinned"])

    def listing(self, canvas: dict | None) -> list[dict]:
        """Flags with their place in plan inches added, when a plan gives the scale."""
        out = []
        for flag in self.flags:
            item = dict(flag)
            if canvas:
                scale = float(canvas["px_per_inch"])
                ox, oy = canvas["origin_px"]
                item["x"] = round((flag["px"][0] - ox) / scale, 2)
                item["y"] = round((flag["px"][1] - oy) / scale, 2)
                if "size" in flag:
                    item["w"] = round(flag["size"][0] / scale, 2)
                    item["h"] = round(flag["size"][1] / scale, 2)
            out.append(item)
        return out
