from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

SOURCE = "uitag_yolo11s"
DEFAULT_IMGSZ = int(os.getenv("BP_UI_DETECTOR_IMGSZ", "1280"))

LABEL_FAMILY_MAP = {
    "button": "button",
    "input_elements": "input",
    "navigation": "nav_item",
    "menu": "menu_item",
    "information_display": "text",
    "visual_elements": "visual",
    "others": "unknown",
    "unknown": "unknown",
}


@dataclass
class DetectionResult:
    candidates: list[dict[str, Any]]
    viewport: dict[str, int]
    trace: dict[str, float]


class UiDetector:
    """Lazy YOLO11s UI detector for browser screenshots."""

    def __init__(self, model_path: str | None = None) -> None:
        self._configured_model_path = model_path
        self._model = None

    def detect_base64(
        self,
        screenshot_base64: str,
        *,
        max_candidates: int = 40,
        threshold: float = 0.05,
    ) -> DetectionResult:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "observe --mode vision/mix requires Pillow. "
                "Install backend with: uv sync --extra vision"
            ) from exc

        started = time.perf_counter()
        image = Image.open(io.BytesIO(base64.b64decode(screenshot_base64))).convert("RGB")
        model = self._load_model()
        load_ms = (time.perf_counter() - started) * 1000

        predict_started = time.perf_counter()
        results = model.predict(
            image,
            imgsz=DEFAULT_IMGSZ,
            conf=threshold,
            max_det=max_candidates,
            device="cpu",
            verbose=False,
        )
        predict_ms = (time.perf_counter() - predict_started) * 1000

        candidates = results_to_candidates(
            results=results,
            width=image.width,
            height=image.height,
            max_candidates=max_candidates,
        )
        return DetectionResult(
            candidates=candidates,
            viewport={"width": image.width, "height": image.height},
            trace={
                "vision_load_ms": round(load_ms, 2),
                "vision_predict_ms": round(predict_ms, 2),
                "vision_total_ms": round((time.perf_counter() - started) * 1000, 2),
                "vision_count": float(len(candidates)),
            },
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "observe --mode vision/mix requires vision dependencies. "
                "Install backend with: uv sync --extra vision"
            ) from exc
        self._model = YOLO(str(resolve_model_path(self._configured_model_path)))
        return self._model


def resolve_model_path(configured_model_path: str | None = None) -> Path:
    env_model = os.getenv("BP_UI_DETECTOR_MODEL")
    if env_model:
        path = Path(env_model).expanduser()
        if path.exists():
            return path
        raise RuntimeError(f"BP_UI_DETECTOR_MODEL does not exist: {path}")

    if configured_model_path:
        path = Path(configured_model_path).expanduser()
        if path.exists():
            return path
        raise RuntimeError(f"UI detector model does not exist: {path}")

    try:
        uitag = import_module("uitag")
        package_file = getattr(uitag, "__file__", None)
        if package_file:
            model_path = Path(package_file).resolve().parent / "models" / "yolo-ui.pt"
            if model_path.exists():
                return model_path
    except Exception as exc:
        raise RuntimeError(
            "Could not find uitag packaged model yolo-ui.pt. "
            "Install backend with the vision extra or set BP_UI_DETECTOR_MODEL."
        ) from exc

    raise RuntimeError(
        "Could not find uitag packaged model yolo-ui.pt. "
        "Set BP_UI_DETECTOR_MODEL to a local YOLO11s UI detector weight."
    )


def normalize_family(label: str) -> str:
    key = label.strip().lower().replace(" ", "_")
    return LABEL_FAMILY_MAP.get(key, key or "unknown")


def results_to_candidates(
    *,
    results: Any,
    width: int,
    height: int,
    max_candidates: int,
) -> list[dict[str, Any]]:
    if not results:
        return []
    result = results[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    names = getattr(result, "names", {}) or {}
    rows: list[tuple[float, int, list[float]]] = []
    for box in boxes:
        score = float(box.conf[0]) if getattr(box, "conf", None) is not None else 0.0
        class_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else 0
        xyxy = [float(value) for value in box.xyxy[0].tolist()]
        rows.append((score, class_id, xyxy))
    rows.sort(key=lambda item: item[0], reverse=True)

    candidates: list[dict[str, Any]] = []
    for rank, (score, class_id, xyxy) in enumerate(rows[:max_candidates], start=1):
        x1, y1, x2, y2 = clamp_xyxy(xyxy, width, height)
        if x2 <= x1 or y2 <= y1:
            continue
        raw_label = str(names.get(class_id, class_id))
        family = normalize_family(raw_label)
        candidates.append(
            {
                "id": f"vis-{rank:03d}",
                "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
                "center": {"x": x1 + (x2 - x1) // 2, "y": y1 + (y2 - y1) // 2},
                "score": round(max(0.0, min(score, 1.0)), 4),
                "label": family,
                "family": family,
                "source": SOURCE,
            }
        )
    return candidates


def clamp_xyxy(xyxy: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = [int(round(value)) for value in xyxy]
    x1 = max(0, min(x1, width))
    y1 = max(0, min(y1, height))
    x2 = max(0, min(x2, width))
    y2 = max(0, min(y2, height))
    return x1, y1, x2, y2


def attach_dom_hints(
    vision_candidates: list[dict[str, Any]],
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for candidate in vision_candidates:
        item = dict(candidate)
        hint = best_dom_hint(candidate, elements)
        if hint is not None:
            item["domHint"] = hint
        enriched.append(item)
    return enriched


def build_mixed_candidates(
    *,
    elements: list[dict[str, Any]],
    vision_candidates: list[dict[str, Any]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    mixed: list[dict[str, Any]] = []
    for index, element in enumerate(elements[:max_candidates], start=1):
        bbox = element.get("bbox")
        center = {"x": int(element.get("x", 0)), "y": int(element.get("y", 0))}
        if not isinstance(bbox, dict):
            bbox = {"x": center["x"], "y": center["y"], "w": 1, "h": 1}
        family = dom_family(element)
        mixed.append(
            {
                "id": f"dom-{index:03d}",
                "bbox": bbox,
                "center": center,
                "score": 1.0,
                "label": element_label(element),
                "family": family,
                "source": "dom",
                "domHint": compact_dom_hint(element),
            }
        )
        if len(mixed) >= max_candidates:
            return mixed

    for candidate in vision_candidates:
        if "domHint" in candidate:
            continue
        mixed.append(candidate)
        if len(mixed) >= max_candidates:
            break
    return mixed


def best_dom_hint(candidate: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    bbox = candidate.get("bbox") or {}
    cx = candidate.get("center", {}).get("x", bbox.get("x", 0))
    cy = candidate.get("center", {}).get("y", bbox.get("y", 0))
    hits = [
        element
        for element in elements
        if point_inside_bbox(int(element.get("x", -1)), int(element.get("y", -1)), bbox)
    ]
    if not hits:
        return None
    best = min(
        hits,
        key=lambda element: (
            0 if element.get("text") else 1,
            abs(int(element.get("x", 0)) - int(cx)) + abs(int(element.get("y", 0)) - int(cy)),
        ),
    )
    return compact_dom_hint(best)


def point_inside_bbox(x: int, y: int, bbox: dict[str, Any]) -> bool:
    bx = int(bbox.get("x", 0))
    by = int(bbox.get("y", 0))
    bw = int(bbox.get("w", 0))
    bh = int(bbox.get("h", 0))
    return bx <= x <= bx + bw and by <= y <= by + bh


def compact_dom_hint(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": element.get("tag"),
        "text": element.get("text"),
        "attrs": element.get("attrs", {}),
        "center": {"x": element.get("x"), "y": element.get("y")},
        "bbox": element.get("bbox"),
    }


def dom_family(element: dict[str, Any]) -> str:
    tag = str(element.get("tag") or "").lower()
    attrs = element.get("attrs") or {}
    role = str(attrs.get("role") or "").lower()
    input_type = str(attrs.get("type") or "").lower()
    if tag in {"input", "textarea", "select"} or role in {"textbox", "searchbox"}:
        return "search_box" if input_type == "search" or role == "searchbox" else "input"
    if tag == "button" or role == "button":
        return "button"
    if tag == "a" or role == "link":
        return "link"
    if role == "tab":
        return "tab"
    if role == "menuitem":
        return "menu_item"
    if tag == "img":
        return "image"
    return "dom"


def element_label(element: dict[str, Any]) -> str:
    text = str(element.get("text") or "").strip()
    if text:
        return text[:80]
    attrs = element.get("attrs") or {}
    for key in ("ariaLabel", "placeholder", "alt", "name", "id"):
        value = attrs.get(key)
        if value:
            return str(value)[:80]
    return str(element.get("tag") or "element")


ui_detector = UiDetector()
