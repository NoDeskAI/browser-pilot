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
ENABLE_TILING = os.getenv("BP_VISION_TILING", "0").strip().lower() in {"1", "true", "yes", "on"}
ANNOTATION_MAX_CANDIDATES = int(os.getenv("BP_VISION_ANNOTATION_MAX", "160"))
ANNOTATION_LABEL_LIMIT = int(os.getenv("BP_VISION_ANNOTATION_LABEL_LIMIT", "60"))
ANNOTATION_SHOW_GROUPS = os.getenv("BP_VISION_ANNOTATION_GROUPS", "0").strip().lower() in {"1", "true", "yes", "on"}

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
    groups: list[dict[str, Any]]
    viewport: dict[str, int]
    trace: dict[str, Any]
    annotated_screenshot: str | None = None


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
        include_annotated: bool = False,
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
        candidates, tiling_trace = run_detection_pipeline(
            model=model,
            image=image,
            max_candidates=max_candidates,
            threshold=threshold,
        )
        predict_ms = (time.perf_counter() - predict_started) * 1000

        semantics_started = time.perf_counter()
        for candidate in candidates:
            refine_candidate_semantics(candidate, width=image.width, height=image.height)
        semantics_ms = (time.perf_counter() - semantics_started) * 1000

        ocr_started = time.perf_counter()
        ocr_items = run_optional_ocr(image)
        apply_ocr_hints(candidates, ocr_items)
        ocr_ms = (time.perf_counter() - ocr_started) * 1000

        grouping_started = time.perf_counter()
        groups = build_vision_groups(candidates, width=image.width, height=image.height, max_groups=max_candidates)
        grouping_ms = (time.perf_counter() - grouping_started) * 1000

        ranking_started = time.perf_counter()
        candidates = rank_vision_candidates(candidates, max_candidates=max_candidates)
        groups = rank_vision_groups(groups, max_groups=max_candidates)
        ranking_ms = (time.perf_counter() - ranking_started) * 1000

        annotated_screenshot = None
        if include_annotated:
            annotated_screenshot = render_annotated_screenshot_base64(image, candidates, groups)

        return DetectionResult(
            candidates=candidates,
            groups=groups,
            viewport={"width": image.width, "height": image.height},
            trace={
                "vision_load_ms": round(load_ms, 2),
                "vision_predict_ms": round(predict_ms, 2),
                "vision_semantics_ms": round(semantics_ms, 2),
                "vision_ocr_ms": round(ocr_ms, 2),
                "vision_grouping_ms": round(grouping_ms, 2),
                "vision_ranking_ms": round(ranking_ms, 2),
                "vision_total_ms": round((time.perf_counter() - started) * 1000, 2),
                "vision_count": float(len(candidates)),
                "vision_group_count": float(len(groups)),
                "vision_unknown_ratio": round(unknown_ratio(candidates), 4),
                "ocr_enabled": bool(ocr_items),
                **tiling_trace,
            },
            annotated_screenshot=annotated_screenshot,
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


def run_detection_pipeline(
    *,
    model: Any,
    image: Any,
    max_candidates: int,
    threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    full_results = predict_image(model, image, max_candidates=max_candidates, threshold=threshold)
    candidates = results_to_candidates(
        results=full_results,
        width=image.width,
        height=image.height,
        max_candidates=max_candidates,
        id_prefix="vis",
    )
    trace: dict[str, Any] = {"tiling_enabled": ENABLE_TILING, "tiling_regions": 0}
    if not ENABLE_TILING:
        return candidates, trace

    tiled_candidates: list[dict[str, Any]] = []
    for index, region in enumerate(tile_regions(image.width, image.height), start=1):
        x, y, w, h = region
        crop = image.crop((x, y, x + w, y + h))
        results = predict_image(model, crop, max_candidates=max(10, max_candidates // 2), threshold=threshold)
        tiled_candidates.extend(
            results_to_candidates(
                results=results,
                width=image.width,
                height=image.height,
                max_candidates=max(10, max_candidates // 2),
                id_prefix=f"tile{index}",
                offset_x=x,
                offset_y=y,
                source_region={"x": x, "y": y, "w": w, "h": h},
                crop_width=w,
                crop_height=h,
            )
        )
    merged = dedupe_candidates(candidates + tiled_candidates, max_candidates=max_candidates)
    trace["tiling_regions"] = len(tile_regions(image.width, image.height))
    trace["tiling_raw_count"] = len(candidates) + len(tiled_candidates)
    trace["tiling_merged_count"] = len(merged)
    return merged, trace


def predict_image(model: Any, image: Any, *, max_candidates: int, threshold: float) -> Any:
    return model.predict(
        image,
        imgsz=DEFAULT_IMGSZ,
        conf=threshold,
        max_det=max_candidates,
        device="cpu",
        verbose=False,
    )


def tile_regions(width: int, height: int) -> list[tuple[int, int, int, int]]:
    top_h = max(96, int(height * 0.18))
    left_w = max(160, int(width * 0.22))
    return [
        (0, 0, width, min(top_h, height)),
        (0, 0, min(left_w, width), height),
        (min(left_w, width - 1), top_h, max(1, width - left_w), max(1, height - top_h)),
    ]


def results_to_candidates(
    *,
    results: Any,
    width: int,
    height: int,
    max_candidates: int,
    id_prefix: str = "vis",
    offset_x: int = 0,
    offset_y: int = 0,
    source_region: dict[str, int] | None = None,
    crop_width: int | None = None,
    crop_height: int | None = None,
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
        clamp_width = crop_width if crop_width is not None else width
        clamp_height = crop_height if crop_height is not None else height
        x1, y1, x2, y2 = clamp_xyxy(xyxy, clamp_width, clamp_height)
        x1 += offset_x
        x2 += offset_x
        y1 += offset_y
        y2 += offset_y
        x1, y1, x2, y2 = clamp_xyxy([x1, y1, x2, y2], width, height)
        if x2 <= x1 or y2 <= y1:
            continue
        raw_label = str(names.get(class_id, class_id))
        family = normalize_family(raw_label)
        candidate = {
            "id": f"{id_prefix}-{rank:03d}",
            "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
            "center": {"x": x1 + (x2 - x1) // 2, "y": y1 + (y2 - y1) // 2},
            "score": round(max(0.0, min(score, 1.0)), 4),
            "label": family,
            "family": family,
            "rawLabel": raw_label,
            "modelFamily": family,
            "semanticSource": "model",
            "source": SOURCE,
        }
        if source_region:
            candidate["sourceRegion"] = source_region
        refine_candidate_semantics(candidate, width=width, height=height)
        candidates.append(candidate)
    return candidates


def refine_candidate_semantics(candidate: dict[str, Any], *, width: int, height: int) -> None:
    """Attach conservative geometry hints to broad/unknown model labels.

    Geometry is useful context, but it is not a trained semantic classifier. Keep
    model labels stable and expose the guessed family as a hint so debugging
    screenshots do not pretend a weak guess is a confident class.
    """

    family = str(candidate.get("family") or "unknown")
    if family not in {"unknown", "visual"}:
        return

    refined = infer_family_from_geometry(candidate, width=width, height=height)
    if refined and refined != family:
        candidate["geometryHint"] = refined
    if family == "unknown":
        candidate["semanticSource"] = "model_unknown"


def infer_family_from_geometry(candidate: dict[str, Any], *, width: int, height: int) -> str:
    bbox = candidate.get("bbox") or {}
    x = int(bbox.get("x", 0))
    y = int(bbox.get("y", 0))
    w = int(bbox.get("w", 0))
    h = int(bbox.get("h", 0))
    if w <= 0 or h <= 0:
        return "unknown"

    aspect = w / max(h, 1)
    area_ratio = (w * h) / max(width * height, 1)
    top_band = y <= height * 0.16
    left_band = x <= width * 0.18

    if left_band and top_band and 32 <= w <= 220 and 18 <= h <= 90:
        return "logo"
    if 10 <= w <= 72 and 10 <= h <= 72 and 0.55 <= aspect <= 1.8:
        return "icon"
    if aspect >= 5.0 and 22 <= h <= 80 and w >= 180:
        return "input"
    if 1.6 <= aspect <= 6.0 and 24 <= h <= 90 and 48 <= w <= 360:
        return "button"
    if top_band and h <= 80 and w >= 60:
        return "nav_item"
    if left_band and h <= 76 and w <= width * 0.35:
        return "nav_item"
    if area_ratio >= 0.025 and w >= 120 and h >= 90:
        return "visual"
    if h <= 52 and w >= 40:
        return "text"
    return "unknown"


def effective_family(candidate: dict[str, Any]) -> str:
    family = str(candidate.get("family") or "unknown")
    if family == "unknown" and candidate.get("geometryHint"):
        return str(candidate.get("geometryHint"))
    return family


def run_optional_ocr(image: Any) -> list[dict[str, Any]]:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:
        return []
    try:
        import numpy as np

        engine = RapidOCR()
        result, _ = engine(np.array(image))
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for row in result or []:
        try:
            points, text, score = row[:3]
            xs = [int(point[0]) for point in points]
            ys = [int(point[1]) for point in points]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            items.append(
                {
                    "text": str(text),
                    "score": float(score),
                    "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
                }
            )
        except Exception:
            continue
    return items


def apply_ocr_hints(candidates: list[dict[str, Any]], ocr_items: list[dict[str, Any]]) -> None:
    if not ocr_items:
        return
    for candidate in candidates:
        matches = [
            item
            for item in ocr_items
            if bbox_iou(candidate.get("bbox") or {}, item.get("bbox") or {}) >= 0.1
            or bbox_contains(candidate.get("bbox") or {}, item.get("bbox") or {})
        ]
        if not matches:
            matches = [
                item
                for item in ocr_items
                if center_distance(candidate.get("center") or {}, bbox_center(item.get("bbox") or {})) <= 80
            ]
        if not matches:
            continue
        matches.sort(key=lambda item: item.get("score", 0), reverse=True)
        text = " ".join(str(item.get("text", "")).strip() for item in matches[:3]).strip()
        if text:
            candidate["textHint"] = text[:160]
            if str(candidate.get("semanticSource")) in {"model_unknown", "geometry"}:
                candidate["semanticSource"] = "ocr"
            family = str(candidate.get("family") or "")
            lowered = text.lower()
            if family in {"unknown", "text"} and any(word in lowered for word in ("search", "搜索", "login", "sign in", "订阅", "subscribe", "创建", "create")):
                candidate["family"] = "button" if not any(word in lowered for word in ("search", "搜索")) else "input"
                candidate["label"] = candidate["family"]


def build_vision_groups(
    candidates: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    max_groups: int,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    groups.extend(build_toolbar_groups(candidates, width=width, height=height))
    groups.extend(build_nav_cluster_groups(candidates, width=width, height=height))
    groups.extend(build_media_groups(candidates, width=width, height=height))
    groups.extend(build_list_item_groups(candidates, width=width, height=height))
    groups = dedupe_groups(groups, max_groups=max_groups)
    for rank, group in enumerate(groups, start=1):
        group["id"] = f"group-{rank:03d}"
        group["source"] = SOURCE
        group["semanticSource"] = "group"
        for child_id in group.get("children", []):
            for candidate in candidates:
                if candidate.get("id") == child_id:
                    candidate["parentId"] = group["id"]
                    break
    return groups


def build_toolbar_groups(candidates: list[dict[str, Any]], *, width: int, height: int) -> list[dict[str, Any]]:
    items = [
        item
        for item in candidates
        if int((item.get("bbox") or {}).get("y", 0)) <= height * 0.16
        and effective_family(item) in {"button", "input", "nav_item", "menu_item", "icon", "logo"}
    ]
    if len(items) < 3:
        return []
    return [make_group("toolbar", items, score_bonus=0.08)]


def build_nav_cluster_groups(candidates: list[dict[str, Any]], *, width: int, height: int) -> list[dict[str, Any]]:
    items = [
        item
        for item in candidates
        if int((item.get("bbox") or {}).get("x", 0)) <= width * 0.18
        and int((item.get("bbox") or {}).get("h", 0)) <= 96
        and effective_family(item) in {"button", "nav_item", "icon", "logo", "text"}
    ]
    if len(items) < 3:
        return []
    return [make_group("nav_cluster", items, score_bonus=0.04)]


def build_media_groups(candidates: list[dict[str, Any]], *, width: int, height: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    visuals = [
        item
        for item in candidates
        if effective_family(item) == "visual"
        and int((item.get("bbox") or {}).get("w", 0)) >= 100
        and int((item.get("bbox") or {}).get("h", 0)) >= 80
    ]
    for visual in visuals:
        vb = visual.get("bbox") or {}
        vx, vy, vw, vh = int(vb.get("x", 0)), int(vb.get("y", 0)), int(vb.get("w", 0)), int(vb.get("h", 0))
        search = {
            "x": max(0, vx - 24),
            "y": max(0, vy - 24),
            "w": min(width - max(0, vx - 24), vw + max(260, int(width * 0.5))),
            "h": min(height - max(0, vy - 24), vh + 160),
        }
        neighbors = [
            item
            for item in candidates
            if item is not visual
            and effective_family(item) in {"text", "button", "input", "icon", "menu_item"}
            and bbox_intersects(search, item.get("bbox") or {})
        ]
        if not neighbors:
            continue
        group_items = [visual] + neighbors[:5]
        has_text = any(effective_family(item) == "text" for item in neighbors)
        family = "video_card" if has_text and vw >= width * 0.22 else "media_item"
        groups.append(make_group(family, group_items, score_bonus=0.12))
    return groups


def build_list_item_groups(candidates: list[dict[str, Any]], *, width: int, height: int) -> list[dict[str, Any]]:
    rows: dict[int, list[dict[str, Any]]] = {}
    for item in candidates:
        family = effective_family(item)
        if family not in {"text", "button", "input", "icon", "logo"}:
            continue
        bbox = item.get("bbox") or {}
        h = int(bbox.get("h", 0))
        w = int(bbox.get("w", 0))
        if h <= 0 or h > 80 or w <= 0:
            continue
        bucket = int((int(bbox.get("y", 0)) + h // 2) / 52)
        rows.setdefault(bucket, []).append(item)
    groups: list[dict[str, Any]] = []
    for items in rows.values():
        if len(items) < 2:
            continue
        union = union_bbox([item.get("bbox") or {} for item in items])
        if union["w"] < width * 0.18 or union["h"] > 120:
            continue
        groups.append(make_group("list_item", items, score_bonus=0.06))
    return groups


def make_group(family: str, items: list[dict[str, Any]], *, score_bonus: float = 0.0) -> dict[str, Any]:
    bbox = union_bbox([item.get("bbox") or {} for item in items])
    score = min(1.0, (sum(float(item.get("score") or 0.0) for item in items) / max(len(items), 1)) + score_bonus)
    text_parts = [str(item.get("textHint") or "").strip() for item in items if item.get("textHint")]
    group: dict[str, Any] = {
        "bbox": bbox,
        "center": bbox_center(bbox),
        "score": round(score, 4),
        "mixScore": round(min(score + 0.12, 1.0), 4),
        "label": family,
        "family": family,
        "children": [str(item.get("id")) for item in items if item.get("id")],
        "kind": "vision_group",
    }
    if text_parts:
        group["textHint"] = " ".join(text_parts)[:180]
    return group


def rank_vision_candidates(candidates: list[dict[str, Any]], *, max_candidates: int) -> list[dict[str, Any]]:
    for item in candidates:
        family = str(item.get("family") or "unknown")
        ranking_family = effective_family(item)
        bonus = {
            "button": 0.08,
            "input": 0.08,
            "icon": 0.05,
            "logo": 0.04,
            "nav_item": 0.04,
            "menu_item": 0.04,
            "visual": 0.03,
            "text": 0.02,
            "unknown": -0.12,
        }.get(ranking_family, 0.0)
        if family == "unknown" and item.get("geometryHint"):
            bonus -= 0.06
        if item.get("textHint"):
            bonus += 0.05
        item["rankScore"] = round(max(0.0, min(float(item.get("score") or 0.0) + bonus, 1.0)), 4)
    candidates.sort(key=lambda item: item.get("rankScore", 0.0), reverse=True)
    for rank, item in enumerate(candidates[:max_candidates], start=1):
        item["rank"] = rank
    return candidates[:max_candidates]


def rank_vision_groups(groups: list[dict[str, Any]], *, max_groups: int) -> list[dict[str, Any]]:
    groups.sort(key=lambda item: item.get("mixScore", item.get("score", 0.0)), reverse=True)
    for rank, item in enumerate(groups[:max_groups], start=1):
        item["rank"] = rank
    return groups[:max_groups]


def render_annotated_screenshot_base64(image: Any, candidates: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    from PIL import ImageDraw, ImageFont

    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    if ANNOTATION_SHOW_GROUPS:
        for item in groups[:12]:
            draw_labeled_box(draw, font, item, prefix=f"G{item.get('rank', '')}", width=2, palette=GROUP_COLORS, faint=True)
    for index, item in enumerate(candidates[: max(1, ANNOTATION_MAX_CANDIDATES)], start=1):
        draw_labeled_box(
            draw,
            font,
            item,
            prefix=str(item.get("rank", "")),
            width=2 if index <= ANNOTATION_LABEL_LIMIT else 1,
            palette=FAMILY_COLORS,
            show_label=index <= ANNOTATION_LABEL_LIMIT,
        )
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


FAMILY_COLORS = {
    "button": (255, 80, 80),
    "input": (80, 150, 255),
    "nav_item": (255, 180, 60),
    "menu_item": (255, 210, 80),
    "text": (120, 220, 120),
    "visual": (190, 120, 255),
    "icon": (80, 220, 220),
    "logo": (255, 105, 180),
    "unknown": (170, 170, 170),
}
GROUP_COLORS = {
    "card": (255, 120, 30),
    "video_card": (255, 120, 30),
    "media_item": (180, 100, 255),
    "list_item": (70, 200, 120),
    "toolbar": (60, 170, 255),
    "nav_cluster": (255, 200, 80),
}


def draw_labeled_box(
    draw: Any,
    font: Any,
    item: dict[str, Any],
    *,
    prefix: str,
    width: int,
    palette: dict[str, tuple[int, int, int]],
    faint: bool = False,
    show_label: bool = True,
) -> None:
    bbox = item.get("bbox") or {}
    x, y, w, h = [int(bbox.get(key, 0)) for key in ("x", "y", "w", "h")]
    if w <= 0 or h <= 0:
        return
    family = str(item.get("family") or "unknown")
    color = palette.get(family, FAMILY_COLORS.get(family, FAMILY_COLORS["unknown"]))
    if faint:
        color = tuple(int(channel * 0.55 + 255 * 0.45) for channel in color)
    draw.rectangle([x, y, x + w, y + h], outline=color, width=width)
    if not show_label:
        return
    hint = item.get("geometryHint")
    suffix = f"~{hint}" if family == "unknown" and hint else ""
    label = f"{prefix} {family}{suffix} {float(item.get('score') or 0.0):.2f}".strip()
    text_w = int(draw.textlength(label, font=font))
    label_y = max(0, y - 16)
    draw.rectangle([x, label_y, x + text_w + 6, label_y + 14], fill=color)
    draw.text((x + 3, label_y + 1), label, fill=(0, 0, 0), font=font)


def clamp_xyxy(xyxy: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = [int(round(value)) for value in xyxy]
    x1 = max(0, min(x1, width))
    y1 = max(0, min(y1, height))
    x2 = max(0, min(x2, width))
    y2 = max(0, min(y2, height))
    return x1, y1, x2, y2


def bbox_center(bbox: dict[str, Any]) -> dict[str, int]:
    return {
        "x": int(bbox.get("x", 0)) + int(bbox.get("w", 0)) // 2,
        "y": int(bbox.get("y", 0)) + int(bbox.get("h", 0)) // 2,
    }


def union_bbox(boxes: list[dict[str, Any]]) -> dict[str, int]:
    valid = [box for box in boxes if int(box.get("w", 0)) > 0 and int(box.get("h", 0)) > 0]
    if not valid:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    x1 = min(int(box.get("x", 0)) for box in valid)
    y1 = min(int(box.get("y", 0)) for box in valid)
    x2 = max(int(box.get("x", 0)) + int(box.get("w", 0)) for box in valid)
    y2 = max(int(box.get("y", 0)) + int(box.get("h", 0)) for box in valid)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = int(a.get("x", 0)), int(a.get("y", 0))
    ax2, ay2 = ax1 + int(a.get("w", 0)), ay1 + int(a.get("h", 0))
    bx1, by1 = int(b.get("x", 0)), int(b.get("y", 0))
    bx2, by2 = bx1 + int(b.get("w", 0)), by1 + int(b.get("h", 0))
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = max((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter, 1)
    return inter / union


def bbox_contains(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    ox, oy = int(outer.get("x", 0)), int(outer.get("y", 0))
    ow, oh = int(outer.get("w", 0)), int(outer.get("h", 0))
    ix, iy = int(inner.get("x", 0)), int(inner.get("y", 0))
    iw, ih = int(inner.get("w", 0)), int(inner.get("h", 0))
    return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def bbox_intersects(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bbox_iou(a, b) > 0 or bbox_contains(a, b) or bbox_contains(b, a)


def center_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    return ((int(a.get("x", 0)) - int(b.get("x", 0))) ** 2 + (int(a.get("y", 0)) - int(b.get("y", 0))) ** 2) ** 0.5


def dedupe_candidates(candidates: list[dict[str, Any]], *, max_candidates: int) -> list[dict[str, Any]]:
    candidates.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(bbox_iou(candidate.get("bbox") or {}, other.get("bbox") or {}) >= 0.72 for other in kept):
            continue
        kept.append(candidate)
        if len(kept) >= max_candidates:
            break
    for rank, candidate in enumerate(kept, start=1):
        candidate["id"] = f"vis-{rank:03d}"
    return kept


def dedupe_groups(groups: list[dict[str, Any]], *, max_groups: int) -> list[dict[str, Any]]:
    groups.sort(key=lambda item: float(item.get("mixScore", item.get("score", 0.0)) or 0.0), reverse=True)
    kept: list[dict[str, Any]] = []
    for group in groups:
        if any(bbox_iou(group.get("bbox") or {}, other.get("bbox") or {}) >= 0.65 for other in kept):
            continue
        kept.append(group)
        if len(kept) >= max_groups:
            break
    return kept


def unknown_ratio(candidates: list[dict[str, Any]]) -> float:
    if not candidates:
        return 0.0
    return len([item for item in candidates if item.get("family") == "unknown"]) / len(candidates)


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
    vision_groups: list[dict[str, Any]] | None = None,
    max_candidates: int,
) -> list[dict[str, Any]]:
    max_candidates = max(1, max_candidates)
    group_entries = [
        make_vision_group_mixed_candidate(group)
        for group in (vision_groups or [])
        if is_usable_vision_candidate(group)
    ]
    group_entries.sort(key=lambda item: item.get("mixScore", 0.0), reverse=True)

    dom_entries = [
        make_dom_mixed_candidate(index, element)
        for index, element in enumerate(elements, start=1)
        if is_usable_dom_element(element)
    ]
    dom_entries.sort(key=lambda item: item.get("mixScore", 0.0), reverse=True)

    vision_entries = [
        make_vision_mixed_candidate(candidate)
        for candidate in vision_candidates
        if is_usable_vision_candidate(candidate)
    ]
    vision_entries.sort(key=lambda item: item.get("mixScore", 0.0), reverse=True)

    if group_entries:
        group_slots = min(len(group_entries), max(1, round(max_candidates * 0.3)))
    else:
        group_slots = 0

    if not dom_entries:
        return (group_entries + vision_entries)[:max_candidates]
    if not vision_entries:
        return (group_entries + dom_entries)[:max_candidates]

    vision_slots = min(len(vision_entries), max(1, round(max_candidates * 0.35)))
    dom_slots = max(0, max_candidates - vision_slots - group_slots)

    selected = group_entries[:group_slots] + dom_entries[:dom_slots] + vision_entries[:vision_slots]
    selected_ids = {item["id"] for item in selected}
    remaining = [
        item
        for item in group_entries[group_slots:] + dom_entries[dom_slots:] + vision_entries[vision_slots:]
        if item["id"] not in selected_ids
    ]
    remaining.sort(key=lambda item: item.get("mixScore", 0.0), reverse=True)
    selected.extend(remaining[: max_candidates - len(selected)])
    selected.sort(key=lambda item: item.get("mixScore", 0.0), reverse=True)
    return selected[:max_candidates]


def make_dom_mixed_candidate(index: int, element: dict[str, Any]) -> dict[str, Any]:
    bbox = element.get("bbox")
    center = {"x": int(element.get("x", 0)), "y": int(element.get("y", 0))}
    if not isinstance(bbox, dict):
        bbox = {"x": center["x"], "y": center["y"], "w": 1, "h": 1}
    family = dom_family(element)
    score = dom_mix_score(element, family)
    return {
        "id": f"dom-{index:03d}",
        "bbox": bbox,
        "center": center,
        "score": round(score, 4),
        "mixScore": round(score, 4),
        "label": element_label(element),
        "family": family,
        "source": "dom",
        "kind": "dom",
        "domHint": compact_dom_hint(element),
    }


def make_vision_mixed_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    family = str(item.get("family") or item.get("label") or "unknown")
    raw_score = float(item.get("score") or 0.0)
    source_bonus = 0.12 if "domHint" not in item else 0.04
    family_bonus = {
        "button": 0.08,
        "input": 0.08,
        "nav_item": 0.05,
        "menu_item": 0.05,
        "visual": 0.04,
        "text": 0.02,
        "unknown": -0.08,
    }.get(family, 0.0)
    item["mixScore"] = round(max(0.0, min(raw_score + source_bonus + family_bonus, 1.0)), 4)
    item["kind"] = "vision"
    return item


def make_vision_group_mixed_candidate(group: dict[str, Any]) -> dict[str, Any]:
    item = dict(group)
    item["source"] = "vision_group"
    item["kind"] = "vision_group"
    item["score"] = round(float(item.get("score") or item.get("mixScore") or 0.0), 4)
    item["mixScore"] = round(min(float(item.get("mixScore") or item["score"]) + 0.08, 1.0), 4)
    return item


def is_usable_dom_element(element: dict[str, Any]) -> bool:
    x = int(element.get("x", -1))
    y = int(element.get("y", -1))
    if x < 0 or y < 0:
        return False
    family = dom_family(element)
    label = element_label(element).strip()
    bbox = element.get("bbox")
    if isinstance(bbox, dict) and int(bbox.get("w", 0)) <= 0 and int(bbox.get("h", 0)) <= 0:
        return False
    return family != "dom" or bool(label)


def is_usable_vision_candidate(candidate: dict[str, Any]) -> bool:
    bbox = candidate.get("bbox")
    if not isinstance(bbox, dict):
        return False
    return int(bbox.get("w", 0)) > 0 and int(bbox.get("h", 0)) > 0


def dom_mix_score(element: dict[str, Any], family: str) -> float:
    score = {
        "input": 0.98,
        "search_box": 0.98,
        "button": 0.96,
        "link": 0.9,
        "tab": 0.86,
        "menu_item": 0.84,
        "image": 0.62,
        "dom": 0.48,
    }.get(family, 0.5)
    if element_label(element).strip():
        score += 0.03
    bbox = element.get("bbox")
    if isinstance(bbox, dict):
        area = int(bbox.get("w", 0)) * int(bbox.get("h", 0))
        if area <= 4:
            score -= 0.18
        elif area > 400:
            score += 0.02
    return max(0.0, min(score, 1.0))


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
