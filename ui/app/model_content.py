from __future__ import annotations
from typing import Any

# Single source of truth for the model_info fields the UI manages. Each field
# has a normalizer (coerce desired/live values to a comparable form) and a
# default for when the field is absent. The drift comparator (read) and the
# convergence PATCH builder (write) both use this, so they cannot disagree.
MANAGED_MODEL_INFO: dict[str, dict[str, Any]] = {
    "disable_background_health_check": {"norm": lambda v: bool(v), "default": False},
}


def normalized_managed(model_info: dict | None) -> dict:
    """{field: normalized value} for every managed field, applying defaults for
    absent fields. litellm-derived fields (created_at, db_model, …) are ignored."""
    mi = model_info or {}
    return {f: spec["norm"](mi.get(f, spec["default"])) for f, spec in MANAGED_MODEL_INFO.items()}


def content_diff(desired_mi: dict | None, live_mi: dict | None) -> list[str]:
    """Sorted managed-field names whose normalized values differ. [] == in sync."""
    d, l = normalized_managed(desired_mi), normalized_managed(live_mi)
    return sorted(f for f in MANAGED_MODEL_INFO if d[f] != l[f])
