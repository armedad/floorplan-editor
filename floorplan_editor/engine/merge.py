"""Combine independent step plans that were each built from the same base."""

from __future__ import annotations

import copy
import json
import re

from .model import ID_KEYS, PlanError, comments_of


def _bare(item: dict | None) -> dict | None:
    return None if item is None else {k: v for k, v in item.items() if k != "comments"}


def _comments(item: dict | None) -> dict[str, dict]:
    return {c["id"]: c for c in (item or {}).get("comments") or [] if isinstance(c, dict) and "id" in c}


def _merge_comments(target: dict, old: dict | None, new: dict, where: str, name: str, owner: dict, errors: list[str]) -> None:
    """Apply one step's comment changes on an object (added, edited, removed) to the merged object."""
    before, after = _comments(old), _comments(new)
    current = list(target.get("comments") or [])
    for comment_id, comment in after.items():
        existing = next((c for c in current if c.get("id") == comment_id), None)
        if comment_id not in before:
            if comment not in current:
                current.append(copy.deepcopy(comment))
        elif comment != before[comment_id]:
            prior = owner.get(comment_id)
            if existing is None or (prior is not None and existing != comment):
                errors.append(f"comment {comment_id} on {where}: {prior or 'another step'} and {name} change it differently")
                continue
            owner[comment_id] = name
            current[current.index(existing)] = copy.deepcopy(comment)
    for comment_id in before.keys() - after.keys():
        current = [c for c in current if c.get("id") != comment_id]
    if current:
        target["comments"] = current
    else:
        target.pop("comments", None)


def _renumber_comments(plan: dict) -> None:
    """Steps number their comments independently; give repeats in the merged plan new ids."""
    taken = {str(c.get("id")) for *_, c in comments_of(plan)}
    n = max((int(m.group(1)) for t in taken if (m := re.fullmatch(r"c(\d+)", t))), default=0)
    seen: set[str] = set()
    for *_, comment in comments_of(plan):
        if comment.get("id") in seen:
            n += 1
            comment["id"] = f"c{n}"
        seen.add(comment["id"])


def merge_plans(base: dict, steps: list[tuple[str, dict]]) -> dict:
    """Apply each step's changes relative to base. Stop on two steps changing one object differently.
    Comments merge on their own: one step commenting on an object another step moves is not a conflict."""
    merged = copy.deepcopy(base)
    errors: list[str] = []
    owner: dict[tuple[str, str], str] = {}
    comment_owner: dict[str, str] = {}
    for name, step in steps:
        for key in ID_KEYS:
            before = {item["id"]: item for item in base.get(key, [])}
            after = {item["id"]: item for item in step.get(key, [])}
            current = {item["id"]: item for item in merged.get(key, [])}
            for item_id in sorted(set(before) | set(after)):
                old, new = before.get(item_id), after.get(item_id)
                if old == new:
                    continue
                if _bare(old) != _bare(new):
                    prior = owner.get((key, item_id))
                    if prior is not None and _bare(current.get(item_id)) != _bare(new):
                        errors.append(f"{key} {item_id}: {prior} and {name} change it differently")
                        continue
                    owner[(key, item_id)] = name
                    if new is None:
                        if set(_comments(current.get(item_id))) - set(_comments(old)):
                            errors.append(f"{key} {item_id}: {name} deletes it, but another step comments on it")
                            continue
                        current.pop(item_id, None)
                        continue
                    kept = (current.get(item_id) or {}).get("comments")
                    current[item_id] = copy.deepcopy(_bare(new))
                    if kept:
                        current[item_id]["comments"] = kept
                if _comments(old) != _comments(new):
                    if item_id not in current:
                        errors.append(f"{key} {item_id}: {name} comments on it, but another step deletes it")
                        continue
                    _merge_comments(current[item_id], old, new, f"{key} {item_id}", name, comment_owner, errors)
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
    _renumber_comments(merged)
    return merged
