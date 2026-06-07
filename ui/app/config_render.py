from __future__ import annotations
from typing import Any, Callable, Optional


def effective(applied: list[dict], staged: list[dict]) -> list[dict]:
    """Overlay staged onto applied. Returns items with a `flag` (None=clean,
    'new'/'changed'/'deleted'). Deleted items are KEPT (marked) so the UI can strike them."""
    out: dict[tuple, dict] = {}
    for it in applied:
        out[(it["kind"], it["name"])] = {**it, "flag": None}
    for st in staged:
        key = (st["kind"], st["name"])
        flag = st.get("flag")
        if flag == "deleted":
            base = out.get(key, {"kind": st["kind"], "name": st["name"], "data": st.get("data")})
            out[key] = {**base, "flag": "deleted"}
        else:  # new | changed
            out[key] = {"kind": st["kind"], "name": st["name"], "data": st["data"], "flag": flag}
    return list(out.values())
