from __future__ import annotations

from typing import Any

from app.tools.browser.session import wd_fetch


AX_SOURCE = "ax_tree"

AX_FAMILY_BY_ROLE = {
    "button": "button",
    "link": "link",
    "textbox": "input",
    "searchbox": "input",
    "menuitem": "menu_item",
    "menuitemcheckbox": "menu_item",
    "menuitemradio": "menu_item",
    "tab": "tab",
    "checkbox": "control",
    "radio": "control",
    "switch": "control",
    "combobox": "input",
    "listbox": "input",
    "slider": "control",
    "image": "visual",
    "img": "visual",
    "heading": "text",
    "statictext": "text",
    "text": "text",
}

ACTIONABLE_AX_FAMILIES = {"button", "link", "input", "menu_item", "tab", "control"}


def normalize_ax_role(role: str | None) -> str:
    normalized = str(role or "").strip().lower().replace(" ", "")
    return AX_FAMILY_BY_ROLE.get(normalized, "unknown")


async def collect_ax_candidates(
    sid: str,
    *,
    base_url: str,
    max_candidates: int = 180,
) -> list[dict[str, Any]]:
    tree = await _cdp(sid, "Accessibility.getFullAXTree", {}, base_url=base_url)
    nodes = tree.get("nodes", []) if isinstance(tree, dict) else []
    candidates: list[dict[str, Any]] = []
    seen_backend_ids: set[int] = set()

    for node in nodes:
        if len(candidates) >= max_candidates:
            break
        if not isinstance(node, dict) or node.get("ignored") is True:
            continue

        backend_node_id = node.get("backendDOMNodeId")
        if not isinstance(backend_node_id, int) or backend_node_id in seen_backend_ids:
            continue

        role = _ax_value(node.get("role"))
        family = normalize_ax_role(role)
        name = _ax_value(node.get("name")).strip()
        if not _is_interesting_ax_node(family, name):
            continue

        rect = await _backend_node_rect(sid, backend_node_id, base_url=base_url)
        if not rect:
            continue

        seen_backend_ids.add(backend_node_id)
        disabled = _property_bool(node, "disabled")
        selected = _property_bool(node, "selected")
        focused = _property_bool(node, "focused")
        score = _ax_score(family=family, label=name, disabled=disabled)
        candidate_id = f"ax-{len(candidates) + 1:03d}"
        candidates.append(
            {
                "id": candidate_id,
                "bbox": rect,
                "center": {
                    "x": rect["x"] + rect["w"] // 2,
                    "y": rect["y"] + rect["h"] // 2,
                },
                "score": score,
                "label": name or role or family,
                "family": family,
                "source": AX_SOURCE,
                "role": role,
                "axHint": {
                    "role": role,
                    "name": name,
                    "backendDOMNodeId": backend_node_id,
                    "disabled": disabled,
                    "selected": selected,
                    "focused": focused,
                },
            }
        )

    candidates.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate["id"] = f"ax-{index:03d}"
    return candidates


async def _cdp(sid: str, cmd: str, params: dict[str, Any] | None = None, *, base_url: str) -> Any:
    return await wd_fetch(
        f"/session/{sid}/goog/cdp/execute",
        "POST",
        {"cmd": cmd, "params": params or {}},
        timeout=5,
        base_url=base_url,
    )


async def _backend_node_rect(sid: str, backend_node_id: int, *, base_url: str) -> dict[str, int] | None:
    try:
        resolved = await _cdp(
            sid,
            "DOM.resolveNode",
            {"backendNodeId": backend_node_id},
            base_url=base_url,
        )
        object_id = ((resolved or {}).get("object") or {}).get("objectId")
        if not object_id:
            return None
        rect_result = await _cdp(
            sid,
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "returnByValue": True,
                "functionDeclaration": """
function() {
  const r = this.getBoundingClientRect();
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const x1 = Math.max(0, Math.min(vw, r.left));
  const y1 = Math.max(0, Math.min(vh, r.top));
  const x2 = Math.max(0, Math.min(vw, r.right));
  const y2 = Math.max(0, Math.min(vh, r.bottom));
  return {
    x: Math.round(x1),
    y: Math.round(y1),
    w: Math.round(Math.max(0, x2 - x1)),
    h: Math.round(Math.max(0, y2 - y1))
  };
}
""",
            },
            base_url=base_url,
        )
        rect = (((rect_result or {}).get("result") or {}).get("value") or {})
        if int(rect.get("w", 0)) <= 0 or int(rect.get("h", 0)) <= 0:
            return None
        return {
            "x": int(rect.get("x", 0)),
            "y": int(rect.get("y", 0)),
            "w": int(rect.get("w", 0)),
            "h": int(rect.get("h", 0)),
        }
    except Exception:
        return None


def _ax_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _property_bool(node: dict[str, Any], name: str) -> bool:
    for item in node.get("properties") or []:
        if item.get("name") == name:
            return bool((item.get("value") or {}).get("value"))
    return False


def _is_interesting_ax_node(family: str, label: str) -> bool:
    if family in ACTIONABLE_AX_FAMILIES:
        return True
    if family in {"visual", "text"} and label:
        return True
    return False


def _ax_score(*, family: str, label: str, disabled: bool) -> float:
    score = {
        "button": 0.94,
        "input": 0.93,
        "link": 0.9,
        "tab": 0.88,
        "menu_item": 0.86,
        "control": 0.84,
        "visual": 0.62,
        "text": 0.5,
    }.get(family, 0.42)
    if label:
        score += 0.03
    if disabled:
        score -= 0.18
    return round(max(0.0, min(score, 1.0)), 4)
