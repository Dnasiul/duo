"""Roster diversity classification for reviewer panels.

Normalization compares model-name spellings only. It does not infer routing,
independence, or provider identity beyond explicit known prefixes.
"""
from __future__ import annotations

import re

from .models import ReviewerSpec

ALIASES: dict[str, str] = {}
_FAMILY_PREFIXES = (("gpt-", "gpt"), ("claude-", "claude"))


def normalize_model(model: str) -> str:
    normalized = re.sub(r"(?<=\d)\.(?=\d)", "-", model.strip().lower())
    return ALIASES.get(normalized, normalized)


def model_family(normalized: str) -> str | None:
    for prefix, family in _FAMILY_PREFIXES:
        if normalized.startswith(prefix):
            return family
    return None


def classify_roster(specs: tuple[ReviewerSpec, ...]) -> dict:
    members = []
    for spec in specs:
        normalized = normalize_model(spec.model)
        members.append({
            "id": spec.id,
            "model": spec.model,
            "normalized": normalized,
            "family": model_family(normalized),
        })
    pairs = []
    warnings = []
    for left in range(len(members)):
        for right in range(left + 1, len(members)):
            a = members[left]
            b = members[right]
            if a["normalized"] == b["normalized"]:
                relation = "identical"
                warnings.append(
                    f"{a['id']} and {b['id']} use the same model; agreement between them is not independent evidence.")
            elif a["family"] is None or b["family"] is None:
                relation = "unknown"
            elif a["family"] == b["family"]:
                relation = "same-family"
            else:
                relation = "different-family"
            pairs.append({"a": a["id"], "b": b["id"], "relation": relation})
    families: dict[str, list[str]] = {}
    unknown = []
    for item in members:
        family = item["family"]
        if family is None:
            unknown.append(item["id"])
        else:
            families.setdefault(family, []).append(item["id"])
    total = len(members)
    for family, ids in families.items():
        if len(ids) >= 2:
            warnings.append(
                f"Same-family concentration: {len(ids)} of {total} reviewers are {family}-family; role names do not create independent model families.")
    for member_id in unknown:
        warnings.append(
            f"Model family for {member_id} is unknown; diversity cannot be assessed for this reviewer.")
    return {"members": members, "pairs": pairs, "families": families, "unknown": unknown,
            "warnings": warnings}
