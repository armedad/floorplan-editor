"""Combine independent step plans that were each built from the same base."""

from __future__ import annotations

import copy
import json

from .model import ID_KEYS, PlanError


def merge_plans(base: dict, steps: list[tuple[str, dict]]) -> dict:
    """Apply each step's changes relative to base. Stop on two steps changing one object differently."""
    merged = copy.deepcopy(base)
    errors: list[str] = []
    owner: dict[tuple[str, str], str] = {}
    for name, step in steps:
        for key in ID_KEYS:
            before = {item["id"]: item for item in base.get(key, [])}
            after = {item["id"]: item for item in step.get(key, [])}
            current = {item["id"]: item for item in merged.get(key, [])}
            for item_id in sorted(set(before) | set(after)):
                old, new = before.get(item_id), after.get(item_id)
                if old == new:
                    continue
                prior = owner.get((key, item_id))
                if prior is not None and current.get(item_id) != new:
                    errors.append(f"{key} {item_id}: {prior} and {name} change it differently")
                    continue
                owner[(key, item_id)] = name
                if new is None:
                    current.pop(item_id, None)
                else:
                    current[item_id] = copy.deepcopy(new)
            order = [item["id"] for item in merged.get(key, [])]
            order += [item_id for item_id in current if item_id not in order]
            merged[key] = [current[item_id] for item_id in order if item_id in current]

        base_labels = [json.dumps(label, sort_keys=True) for label in base.get("labels", [])]
        step_labels = [json.dumps(label, sort_keys=True) for label in step.get("labels", [])]
        merged_labels = [json.dumps(label, sort_keys=True) for label in merged.get("labels", [])]
        for text in base_labels:
            if text not in step_labels and text in merged_labels:
                merged_labels.remove(text)
        for text in step_labels:
            if text not in base_labels and text not in merged_labels:
                merged_labels.append(text)
        merged["labels"] = [json.loads(text) for text in merged_labels]

    if errors:
        raise PlanError(errors)
    for key in ID_KEYS + ("labels",):
        if not merged.get(key):
            merged.pop(key, None)
    return merged
