from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.error import URLError

SOURCE = "yolov8_ui"
OMNIPARSER_SOURCE = "omniparser"
DEFAULT_IMGSZ = int(os.getenv("BP_UI_DETECTOR_IMGSZ", "1280"))
PREPROCESS_ENABLED = os.getenv("BP_VISION_PREPROCESS", "1").strip().lower() not in {"0", "false", "no", "off"}
PREPROCESS_MAX_LONG_EDGE = int(os.getenv("BP_VISION_MAX_LONG_EDGE", "1280"))
PREPROCESS_MAX_PIXELS = int(os.getenv("BP_VISION_MAX_PIXELS", str(1280 * 720)))
ENABLE_TILING = os.getenv("BP_VISION_TILING", "0").strip().lower() in {"1", "true", "yes", "on"}
ANNOTATION_MAX_CANDIDATES = int(os.getenv("BP_VISION_ANNOTATION_MAX", "160"))
ANNOTATION_LABEL_LIMIT = int(os.getenv("BP_VISION_ANNOTATION_LABEL_LIMIT", "60"))
ANNOTATION_SHOW_GROUPS = os.getenv("BP_VISION_ANNOTATION_GROUPS", "0").strip().lower() in {"1", "true", "yes", "on"}

LABEL_FAMILY_MAP = {
    "button": "button",
    "field": "input",
    "input_field": "input",
    "input_elements": "input",
    "link": "nav_item",
    "navigation": "nav_item",
    "menu": "menu_item",
    "heading": "text",
    "label": "text",
    "text": "text",
    "information_display": "text",
    "image": "visual",
    "iframe": "visual",
    "visual_elements": "visual",
    "others": "unknown",
    "unknown": "unknown",
}

DEFAULT_UI_MODEL_FILENAME = "noah-real-yolov8n-ui.pt"
DEFAULT_UI_MODEL_URL = (
    "https://huggingface.co/Noah03064515s22/yolov8-ui-detection-models/"
    "resolve/main/models/real_yolov8n.pt"
)


@dataclass
class DetectionResult:
    candidates: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    viewport: dict[str, int]
    trace: dict[str, Any]
    annotated_screenshot: str | None = None
    vision_frame: dict[str, Any] | None = None


@dataclass
class VisionFrame:
    raw_base64: str
    inference_base64: str
    raw_image: Any
    inference_image: Any
    raw_size: dict[str, int]
    inference_size: dict[str, int]
    click_viewport: dict[str, int]
    raw_to_inference_scale: dict[str, float]
    inference_to_raw_scale: dict[str, float]
    raw_to_click_scale: dict[str, float]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "rawSize": self.raw_size,
            "inferenceSize": self.inference_size,
            "clickViewport": self.click_viewport,
            "rawToInferenceScale": self.raw_to_inference_scale,
            "inferenceToRawScale": self.inference_to_raw_scale,
            "rawToClickScale": self.raw_to_click_scale,
            "coordinateSpace": "click-viewport",
            "preprocessEnabled": PREPROCESS_ENABLED,
        }


class UiDetector:
    """Lazy YOLO UI detector for browser screenshots."""

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
        click_viewport: dict[str, Any] | None = None,
    ) -> DetectionResult:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "observe --mode vision/mix requires Pillow. "
                "Install backend with: uv sync --extra vision"
            ) from exc

        started = time.perf_counter()
        raw_image = Image.open(io.BytesIO(base64.b64decode(screenshot_base64))).convert("RGB")
        frame = build_vision_frame(raw_image, screenshot_base64, click_viewport=click_viewport)
        image = frame.inference_image
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
        candidates = map_items_to_click_space(candidates, frame)
        groups = map_items_to_click_space(groups, frame)

        return DetectionResult(
            candidates=candidates,
            groups=groups,
            viewport=frame.click_viewport,
            trace={
                "vision_backend": SOURCE,
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
            vision_frame=frame.to_public_dict(),
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


class OmniParserDetector:
    """Adapter for Microsoft OmniParser V2 screen parsing.

    OmniParser can run as a separate FastAPI service or from a local cloned repo.
    Keeping it behind this adapter lets Browser Pilot continue to ship without
    vendoring OmniParser code or weights.
    """

    def __init__(self) -> None:
        self._parser = None
        self._repo_path: Path | None = None

    def detect_base64(
        self,
        screenshot_base64: str,
        *,
        max_candidates: int = 40,
        threshold: float = 0.05,
        include_annotated: bool = False,
        click_viewport: dict[str, Any] | None = None,
    ) -> DetectionResult:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "observe --mode vision/mix requires Pillow. "
                "Install backend with: uv sync --extra vision"
            ) from exc

        started = time.perf_counter()
        raw_image = Image.open(io.BytesIO(base64.b64decode(screenshot_base64))).convert("RGB")
        frame = build_vision_frame(raw_image, screenshot_base64, click_viewport=click_viewport)
        image = frame.inference_image

        parse_started = time.perf_counter()
        payload, mode = self._parse_with_omniparser(
            screenshot_base64=frame.inference_base64,
            threshold=threshold,
        )
        parse_ms = (time.perf_counter() - parse_started) * 1000

        candidates = omniparser_items_to_candidates(
            payload.get("parsed_content_list") or payload.get("parsedContentList") or [],
            width=image.width,
            height=image.height,
            max_candidates=max_candidates,
        )

        grouping_started = time.perf_counter()
        groups = build_vision_groups(candidates, width=image.width, height=image.height, max_groups=max_candidates)
        grouping_ms = (time.perf_counter() - grouping_started) * 1000

        ranking_started = time.perf_counter()
        candidates = rank_vision_candidates(candidates, max_candidates=max_candidates)
        groups = rank_vision_groups(groups, max_groups=max_candidates)
        ranking_ms = (time.perf_counter() - ranking_started) * 1000

        annotated_screenshot = None
        if include_annotated:
            annotated_screenshot = (
                payload.get("som_image_base64")
                or payload.get("annotatedScreenshot")
                or render_annotated_screenshot_base64(image, candidates, groups)
            )
        candidates = map_items_to_click_space(candidates, frame)
        groups = map_items_to_click_space(groups, frame)

        return DetectionResult(
            candidates=candidates,
            groups=groups,
            viewport=frame.click_viewport,
            trace={
                "vision_backend": OMNIPARSER_SOURCE,
                "omniparser_mode": mode,
                "omniparser_parse_ms": round(parse_ms, 2),
                "omniparser_latency_s": payload.get("latency"),
                "vision_grouping_ms": round(grouping_ms, 2),
                "vision_ranking_ms": round(ranking_ms, 2),
                "vision_total_ms": round((time.perf_counter() - started) * 1000, 2),
                "vision_count": float(len(candidates)),
                "vision_group_count": float(len(groups)),
                "vision_unknown_ratio": round(unknown_ratio(candidates), 4),
                "ocr_enabled": True,
            },
            annotated_screenshot=annotated_screenshot,
            vision_frame=frame.to_public_dict(),
        )

    def _parse_with_omniparser(self, *, screenshot_base64: str, threshold: float) -> tuple[dict[str, Any], str]:
        server_url = os.getenv("BP_OMNIPARSER_URL", "").strip()
        if server_url:
            return self._parse_with_server(server_url, screenshot_base64), "server"
        return self._parse_with_local_repo(screenshot_base64=screenshot_base64, threshold=threshold), "local"

    def _parse_with_server(self, server_url: str, screenshot_base64: str) -> dict[str, Any]:
        endpoint = server_url.rstrip("/") + "/parse/"
        body = json.dumps({"base64_image": screenshot_base64}).encode("utf-8")
        req = urllib_request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=float(os.getenv("BP_OMNIPARSER_TIMEOUT", "120"))) as response:
                data = response.read().decode("utf-8")
        except URLError as exc:
            raise RuntimeError(f"OmniParser server is not reachable at {endpoint}: {exc}") from exc
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise RuntimeError("OmniParser server returned a non-object response")
        return parsed

    def _parse_with_local_repo(self, *, screenshot_base64: str, threshold: float) -> dict[str, Any]:
        parser = self._load_local_parser(threshold=threshold)
        parser.config["BOX_TRESHOLD"] = threshold
        annotated, parsed_content_list = parser.parse(screenshot_base64)
        return {
            "som_image_base64": annotated,
            "parsed_content_list": parsed_content_list,
        }

    def _load_local_parser(self, *, threshold: float):
        if self._parser is not None:
            return self._parser

        repo = resolve_omniparser_repo()
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))

        try:
            from util.omniparser import Omniparser  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Could not import OmniParser from BP_OMNIPARSER_REPO. "
                "Clone https://github.com/microsoft/OmniParser and install its requirements, "
                "or set BP_OMNIPARSER_URL to a running OmniParser server."
            ) from exc

        config = {
            "som_model_path": str(resolve_omniparser_icon_detect_model(repo)),
            "caption_model_name": os.getenv("BP_OMNIPARSER_CAPTION_MODEL", "florence2"),
            "caption_model_path": str(resolve_omniparser_caption_model(repo)),
            "device": os.getenv("BP_OMNIPARSER_DEVICE", "cpu"),
            "BOX_TRESHOLD": threshold,
        }
        self._parser = Omniparser(config)
        self._repo_path = repo
        return self._parser


class VisionDetector:
    """Selects the configured screenshot parser backend."""

    def __init__(self) -> None:
        self._yolo = UiDetector()
        self._omniparser = OmniParserDetector()

    def detect_base64(
        self,
        screenshot_base64: str,
        *,
        max_candidates: int = 40,
        threshold: float = 0.05,
        include_annotated: bool = False,
        click_viewport: dict[str, Any] | None = None,
    ) -> DetectionResult:
        backend = os.getenv("BP_VISION_BACKEND", "yolo").strip().lower()
        if backend in {"uitag", "yolo", "yolo11s", "yolov8", "yolov8_ui"}:
            result = self._yolo.detect_base64(
                screenshot_base64,
                max_candidates=max_candidates,
                threshold=threshold,
                include_annotated=include_annotated,
                click_viewport=click_viewport,
            )
            return result
        if backend in {"omniparser", "omni"}:
            return self._omniparser.detect_base64(
                screenshot_base64,
                max_candidates=max_candidates,
                threshold=threshold,
                include_annotated=include_annotated,
                click_viewport=click_viewport,
            )
        raise RuntimeError('BP_VISION_BACKEND must be one of: "yolo", "omniparser"')


def resolve_model_path(configured_model_path: str | None = None) -> Path:
    env_model = os.getenv("BP_UI_DETECTOR_MODEL")
    if env_model:
        path = Path(env_model).expanduser()
        if path.exists():
            return path
        raise RuntimeError(missing_default_model_message(f"BP_UI_DETECTOR_MODEL does not exist: {path}"))

    if configured_model_path:
        path = Path(configured_model_path).expanduser()
        if path.exists():
            return path
        raise RuntimeError(missing_default_model_message(f"UI detector model does not exist: {path}"))

    for path in default_model_candidates():
        if path.exists():
            return path

    raise RuntimeError(missing_default_model_message())


def default_model_candidates() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[4]
    candidates = [
        repo_root / "backend" / "models" / DEFAULT_UI_MODEL_FILENAME,
        repo_root / "models" / DEFAULT_UI_MODEL_FILENAME,
        Path.home() / ".cache" / "browser-pilot" / "models" / DEFAULT_UI_MODEL_FILENAME,
        repo_root / "vision_benchmark" / "models" / DEFAULT_UI_MODEL_FILENAME,
    ]
    return candidates


def missing_default_model_message(prefix: str | None = None) -> str:
    locations = ", ".join(str(path) for path in default_model_candidates()[:3])
    base = (
        "YOLOv8 UI detector weight is not installed. Download it from "
        f"{DEFAULT_UI_MODEL_URL} and save it as {DEFAULT_UI_MODEL_FILENAME} under one of: "
        f"{locations}. You can also set BP_UI_DETECTOR_MODEL=/absolute/path/to/{DEFAULT_UI_MODEL_FILENAME}."
    )
    return f"{prefix}. {base}" if prefix else base


def resolve_omniparser_repo() -> Path:
    configured = os.getenv("BP_OMNIPARSER_REPO", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            Path.home() / "OmniParser",
            Path.home() / "Downloads" / "OmniParser",
            Path("/opt/OmniParser"),
        ]
    )
    for path in candidates:
        if (path / "util" / "omniparser.py").exists():
            return path.resolve()
    raise RuntimeError(
        "BP_VISION_BACKEND=omniparser requires either BP_OMNIPARSER_URL or a local "
        "OmniParser repo. Set BP_OMNIPARSER_REPO=/path/to/OmniParser after cloning "
        "https://github.com/microsoft/OmniParser."
    )


def resolve_omniparser_icon_detect_model(repo: Path) -> Path:
    configured = os.getenv("BP_OMNIPARSER_ICON_DETECT_MODEL", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(repo / "weights" / "icon_detect" / "model.pt")
    for path in candidates:
        if path.exists():
            return path.resolve()
    raise RuntimeError(
        "OmniParser icon detection weights not found. Download microsoft/OmniParser-v2.0 "
        "icon_detect/* into OmniParser/weights/icon_detect, or set BP_OMNIPARSER_ICON_DETECT_MODEL."
    )


def resolve_omniparser_caption_model(repo: Path) -> Path:
    configured = os.getenv("BP_OMNIPARSER_CAPTION_MODEL_PATH", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            repo / "weights" / "icon_caption_florence",
            repo / "weights" / "icon_caption",
        ]
    )
    for path in candidates:
        if path.exists():
            return path.resolve()
    raise RuntimeError(
        "OmniParser caption weights not found. Download microsoft/OmniParser-v2.0 "
        "icon_caption/* into OmniParser/weights/icon_caption_florence, or set BP_OMNIPARSER_CAPTION_MODEL_PATH."
    )


def build_vision_frame(raw_image: Any, raw_base64: str, *, click_viewport: dict[str, Any] | None = None) -> VisionFrame:
    raw_width, raw_height = int(raw_image.width), int(raw_image.height)
    inference_width, inference_height = target_inference_size(raw_width, raw_height)
    if inference_width == raw_width and inference_height == raw_height:
        inference_image = raw_image
        inference_base64 = raw_base64
    else:
        inference_image = raw_image.resize((inference_width, inference_height), resample=get_lanczos_filter(raw_image))
        inference_base64 = encode_png_base64(inference_image)

    click_width = int((click_viewport or {}).get("width") or raw_width)
    click_height = int((click_viewport or {}).get("height") or raw_height)
    click_width = max(1, click_width)
    click_height = max(1, click_height)

    raw_to_inference_x = inference_width / max(raw_width, 1)
    raw_to_inference_y = inference_height / max(raw_height, 1)
    inference_to_raw_x = raw_width / max(inference_width, 1)
    inference_to_raw_y = raw_height / max(inference_height, 1)
    raw_to_click_x = click_width / max(raw_width, 1)
    raw_to_click_y = click_height / max(raw_height, 1)

    return VisionFrame(
        raw_base64=raw_base64,
        inference_base64=inference_base64,
        raw_image=raw_image,
        inference_image=inference_image,
        raw_size={"width": raw_width, "height": raw_height},
        inference_size={"width": inference_width, "height": inference_height},
        click_viewport={"width": click_width, "height": click_height},
        raw_to_inference_scale={"x": round(raw_to_inference_x, 6), "y": round(raw_to_inference_y, 6)},
        inference_to_raw_scale={"x": round(inference_to_raw_x, 6), "y": round(inference_to_raw_y, 6)},
        raw_to_click_scale={"x": round(raw_to_click_x, 6), "y": round(raw_to_click_y, 6)},
    )


def target_inference_size(width: int, height: int) -> tuple[int, int]:
    if not PREPROCESS_ENABLED or width <= 0 or height <= 0:
        return max(1, width), max(1, height)
    scale = 1.0
    if PREPROCESS_MAX_LONG_EDGE > 0:
        scale = min(scale, PREPROCESS_MAX_LONG_EDGE / max(width, height))
    if PREPROCESS_MAX_PIXELS > 0 and width * height > PREPROCESS_MAX_PIXELS:
        scale = min(scale, math.sqrt(PREPROCESS_MAX_PIXELS / max(width * height, 1)))
    if scale >= 0.999:
        return width, height
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def get_lanczos_filter(image: Any) -> Any:
    resampling = getattr(image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    try:
        from PIL import Image

        return Image.Resampling.LANCZOS
    except Exception:
        return 1


def encode_png_base64(image: Any) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def map_items_to_click_space(items: list[dict[str, Any]], frame: VisionFrame) -> list[dict[str, Any]]:
    return [map_item_to_click_space(item, frame) for item in items]


def map_item_to_click_space(item: dict[str, Any], frame: VisionFrame) -> dict[str, Any]:
    mapped = dict(item)
    bbox = mapped.get("bbox")
    if not isinstance(bbox, dict):
        return mapped
    mapped["inferenceBbox"] = dict(bbox)
    mapped["bbox"] = inference_bbox_to_click_bbox(bbox, frame)
    mapped["center"] = bbox_center(mapped["bbox"])
    mapped["coordinateSpace"] = "click-viewport"
    return mapped


def inference_bbox_to_click_bbox(bbox: dict[str, Any], frame: VisionFrame) -> dict[str, int]:
    sx = float(frame.inference_to_raw_scale["x"]) * float(frame.raw_to_click_scale["x"])
    sy = float(frame.inference_to_raw_scale["y"]) * float(frame.raw_to_click_scale["y"])
    x = int(round(float(bbox.get("x", 0)) * sx))
    y = int(round(float(bbox.get("y", 0)) * sy))
    w = int(round(float(bbox.get("w", 0)) * sx))
    h = int(round(float(bbox.get("h", 0)) * sy))
    max_w = int(frame.click_viewport["width"])
    max_h = int(frame.click_viewport["height"])
    x = max(0, min(x, max_w))
    y = max(0, min(y, max_h))
    w = max(1, min(w, max_w - x if x < max_w else 1))
    h = max(1, min(h, max_h - y if y < max_h else 1))
    return {"x": x, "y": y, "w": w, "h": h}


def normalize_family(label: str) -> str:
    key = label.strip().lower().replace(" ", "_")
    return LABEL_FAMILY_MAP.get(key, key or "unknown")


def omniparser_items_to_candidates(
    items: list[Any],
    *,
    width: int,
    height: int,
    max_candidates: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        bbox = omniparser_bbox_to_pixel_box(item.get("bbox"), width=width, height=height)
        if bbox is None:
            continue
        source_type = str(item.get("type") or "unknown").lower()
        content = str(item.get("content") or "").strip()
        family = normalize_omniparser_family(item)
        semantic_source = omniparser_semantic_source(item)
        score = omniparser_score(item, family=family)
        candidate: dict[str, Any] = {
            "id": f"op-{index:03d}",
            "bbox": bbox,
            "center": bbox_center(bbox),
            "score": score,
            "label": content[:80] if content else family,
            "family": family,
            "rawLabel": source_type,
            "modelFamily": family,
            "semanticSource": semantic_source,
            "semanticConfidence": "medium" if content else "weak",
            "source": OMNIPARSER_SOURCE,
            "interactivity": bool(item.get("interactivity")),
        }
        if content:
            candidate["textHint"] = content[:180]
        original_source = item.get("source")
        if original_source:
            candidate["rawSource"] = original_source
        candidates.append(candidate)

    candidates.sort(key=lambda candidate: float(candidate.get("score") or 0.0), reverse=True)
    return candidates[:max_candidates]


def omniparser_bbox_to_pixel_box(raw_bbox: Any, *, width: int, height: int) -> dict[str, int] | None:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    values = [float(value) for value in raw_bbox]
    if max(values) <= 1.5:
        x1, y1, x2, y2 = (
            values[0] * width,
            values[1] * height,
            values[2] * width,
            values[3] * height,
        )
    else:
        x1, y1, x2, y2 = values
    x1i, y1i, x2i, y2i = clamp_xyxy([x1, y1, x2, y2], width, height)
    if x2i <= x1i or y2i <= y1i:
        return None
    return {"x": x1i, "y": y1i, "w": x2i - x1i, "h": y2i - y1i}


def normalize_omniparser_family(item: dict[str, Any]) -> str:
    source_type = str(item.get("type") or "").lower()
    content = str(item.get("content") or "").strip().lower()
    source = str(item.get("source") or "").lower()
    interactive = bool(item.get("interactivity"))

    if source_type == "text":
        if any(word in content for word in ("search", "搜索")):
            return "input"
        if any(word in content for word in ("login", "sign in", "subscribe", "create", "发布", "登录", "订阅", "创建")):
            return "button"
        return "text"

    if source_type == "icon":
        if "ocr" in source and any(word in content for word in ("search", "搜索")):
            return "input"
        if any(word in content for word in ("logo", "brand")):
            return "logo"
        if any(word in content for word in ("image", "photo", "video", "thumbnail", "picture", "poster")):
            return "visual"
        if any(word in content for word in ("menu", "more", "ellipsis", "hamburger")):
            return "menu_item"
        if any(word in content for word in ("home", "back", "next", "notification", "settings", "profile", "search", "upload", "share")):
            return "icon"
        return "button" if interactive else "icon"

    return "unknown"


def omniparser_semantic_source(item: dict[str, Any]) -> str:
    source_type = str(item.get("type") or "").lower()
    source = str(item.get("source") or "").lower()
    content = str(item.get("content") or "").strip()
    if source_type == "text" or "ocr" in source:
        return "omniparser_ocr"
    if content:
        return "omniparser_caption"
    return "omniparser_detector"


def omniparser_score(item: dict[str, Any], *, family: str) -> float:
    source_type = str(item.get("type") or "").lower()
    content = str(item.get("content") or "").strip()
    interactive = bool(item.get("interactivity"))
    score = 0.72
    if source_type == "icon":
        score = 0.84 if interactive else 0.68
    elif source_type == "text":
        score = 0.74
    if content:
        score += 0.06
    if family in {"button", "input", "icon", "logo", "visual"}:
        score += 0.03
    if family == "unknown":
        score -= 0.15
    return round(max(0.0, min(score, 1.0)), 4)


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
    """Attach geometry semantics to broad/unknown model labels.

    Lightweight UI detectors can emit broad or low-context classes on real
    websites. Keep the raw/model label for traceability, but promote high-signal
    geometry into the public family. This makes vision useful as a DOM fallback
    without pretending the model itself knew the semantic class.
    """

    family = str(candidate.get("family") or "unknown")
    if family not in {"unknown", "visual"}:
        candidate.setdefault("semanticConfidence", "model")
        return

    refined = infer_family_from_geometry(candidate, width=width, height=height)
    if family == "unknown":
        candidate["semanticSource"] = "model_unknown"
    if refined and refined != "unknown" and refined != family:
        candidate["geometryHint"] = refined
        candidate["family"] = refined
        candidate["label"] = refined
        candidate["semanticSource"] = "geometry"
        candidate["semanticConfidence"] = "weak"
    else:
        candidate.setdefault("semanticConfidence", "weak" if family == "unknown" else "model")


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
    bottom_band = y + h >= height * 0.84
    small = w <= 96 and h <= 96

    if left_band and top_band and 32 <= w <= 220 and 18 <= h <= 90:
        return "logo"
    if 10 <= w <= 72 and 10 <= h <= 72 and 0.55 <= aspect <= 1.8:
        return "icon"
    if aspect >= 5.0 and 22 <= h <= 80 and w >= 180:
        return "input"
    if 1.6 <= aspect <= 6.0 and 24 <= h <= 90 and 48 <= w <= 360:
        return "button"
    if small and (top_band or left_band or bottom_band):
        return "icon"
    if top_band and h <= 80 and w >= 60:
        return "nav_item"
    if left_band and h <= 76 and w <= width * 0.35:
        return "nav_item"
    if area_ratio >= 0.018 and w >= 120 and h >= 90:
        return "visual"
    if h <= 52 and w >= 40:
        return "text"
    return "unknown"


def effective_family(candidate: dict[str, Any]) -> str:
    family = str(candidate.get("family") or "unknown")
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
                candidate["semanticConfidence"] = "medium"
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
            "link": 0.06,
            "tab": 0.05,
            "icon": 0.05,
            "logo": 0.04,
            "nav_item": 0.04,
            "menu_item": 0.04,
            "visual": 0.03,
            "text": 0.02,
            "unknown": -0.12,
        }.get(ranking_family, 0.0)
        if item.get("semanticSource") == "geometry":
            bonus -= 0.02
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
    "link": (80, 190, 255),
    "tab": (120, 170, 255),
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
            apply_dom_hint_semantics(item, hint)
        enriched.append(item)
    return enriched


def attach_ax_hints(
    vision_candidates: list[dict[str, Any]],
    ax_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for candidate in vision_candidates:
        item = dict(candidate)
        hint = best_ax_hint(candidate, ax_candidates)
        if hint is not None:
            item["axHint"] = hint
            apply_ax_hint_semantics(item, hint)
        enriched.append(item)
    return enriched


def apply_dom_hint_semantics(candidate: dict[str, Any], hint: dict[str, Any]) -> None:
    """Use DOM only as a semantic hint for the visual box.

    This is deliberately one-way: DOM text/role can clarify a visual candidate,
    but the visual box still keeps its own bbox and source. Large media boxes
    remain visual; small or unknown boxes can inherit the DOM control family.
    """

    dom_hint_family = dom_family(hint)
    if dom_hint_family == "dom":
        return

    label = element_label(hint).strip()
    if label and not candidate.get("textHint"):
        candidate["textHint"] = label

    family = str(candidate.get("family") or "unknown")
    bbox = candidate.get("bbox") or {}
    w = int(bbox.get("w", 0))
    h = int(bbox.get("h", 0))
    area = w * h
    aspect = w / max(h, 1)
    action_like = h <= 96 and (w <= 360 or 1.4 <= aspect <= 8.0)

    if dom_hint_family == "image":
        if family in {"unknown", "text"}:
            candidate["family"] = "visual"
            candidate["label"] = "visual"
            candidate["semanticSource"] = "dom_hint"
            candidate["semanticConfidence"] = "medium"
        return

    if family == "unknown" or (family in {"text", "icon"} and action_like):
        promoted = "input" if dom_hint_family == "search_box" else dom_hint_family
        candidate["family"] = promoted
        candidate["label"] = promoted
        candidate["semanticSource"] = "dom_hint"
        candidate["semanticConfidence"] = "medium"
        return

    if family == "visual" and action_like and area <= 360 * 96 and dom_hint_family in {"button", "input", "search_box", "link", "tab", "menu_item"}:
        promoted = "input" if dom_hint_family == "search_box" else dom_hint_family
        candidate["family"] = promoted
        candidate["label"] = promoted
        candidate["semanticSource"] = "dom_hint"
        candidate["semanticConfidence"] = "medium"


def apply_ax_hint_semantics(candidate: dict[str, Any], hint: dict[str, Any]) -> None:
    family = str(candidate.get("family") or "unknown")
    ax_family_value = str(hint.get("family") or "unknown")
    label = str(hint.get("label") or "").strip()
    if label and not candidate.get("textHint"):
        candidate["textHint"] = label

    bbox = candidate.get("bbox") or {}
    w = int(bbox.get("w", 0))
    h = int(bbox.get("h", 0))
    area = w * h
    aspect = w / max(h, 1)
    action_like = h <= 96 and (w <= 380 or 1.4 <= aspect <= 8.0)

    if ax_family_value in {"button", "input", "link", "tab", "menu_item", "control"}:
        if family == "unknown" or (family in {"text", "icon"} and action_like):
            candidate["family"] = ax_family_value
            candidate["label"] = ax_family_value
            candidate["semanticSource"] = "ax_hint"
            candidate["semanticConfidence"] = "medium"
            return
        if family == "visual" and action_like and area <= 380 * 110:
            candidate["family"] = ax_family_value
            candidate["label"] = ax_family_value
            candidate["semanticSource"] = "ax_hint"
            candidate["semanticConfidence"] = "medium"


def build_mixed_candidates(
    *,
    elements: list[dict[str, Any]],
    vision_candidates: list[dict[str, Any]],
    vision_groups: list[dict[str, Any]] | None = None,
    ax_candidates: list[dict[str, Any]] | None = None,
    max_candidates: int,
    fusion_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for the public mix mode.

    Mix now uses the visual-anchor strategy: visual boxes/groups are the
    primary coordinate anchors, while DOM and AX are attached as semantic hints
    or high-quality supplements.
    """

    candidates = build_anchor_candidates(
        elements=elements,
        vision_candidates=vision_candidates,
        vision_groups=vision_groups,
        ax_candidates=ax_candidates,
        max_candidates=max_candidates,
        fusion_trace=fusion_trace,
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["id"] = f"mix-{index:03d}"
        candidate["rank"] = index
    return candidates


def build_anchor_candidates(
    *,
    elements: list[dict[str, Any]],
    vision_candidates: list[dict[str, Any]],
    vision_groups: list[dict[str, Any]] | None = None,
    ax_candidates: list[dict[str, Any]] | None = None,
    max_candidates: int,
    fusion_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a visual-first candidate list.

    Unlike mix mode, the primary boxes come from visual detections/groups. DOM
    and AX entries are pulled into those visual anchors to provide labels,
    roles, and href hints. High-confidence DOM/AX controls that vision misses
    are still appended so the mode remains usable on ordinary form pages.
    """

    vision_entries = [
        make_anchor_group_candidate(group)
        for group in (vision_groups or [])
        if is_usable_vision_candidate(group)
    ]
    vision_entries.extend(
        make_anchor_vision_candidate(candidate)
        for candidate in vision_candidates
        if is_usable_vision_candidate(candidate)
    )
    anchors = [entry for entry in vision_entries if is_visual_anchor_entry(entry)]

    dom_entries = [
        make_dom_mixed_candidate(index, element)
        for index, element in enumerate(elements, start=1)
        if is_usable_dom_element(element)
    ]
    ax_entries = [
        make_ax_mixed_candidate(index, candidate)
        for index, candidate in enumerate(ax_candidates or [], start=1)
        if is_usable_vision_candidate(candidate)
    ]

    entries: list[dict[str, Any]] = list(vision_entries)
    entries.extend(entry for entry in dom_entries if should_include_anchor_hint(entry, anchors) or is_high_quality_anchor_supplement(entry))
    entries.extend(entry for entry in ax_entries if should_include_anchor_hint(entry, anchors) or is_high_quality_anchor_supplement(entry))
    return fuse_anchor_candidates(entries, max_candidates=max_candidates, fusion_trace=fusion_trace)


def make_dom_mixed_candidate(index: int, element: dict[str, Any]) -> dict[str, Any]:
    bbox = element.get("bbox")
    center = {"x": int(element.get("x", 0)), "y": int(element.get("y", 0))}
    if not isinstance(bbox, dict):
        bbox = {"x": center["x"], "y": center["y"], "w": 1, "h": 1}
    family = dom_family(element)
    score = dom_mix_score(element, family)
    source_key = dom_source_key(element, family)
    return {
        "id": f"dom-{index:03d}",
        "bbox": bbox,
        "center": center,
        "score": round(score, 4),
        "mixScore": round(score, 4),
        "label": element_label(element),
        "family": family,
        "source": "dom",
        "sourceKey": source_key,
        "kind": "dom",
        "domHint": compact_dom_hint(element),
    }


def make_ax_mixed_candidate(index: int, candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    item["id"] = item.get("id") or f"ax-{index:03d}"
    item["source"] = "ax_tree"
    item["sourceKey"] = "ax_tree"
    item["kind"] = "ax"
    item["mixScore"] = round(float(item.get("score") or 0.0), 4)
    item.setdefault("label", item.get("role") or item.get("family") or "ax")
    item.setdefault("family", "unknown")
    return item


def make_vision_mixed_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    family = str(item.get("family") or item.get("label") or "unknown")
    raw_score = float(item.get("score") or 0.0)
    source_bonus = 0.14 if "domHint" not in item else 0.03
    family_bonus = {
        "button": 0.08,
        "input": 0.08,
        "link": 0.06,
        "tab": 0.05,
        "nav_item": 0.05,
        "menu_item": 0.05,
        "visual": 0.07,
        "logo": 0.06,
        "icon": 0.05,
        "text": 0.02,
        "unknown": -0.12,
    }.get(family, 0.0)
    item["mixScore"] = round(max(0.0, min(raw_score + source_bonus + family_bonus, 1.0)), 4)
    item["kind"] = "vision_supplement"
    item["sourceKey"] = "vision_yolo"
    item.setdefault("supplementReason", "dom_blind_spot" if "domHint" not in item else "visual_context")
    return item


def make_vision_group_mixed_candidate(group: dict[str, Any]) -> dict[str, Any]:
    item = dict(group)
    item["source"] = "vision_group"
    item["sourceKey"] = "vision_group"
    item["kind"] = "vision_supplement_group"
    item["score"] = round(float(item.get("score") or item.get("mixScore") or 0.0), 4)
    item["mixScore"] = round(min(float(item.get("mixScore") or item["score"]) + 0.08, 1.0), 4)
    item.setdefault("supplementReason", "visual_group")
    return item


def make_anchor_vision_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = make_vision_mixed_candidate(candidate)
    item["kind"] = "vision_anchor"
    item["anchorRole"] = "visual_atom"
    item["mixScore"] = round(min(float(item.get("mixScore") or 0.0) + 0.06, 1.0), 4)
    item.setdefault("supplementReason", "vision_anchor")
    return item


def make_anchor_group_candidate(group: dict[str, Any]) -> dict[str, Any]:
    item = make_vision_group_mixed_candidate(group)
    item["kind"] = "vision_anchor_group"
    item["anchorRole"] = "visual_group"
    item["mixScore"] = round(min(float(item.get("mixScore") or 0.0) + 0.08, 1.0), 4)
    item.setdefault("supplementReason", "vision_group_anchor")
    return item


MIX_SOURCE_PRIOR = {
    "dom_explicit": 0.95,
    "ax_tree": 0.9,
    "dom_clickable": 0.82,
    "vision_group": 0.72,
    "vision_yolo": 0.58,
    "dom_generic": 0.55,
    "ocr_text": 0.45,
    "unknown": 0.25,
}

MIX_CONTROL_FAMILIES = {"button", "input", "search_box", "link", "tab", "menu_item", "control", "icon"}
MIX_LARGE_VISUAL_FAMILIES = {"card", "video_card", "media_item", "visual", "panel", "window", "toolbar", "nav_cluster"}
ANCHOR_PRESERVED_ATOM_FAMILIES = {
    "button",
    "input",
    "search_box",
    "nav_item",
    "link",
    "icon",
    "text",
    "visual",
    "logo",
    "tab",
    "menu_item",
    "control",
}


def fuse_mixed_candidates(
    entries: list[dict[str, Any]],
    *,
    max_candidates: int,
    fusion_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    max_candidates = max(1, max_candidates)
    clusters = cluster_mixed_entries(entries)
    if fusion_trace is not None:
        fusion_trace["fusion_cluster_count"] = len(clusters)
        fusion_trace["fusion_input_count"] = len(entries)
    fused = [fuse_mixed_cluster(cluster) for cluster in clusters]
    fused.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    accepted: list[dict[str, Any]] = []
    for candidate in fused:
        if should_drop_due_to_existing(candidate, accepted):
            continue
        accepted.append(candidate)
        if len(accepted) >= max_candidates:
            break
    for index, candidate in enumerate(accepted, start=1):
        candidate["id"] = f"mix-{index:03d}"
        candidate["rank"] = index
    return accepted


def fuse_anchor_candidates(
    entries: list[dict[str, Any]],
    *,
    max_candidates: int,
    fusion_trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    max_candidates = max(1, max_candidates)
    clusters = cluster_anchor_entries(entries)
    if fusion_trace is not None:
        fusion_trace["fusion_cluster_count"] = len(clusters)
        fusion_trace["fusion_input_count"] = len(entries)
    fused = [fuse_anchor_cluster(cluster) for cluster in clusters]
    fused.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    accepted: list[dict[str, Any]] = []
    for candidate in fused:
        if should_drop_due_to_existing(candidate, accepted):
            continue
        accepted.append(candidate)
        if len(accepted) >= max_candidates:
            break
    for index, candidate in enumerate(accepted, start=1):
        candidate["id"] = f"anchor-{index:03d}"
        candidate["rank"] = index
    return accepted


def cluster_anchor_entries(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for entry in entries:
        if not is_usable_vision_candidate(entry):
            continue
        for cluster in clusters:
            if any(should_cluster_anchor_entries(entry, existing) for existing in cluster):
                cluster.append(entry)
                break
        else:
            clusters.append([entry])
    return clusters


def should_cluster_anchor_entries(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_box = left.get("bbox") or {}
    right_box = right.get("bbox") or {}
    if protects_anchor_atom(left, right) or protects_anchor_atom(right, left):
        return False
    if bbox_iou(left_box, right_box) >= 0.42:
        return True
    if public_source(left) == "vision" and public_source(right) == "vision":
        return False
    if is_visual_anchor_entry(left) and anchor_hint_matches(right, left):
        return True
    if is_visual_anchor_entry(right) and anchor_hint_matches(left, right):
        return True
    return False


def protects_anchor_atom(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    """Keep atom-level visual detections separate from large visual anchors.

    ANCHOR mode still lets DOM/AX hints attach to big visual boxes for labels,
    but YOLO atom boxes such as buttons, nav items, icons, and text should not
    disappear into card/video/list groups. Those atoms are often the actual
    click targets the agent needs.
    """

    outer_family = str(outer.get("family") or "unknown")
    inner_family = str(inner.get("family") or "unknown")
    outer_source = public_source(outer)
    if outer_source not in {"vision", "vision_group"} or public_source(inner) != "vision":
        return False
    if outer_family not in MIX_LARGE_VISUAL_FAMILIES or inner_family not in ANCHOR_PRESERVED_ATOM_FAMILIES:
        return False
    if outer_source == "vision_group":
        return True
    outer_box = outer.get("bbox") or {}
    inner_box = inner.get("bbox") or {}
    inner_center = inner.get("center") or bbox_center(inner_box)
    is_inside = bbox_contains(outer_box, inner_box) or point_inside_bbox(
        int(inner_center.get("x", -1)),
        int(inner_center.get("y", -1)),
        outer_box,
    )
    if not is_inside:
        return False
    return box_area(outer_box) >= box_area(inner_box) * 1.8


def cluster_mixed_entries(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for entry in entries:
        if not is_usable_vision_candidate(entry):
            continue
        for cluster in clusters:
            if any(should_cluster_mixed_entries(entry, existing) for existing in cluster):
                cluster.append(entry)
                break
        else:
            clusters.append([entry])
    return clusters


def should_cluster_mixed_entries(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_box = left.get("bbox") or {}
    right_box = right.get("bbox") or {}
    iou = bbox_iou(left_box, right_box)
    if iou >= 0.42:
        return True
    if protects_nested_control(left, right) or protects_nested_control(right, left):
        return False
    if bbox_contains(left_box, right_box) or bbox_contains(right_box, left_box):
        left_area = box_area(left_box)
        right_area = box_area(right_box)
        if min(left_area, right_area) / max(max(left_area, right_area), 1) >= 0.35:
            return True
    return False


def protects_nested_control(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    outer_family = str(outer.get("family") or "unknown")
    inner_family = str(inner.get("family") or "unknown")
    if outer_family not in MIX_LARGE_VISUAL_FAMILIES or inner_family not in MIX_CONTROL_FAMILIES:
        return False
    outer_box = outer.get("bbox") or {}
    inner_box = inner.get("bbox") or {}
    return bbox_contains(outer_box, inner_box) and box_area(outer_box) >= box_area(inner_box) * 2.5


def fuse_anchor_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    representative = anchor_representative(cluster)
    family = anchor_cluster_family(cluster, representative)
    label = cluster_label(cluster, family)
    bbox = representative.get("bbox") or union_bbox([item.get("bbox") or {} for item in cluster])
    sources = sorted({public_source(item) for item in cluster})
    score = score_anchor_cluster(cluster, representative=representative, family=family)
    result = {
        "id": representative.get("id"),
        "bbox": bbox,
        "center": bbox_center(bbox),
        "score": score,
        "mixScore": score,
        "family": family,
        "label": label,
        "source": "anchor_fusion",
        "kind": "anchor_fusion",
        "sources": sources,
        "sourceSummary": "+".join(sources),
        "anchorSource": public_source(representative),
    }
    for item in cluster:
        if item.get("domHint") and "domHint" not in result:
            result["domHint"] = item["domHint"]
        if item.get("axHint") and "axHint" not in result:
            result["axHint"] = item["axHint"]
        if public_source(item) in {"vision", "vision_group"} and "visionHint" not in result:
            result["visionHint"] = compact_vision_hint(item)
        if item.get("textHint") and not result.get("textHint"):
            result["textHint"] = item["textHint"]
    if any(public_source(item) == "dom" and item.get("domHint") for item in cluster) and "domHint" not in result:
        result["domHint"] = next(item["domHint"] for item in cluster if public_source(item) == "dom" and item.get("domHint"))
    if any(public_source(item) == "ax_tree" and item.get("axHint") for item in cluster) and "axHint" not in result:
        result["axHint"] = next(item["axHint"] for item in cluster if public_source(item) == "ax_tree" and item.get("axHint"))
    return result


def anchor_representative(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(item: dict[str, Any]) -> tuple[float, float, int]:
        source = public_source(item)
        source_rank = {
            "vision_group": 4.0,
            "vision": 3.0,
            "ax_tree": 2.0,
            "dom": 1.0,
        }.get(source, 0.0)
        return (
            source_rank,
            float(item.get("mixScore", item.get("score", 0.0)) or 0.0),
            box_area(item.get("bbox") or {}),
        )

    return max(cluster, key=rank)


def anchor_cluster_family(cluster: list[dict[str, Any]], representative: dict[str, Any]) -> str:
    representative_family = str(representative.get("family") or "unknown")
    representative_source = public_source(representative)
    if representative_source == "vision_group" and representative_family in {
        "video_card",
        "card",
        "media_item",
        "list_item",
        "toolbar",
        "nav_cluster",
    }:
        return representative_family
    if representative_source == "vision" and representative_family in {"visual", "logo"}:
        return representative_family
    return cluster_family(cluster)


def fuse_mixed_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    representative = max(
        cluster,
        key=lambda item: (
            source_prior(item),
            float(item.get("mixScore", item.get("score", 0.0)) or 0.0),
            -box_area(item.get("bbox") or {}),
        ),
    )
    family = cluster_family(cluster)
    label = cluster_label(cluster, family)
    bbox = representative.get("bbox") or union_bbox([item.get("bbox") or {} for item in cluster])
    sources = sorted({public_source(item) for item in cluster})
    score = score_mixed_cluster(cluster, representative=representative, family=family)
    result = {
        "id": representative.get("id"),
        "bbox": bbox,
        "center": bbox_center(bbox),
        "score": score,
        "mixScore": score,
        "family": family,
        "label": label,
        "source": "fusion",
        "kind": "fusion",
        "sources": sources,
        "sourceSummary": "+".join(sources),
    }
    for item in cluster:
        if item.get("domHint") and "domHint" not in result:
            result["domHint"] = item["domHint"]
        if item.get("axHint") and "axHint" not in result:
            result["axHint"] = item["axHint"]
        if public_source(item) in {"vision", "vision_group"} and "visionHint" not in result:
            result["visionHint"] = compact_vision_hint(item)
        if item.get("textHint") and not result.get("textHint"):
            result["textHint"] = item["textHint"]
    return result


def score_anchor_cluster(cluster: list[dict[str, Any]], *, representative: dict[str, Any], family: str) -> float:
    score = score_mixed_cluster(cluster, representative=representative, family=family)
    sources = {public_source(item) for item in cluster}
    if "vision_group" in sources:
        score += 0.06
    elif "vision" in sources:
        score += 0.04
    if ("dom" in sources or "ax_tree" in sources) and ("vision" in sources or "vision_group" in sources):
        score += 0.05
    if family == "unknown":
        score -= 0.08
    return round(max(0.0, min(score, 1.0)), 4)


def score_mixed_cluster(cluster: list[dict[str, Any]], *, representative: dict[str, Any], family: str) -> float:
    source_score = max(source_prior(item) for item in cluster)
    model_score = max(float(item.get("mixScore", item.get("score", 0.0)) or 0.0) for item in cluster)
    semantic_bonus = semantic_family_bonus(cluster, family)
    spatial_bonus = spatial_candidate_bonus(representative)
    consensus_bonus = mixed_consensus_bonus(cluster)
    size_penalty = mixed_size_penalty(representative, family)
    noise_penalty = mixed_noise_penalty(cluster, family)
    score = (
        0.40 * source_score
        + 0.20 * model_score
        + 0.20 * semantic_bonus
        + 0.12 * spatial_bonus
        + 0.12 * consensus_bonus
        - size_penalty
        - noise_penalty
    )
    return round(max(0.0, min(score, 1.0)), 4)


def cluster_family(cluster: list[dict[str, Any]]) -> str:
    for source in ("ax_tree", "dom", "vision_group", "vision"):
        for item in cluster:
            if public_source(item) == source:
                family = str(item.get("family") or "unknown")
                if family != "unknown":
                    return "input" if family == "search_box" else family
    return str(cluster[0].get("family") or "unknown")


def cluster_label(cluster: list[dict[str, Any]], family: str) -> str:
    for source in ("ax_tree", "dom", "vision_group", "vision"):
        for item in cluster:
            if public_source(item) != source:
                continue
            label = str(item.get("label") or item.get("textHint") or "").strip()
            if label and label.lower() not in {"unknown", "visual", "button", "input", "link", "text"}:
                return label[:120]
    return family


def source_prior(item: dict[str, Any]) -> float:
    return MIX_SOURCE_PRIOR.get(str(item.get("sourceKey") or public_source(item) or "unknown"), MIX_SOURCE_PRIOR["unknown"])


def public_source(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "")
    if source == "ax_tree":
        return "ax_tree"
    if source == "dom":
        return "dom"
    if source == "vision_group":
        return "vision_group"
    if source in {SOURCE, OMNIPARSER_SOURCE} or str(item.get("kind", "")).startswith("vision"):
        return "vision"
    return source or "unknown"


def is_visual_anchor_entry(entry: dict[str, Any]) -> bool:
    return public_source(entry) in {"vision", "vision_group"} and is_usable_vision_candidate(entry)


def should_include_anchor_hint(entry: dict[str, Any], anchors: list[dict[str, Any]]) -> bool:
    return any(anchor_hint_matches(entry, anchor) for anchor in anchors)


def anchor_hint_matches(entry: dict[str, Any], anchor: dict[str, Any]) -> bool:
    entry_box = entry.get("bbox") or {}
    anchor_box = anchor.get("bbox") or {}
    if bbox_iou(entry_box, anchor_box) >= 0.28:
        return True
    entry_center = entry.get("center") or bbox_center(entry_box)
    anchor_center = anchor.get("center") or bbox_center(anchor_box)
    if point_inside_bbox(int(entry_center.get("x", -1)), int(entry_center.get("y", -1)), anchor_box):
        return True
    if point_inside_bbox(int(anchor_center.get("x", -1)), int(anchor_center.get("y", -1)), entry_box):
        return True
    if bbox_contains(anchor_box, entry_box):
        return True
    if bbox_contains(entry_box, anchor_box):
        return box_area(anchor_box) / max(box_area(entry_box), 1) >= 0.25
    return False


def is_high_quality_anchor_supplement(entry: dict[str, Any]) -> bool:
    family = str(entry.get("family") or "unknown")
    if family not in {"button", "input", "search_box", "link", "tab", "menu_item", "control"}:
        return False
    label = str(entry.get("label") or entry.get("textHint") or "").strip()
    if public_source(entry) == "ax_tree":
        label = label or str((entry.get("axHint") or {}).get("name") or "").strip()
    if public_source(entry) == "dom":
        label = label or str((entry.get("domHint") or {}).get("text") or "").strip()
    return bool(label) and box_area(entry.get("bbox") or {}) >= 16


def semantic_family_bonus(cluster: list[dict[str, Any]], family: str) -> float:
    if any(item.get("textHint") or item.get("domHint") or item.get("axHint") for item in cluster):
        base = 0.62
    else:
        base = 0.35
    if family in {"button", "input", "link", "tab", "menu_item", "control"}:
        base += 0.28
    elif family in {"video_card", "card", "media_item", "visual", "list_item"}:
        base += 0.16
    elif family == "unknown":
        base -= 0.24
    return max(0.0, min(base, 1.0))


def spatial_candidate_bonus(candidate: dict[str, Any]) -> float:
    bbox = candidate.get("bbox") or {}
    w = int(bbox.get("w", 0))
    h = int(bbox.get("h", 0))
    if w <= 0 or h <= 0:
        return 0.0
    aspect = w / max(h, 1)
    family = str(candidate.get("family") or "unknown")
    if family in {"button", "input", "link", "tab", "menu_item"} and h <= 96 and aspect >= 1.0:
        return 0.75
    if family in {"icon", "control"} and w <= 96 and h <= 96:
        return 0.65
    if family in {"video_card", "card", "media_item", "visual"} and w >= 100 and h >= 80:
        return 0.58
    return 0.35


def mixed_consensus_bonus(cluster: list[dict[str, Any]]) -> float:
    sources = {public_source(item) for item in cluster}
    if {"dom", "ax_tree", "vision"} <= sources or {"dom", "ax_tree", "vision_group"} <= sources:
        return 1.0
    if {"dom", "ax_tree"} <= sources:
        return 0.85
    if {"dom", "vision"} <= sources or {"dom", "vision_group"} <= sources:
        return 0.62
    if {"ax_tree", "vision"} <= sources or {"ax_tree", "vision_group"} <= sources:
        return 0.62
    return 0.15


def mixed_size_penalty(candidate: dict[str, Any], family: str) -> float:
    bbox = candidate.get("bbox") or {}
    area = box_area(bbox)
    if area <= 0:
        return 0.2
    if family in MIX_LARGE_VISUAL_FAMILIES:
        return 0.0
    if area > 500_000:
        return 0.18
    if area > 220_000:
        return 0.08
    return 0.0


def mixed_noise_penalty(cluster: list[dict[str, Any]], family: str) -> float:
    if family == "unknown":
        return 0.2
    if len(cluster) == 1 and public_source(cluster[0]) == "vision" and family in {"text", "unknown"}:
        return 0.12
    return 0.0


def should_drop_due_to_existing(candidate: dict[str, Any], accepted: list[dict[str, Any]]) -> bool:
    for existing in accepted:
        if protects_nested_control(existing, candidate) or protects_nested_control(candidate, existing):
            continue
        if bbox_iou(candidate.get("bbox") or {}, existing.get("bbox") or {}) >= 0.72:
            return True
    return False


def dom_source_key(element: dict[str, Any], family: str) -> str:
    tag = str(element.get("tag") or "").lower()
    attrs = element.get("attrs") or {}
    role = str(attrs.get("role") or "").lower()
    if family in {"button", "input", "search_box", "link", "tab", "menu_item"}:
        return "dom_explicit" if tag in {"a", "button", "input", "textarea", "select"} or role else "dom_clickable"
    return "dom_generic"


def compact_vision_hint(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "family": item.get("family"),
        "label": item.get("label"),
        "rawLabel": item.get("rawLabel"),
        "source": item.get("source"),
        "bbox": item.get("bbox"),
        "center": item.get("center"),
    }


def box_area(bbox: dict[str, Any]) -> int:
    return max(int(bbox.get("w", 0)), 0) * max(int(bbox.get("h", 0)), 0)


def is_usable_dom_element(element: dict[str, Any]) -> bool:
    x = int(element.get("x", -1))
    y = int(element.get("y", -1))
    if x < 0 or y < 0:
        return False
    family = dom_family(element)
    label = element_label(element).strip()
    bbox = element.get("bbox")
    if isinstance(bbox, dict) and (int(bbox.get("w", 0)) <= 0 or int(bbox.get("h", 0)) <= 0):
        return False
    return family != "dom" or bool(label)


def is_usable_vision_candidate(candidate: dict[str, Any]) -> bool:
    bbox = candidate.get("bbox")
    if not isinstance(bbox, dict):
        return False
    return int(bbox.get("w", 0)) > 0 and int(bbox.get("h", 0)) > 0


def is_vision_dom_supplement(candidate: dict[str, Any], elements: list[dict[str, Any]], *, is_group: bool = False) -> bool:
    """Return true when a visual candidate adds information beyond DOM.

    In mix mode vision is a fallback layer, so a visual box that merely repeats
    a known DOM button/link should not spend candidate budget. Media boxes,
    card-like groups, icons/logos, and candidates with no meaningful DOM hit are
    kept because those are the places DOM observe tends to be thin.
    """

    if not is_usable_vision_candidate(candidate):
        return False
    family = str(candidate.get("family") or candidate.get("label") or "unknown")
    bbox = candidate.get("bbox") or {}
    area = int(bbox.get("w", 0)) * int(bbox.get("h", 0))

    if is_group:
        if family in {"video_card", "media_item", "card", "list_item", "toolbar", "nav_cluster"}:
            return not has_single_strong_dom_equivalent(candidate, elements)
        return False

    if family in {"visual", "logo"}:
        return True
    if family == "icon":
        return not has_actionable_dom_hit(candidate, elements)
    if family == "unknown":
        return not has_actionable_dom_hit(candidate, elements) and area >= 900
    if family in {"button", "input", "link", "tab", "menu_item", "nav_item", "text"}:
        return not has_actionable_dom_hit(candidate, elements)
    return not has_actionable_dom_hit(candidate, elements)


def has_actionable_dom_hit(candidate: dict[str, Any], elements: list[dict[str, Any]]) -> bool:
    bbox = candidate.get("bbox") or {}
    for element in elements:
        if not is_usable_dom_element(element):
            continue
        family = dom_family(element)
        if family == "dom":
            continue
        if point_inside_bbox(int(element.get("x", -1)), int(element.get("y", -1)), bbox):
            return True
        element_bbox = element.get("bbox")
        if isinstance(element_bbox, dict) and bbox_iou(bbox, element_bbox) >= 0.45:
            return True
    return False


def has_single_strong_dom_equivalent(candidate: dict[str, Any], elements: list[dict[str, Any]]) -> bool:
    bbox = candidate.get("bbox") or {}
    for element in elements:
        if not is_usable_dom_element(element):
            continue
        family = dom_family(element)
        if family == "dom":
            continue
        element_bbox = element.get("bbox")
        if not isinstance(element_bbox, dict):
            continue
        if bbox_iou(bbox, element_bbox) >= 0.65:
            return True
    return False


def dedupe_mixed_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: item.get("mixScore", item.get("score", 0.0)), reverse=True):
        bbox = entry.get("bbox") or {}
        if any(bbox_iou(bbox, other.get("bbox") or {}) >= 0.72 for other in kept):
            continue
        kept.append(entry)
    return kept


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


def best_ax_hint(candidate: dict[str, Any], ax_candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    bbox = candidate.get("bbox") or {}
    cx = int(candidate.get("center", {}).get("x", bbox.get("x", 0)))
    cy = int(candidate.get("center", {}).get("y", bbox.get("y", 0)))
    hits = []
    for ax_candidate in ax_candidates:
        if not is_usable_vision_candidate(ax_candidate):
            continue
        ax_box = ax_candidate.get("bbox") or {}
        ax_center = ax_candidate.get("center") or bbox_center(ax_box)
        if point_inside_bbox(int(ax_center.get("x", -1)), int(ax_center.get("y", -1)), bbox) or bbox_iou(bbox, ax_box) >= 0.35:
            hits.append(ax_candidate)
    if not hits:
        return None
    best = min(
        hits,
        key=lambda item: (
            0 if str(item.get("family") or "") in {"button", "input", "link", "tab", "menu_item", "control"} else 1,
            0 if item.get("label") else 1,
            abs(int((item.get("center") or {}).get("x", 0)) - cx) + abs(int((item.get("center") or {}).get("y", 0)) - cy),
        ),
    )
    return compact_ax_hint(best)


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


def compact_ax_hint(candidate: dict[str, Any]) -> dict[str, Any]:
    ax_hint = candidate.get("axHint") if isinstance(candidate.get("axHint"), dict) else {}
    return {
        "role": candidate.get("role") or ax_hint.get("role"),
        "name": candidate.get("label") or ax_hint.get("name"),
        "family": candidate.get("family"),
        "label": candidate.get("label"),
        "center": candidate.get("center"),
        "bbox": candidate.get("bbox"),
        "backendDOMNodeId": ax_hint.get("backendDOMNodeId"),
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


ui_detector = VisionDetector()
