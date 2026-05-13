#!/usr/bin/env python3
"""Collect viewport screenshots and weak DOM-derived UI labels.

The goal is to bootstrap a small web-UI detector dataset quickly:

- image: WebDriver viewport screenshot, not browser chrome.
- dom: raw Browser Pilot DOM observe output for auditing.
- labels: weak JSON labels inferred from DOM attributes, roles, text, and bbox.
- yolo: optional YOLO txt labels using the same class list.

These labels are intentionally marked as weak so they can be reviewed before
training a production model.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLASS_NAMES = [
    "button",
    "input",
    "icon",
    "logo",
    "nav_item",
    "tab",
    "menu_item",
    "link",
    "card",
    "video_card",
    "image",
    "avatar",
    "checkbox_toggle",
    "list_item",
    "text_block",
]
CLASS_INDEX = {name: index for index, name in enumerate(CLASS_NAMES)}

DEFAULT_URLS = [
    ("youtube_home", "https://www.youtube.com/"),
    ("youtube_cats", "https://www.youtube.com/results?search_query=cats"),
    ("csdn_home", "https://www.csdn.net/"),
    ("github_trending", "https://github.com/trending"),
    ("x_home", "https://x.com/home"),
    ("xiaohongshu_search", "https://www.xiaohongshu.com/search_result?keyword=agent"),
]


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} failed with HTTP {exc.code}: {text}") from exc


def login(base_url: str, email: str, password: str) -> str:
    data = request_json(base_url, "/api/auth/login", {"email": email, "password": password})
    token = data.get("access_token") or data.get("token")
    if not token:
        raise RuntimeError(f"login did not return a token: {data}")
    return str(token)


def create_session(base_url: str, token: str, name: str) -> str:
    data = request_json(base_url, "/api/sessions", {"name": name}, token=token)
    session_id = data.get("id")
    if not session_id:
        raise RuntimeError(f"create session did not return an id: {data}")
    ensure_ok(
        request_json(base_url, f"/api/sessions/{session_id}/container/start", {}, token=token, timeout=180),
        "start session container",
    )
    wait_browser_ready(base_url, token, str(session_id))
    return str(session_id)


def ensure_ok(data: dict[str, Any], action: str) -> None:
    if data.get("ok") is False:
        raise RuntimeError(f"{action} failed: {data.get('error') or data}")


def parse_page(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        parsed = urllib.parse.urlparse(raw)
        name = (parsed.netloc or "page").replace("www.", "").replace(".", "_")
        return slugify(name), raw
    name, url = raw.split("=", 1)
    return slugify(name.strip() or "page"), url.strip()


def read_url_file(path: str) -> list[tuple[str, str]]:
    pages: list[tuple[str, str]] = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pages.append(parse_page(line))
    return pages


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip().lower())
    return value.strip("_") or "page"


def save_base64_png(path: Path, data: str) -> None:
    if data.startswith("data:image"):
        data = data.split(",", 1)[1]
    path.write_bytes(base64.b64decode(data))


def wait_for_settle(base_url: str, token: str, session_id: str, seconds: float) -> None:
    time.sleep(seconds)
    for _ in range(3):
        data = request_json(
            base_url,
            "/api/browser/observe",
            {"sessionId": session_id, "mode": "dom", "maxCandidates": 5},
            token=token,
            timeout=30,
        )
        if data.get("ok") is not False:
            return
        time.sleep(1.0)


def wait_browser_ready(base_url: str, token: str, session_id: str) -> None:
    last: dict[str, Any] = {}
    for _ in range(30):
        time.sleep(1.0)
        last = request_json(
            base_url,
            "/api/browser/observe",
            {"sessionId": session_id, "mode": "dom", "maxCandidates": 5},
            token=token,
            timeout=30,
        )
        if last.get("ok") is not False:
            return
    raise RuntimeError(f"browser did not become ready: {last}")


def navigate_url(base_url: str, token: str, session_id: str, url: str) -> None:
    data = request_json(base_url, "/api/browser/navigate", {"sessionId": session_id, "url": url}, token=token)
    if data.get("ok") is not False:
        return
    fallback = request_json(base_url, "/api/docker/navigate", {"sessionId": session_id, "url": url}, token=token)
    ensure_ok(fallback, f"navigate {url}")


def capture_dom(
    base_url: str,
    token: str,
    session_id: str,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(5):
        last = request_json(
            base_url,
            "/api/browser/observe",
            {"sessionId": session_id, "mode": "dom", "includeScreenshot": True},
            token=token,
            timeout=90,
        )
        if last.get("ok") is not False and last.get("screenshot"):
            return last
        time.sleep(1.0)
    ensure_ok(last, "observe dom")
    raise RuntimeError("observe did not return screenshot")


def build_labels(observe: dict[str, Any], *, include_low_confidence: bool) -> list[dict[str, Any]]:
    viewport = infer_viewport(observe)
    elements = list(observe.get("elements") or [])
    labels: list[dict[str, Any]] = []
    for index, element in enumerate(elements, start=1):
        label = label_from_element(element, viewport=viewport, index=index)
        if label is None:
            continue
        if not include_low_confidence and label["confidence"] < 0.7:
            continue
        labels.append(label)
    return dedupe_labels(labels)


def infer_viewport(observe: dict[str, Any]) -> dict[str, int]:
    viewport = observe.get("viewport")
    if isinstance(viewport, dict) and viewport.get("width") and viewport.get("height"):
        return {"width": int(viewport["width"]), "height": int(viewport["height"])}
    max_x = 1280
    max_y = 720
    for element in observe.get("elements") or []:
        bbox = element.get("bbox") or {}
        max_x = max(max_x, int(bbox.get("x", 0)) + int(bbox.get("w", 0)))
        max_y = max(max_y, int(bbox.get("y", 0)) + int(bbox.get("h", 0)))
    return {"width": max_x, "height": max_y}


def label_from_element(element: dict[str, Any], *, viewport: dict[str, int], index: int) -> dict[str, Any] | None:
    bbox = clamp_bbox(element.get("bbox") or {}, viewport)
    if not bbox or bbox["w"] < 4 or bbox["h"] < 4:
        return None
    viewport_area = max(viewport["width"] * viewport["height"], 1)
    area_ratio = (bbox["w"] * bbox["h"]) / viewport_area
    if area_ratio > 0.72:
        return None

    category, confidence, reason = classify_element(element, bbox=bbox, viewport=viewport)
    if category is None:
        return None

    text = clean_text(element.get("text") or "")
    attrs = element.get("attrs") if isinstance(element.get("attrs"), dict) else {}
    return {
        "id": f"dom-{index:04d}",
        "category": category,
        "classId": CLASS_INDEX[category],
        "bbox": bbox,
        "center": {"x": bbox["x"] + bbox["w"] // 2, "y": bbox["y"] + bbox["h"] // 2},
        "confidence": round(confidence, 3),
        "source": "dom_weak",
        "reason": reason,
        "needsReview": confidence < 0.75 or category in {"card", "video_card", "list_item"},
        "element": {
            "tag": element.get("tag"),
            "text": text[:160],
            "attrs": compact_attrs(attrs),
            "scope": element.get("scope"),
        },
    }


def classify_element(
    element: dict[str, Any],
    *,
    bbox: dict[str, int],
    viewport: dict[str, int],
) -> tuple[str | None, float, str]:
    tag = str(element.get("tag") or "").lower()
    attrs = element.get("attrs") if isinstance(element.get("attrs"), dict) else {}
    role = str(attrs.get("role") or "").lower()
    input_type = str(attrs.get("type") or "").lower()
    text = clean_text(" ".join(str(value or "") for value in [element.get("text"), attrs.get("ariaLabel"), attrs.get("placeholder"), attrs.get("alt")]))
    href = str(attrs.get("href") or "")
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    aspect = w / max(h, 1)
    top_band = y <= viewport["height"] * 0.16
    left_band = x <= viewport["width"] * 0.18
    squareish = 0.65 <= aspect <= 1.55
    small_square = squareish and 12 <= w <= 96 and 12 <= h <= 96
    button_like = 1.35 <= aspect <= 8.0 and 20 <= h <= 96 and 36 <= w <= 420
    row_like = w >= viewport["width"] * 0.22 and 28 <= h <= 130
    card_like = w >= 120 and h >= 90

    if tag in {"input", "textarea", "select"} or role in {"textbox", "searchbox", "combobox"}:
        if input_type in {"checkbox", "radio"}:
            return "checkbox_toggle", 0.95, "input checkbox/radio"
        return "input", 0.96, "form control"
    if role in {"checkbox", "radio", "switch"}:
        return "checkbox_toggle", 0.93, f"role={role}"
    if role == "tab":
        return "tab", 0.92, "role=tab"
    if role in {"menuitem", "option"} or tag == "option":
        return "menu_item", 0.9, f"role/tag={role or tag}"
    if tag == "button" or role == "button":
        return "icon" if small_square and not text else "button", 0.93, "button role/tag"
    if tag == "summary":
        return "button", 0.86, "summary disclosure"

    if tag == "img":
        lowered = text.lower()
        if "logo" in lowered or (top_band and x <= viewport["width"] * 0.35 and w <= 260 and h <= 120):
            return "logo", 0.84, "image logo heuristic"
        if small_square and any(word in lowered for word in ("avatar", "profile", "user", "作者", "头像")):
            return "avatar", 0.82, "image avatar text"
        if small_square and not top_band:
            return "avatar", 0.62, "small square image"
        return "image", 0.86, "image tag"

    if tag == "a" or role == "link":
        if top_band or left_band:
            return "nav_item", 0.82, "link in nav band"
        if is_video_href(href) and card_like:
            return "video_card", 0.78, "video link card"
        if card_like and w >= viewport["width"] * 0.18:
            return "card", 0.68, "large link card"
        return "link", 0.86, "link role/tag"

    if small_square:
        return "icon", 0.64, "small clickable square"
    if top_band or (left_band and h <= 110):
        return "nav_item", 0.66, "clickable in nav band"
    if button_like and text:
        return "button", 0.65, "clickable button geometry"
    if row_like and text:
        return "list_item", 0.6, "wide clickable row"
    if card_like:
        return "video_card" if looks_video_text(text, href) else "card", 0.56, "large clickable region"
    if text and w >= 30 and h >= 12:
        return "text_block", 0.42, "text fallback"
    return None, 0.0, "unlabeled"


def is_video_href(href: str) -> bool:
    lowered = href.lower()
    return any(token in lowered for token in ("/watch", "/video", "youtube", "bilibili", "douyin"))


def looks_video_text(text: str, href: str) -> bool:
    lowered = f"{text} {href}".lower()
    return any(token in lowered for token in ("watch", "video", "shorts", "youtube", "bilibili", "播放", "视频", "直播"))


def clamp_bbox(raw: dict[str, Any], viewport: dict[str, int]) -> dict[str, int] | None:
    x = int(raw.get("x", 0))
    y = int(raw.get("y", 0))
    w = int(raw.get("w", 0))
    h = int(raw.get("h", 0))
    x1 = max(0, min(x, viewport["width"]))
    y1 = max(0, min(y, viewport["height"]))
    x2 = max(0, min(x + w, viewport["width"]))
    y2 = max(0, min(y + h, viewport["height"]))
    if x2 <= x1 or y2 <= y1:
        return None
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def dedupe_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels.sort(key=lambda item: (item["confidence"], item["bbox"]["w"] * item["bbox"]["h"]), reverse=True)
    kept: list[dict[str, Any]] = []
    for label in labels:
        if any(label["category"] == other["category"] and bbox_iou(label["bbox"], other["bbox"]) >= 0.82 for other in kept):
            continue
        kept.append(label)
    kept.sort(key=lambda item: (item["bbox"]["y"], item["bbox"]["x"], item["category"]))
    for index, label in enumerate(kept, start=1):
        label["id"] = f"label-{index:04d}"
    return kept


def bbox_iou(a: dict[str, int], b: dict[str, int]) -> float:
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = max(a["w"] * a["h"] + b["w"] * b["h"] - inter, 1)
    return inter / union


def compact_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    allowed = ["id", "name", "type", "role", "ariaLabel", "placeholder", "href", "alt", "src"]
    return {key: attrs[key] for key in allowed if key in attrs and attrs[key] not in (None, "")}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def write_yolo(path: Path, labels: list[dict[str, Any]], viewport: dict[str, int]) -> None:
    lines: list[str] = []
    for label in labels:
        bbox = label["bbox"]
        cx = (bbox["x"] + bbox["w"] / 2) / viewport["width"]
        cy = (bbox["y"] + bbox["h"] / 2) / viewport["height"]
        w = bbox["w"] / viewport["width"]
        h = bbox["h"] / viewport["height"]
        lines.append(f"{label['classId']} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_dataset_yaml(path: Path, root: Path) -> None:
    names = "\n".join(f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES))
    path.write_text(
        f"path: {root}\ntrain: images\nval: images\nnames:\n{names}\n",
        encoding="utf-8",
    )


def save_capture(
    *,
    root: Path,
    image_dir: Path,
    label_dir: Path,
    dom_dir: Path,
    yolo_dir: Path,
    stem: str,
    requested_url: str,
    observe: dict[str, Any],
    include_low_confidence: bool,
) -> tuple[int, Counter[str]]:
    viewport = infer_viewport(observe)
    image_path = image_dir / f"{stem}.png"
    label_path = label_dir / f"{stem}.json"
    dom_path = dom_dir / f"{stem}.json"
    yolo_path = yolo_dir / f"{stem}.txt"

    save_base64_png(image_path, str(observe["screenshot"]))
    labels = build_labels(observe, include_low_confidence=include_low_confidence)
    dom_path.write_text(json.dumps(observe, ensure_ascii=False, indent=2), encoding="utf-8")
    label_doc = {
        "version": 1,
        "image": str(image_path.relative_to(root)),
        "sourceUrl": observe.get("url") or requested_url,
        "requestedUrl": requested_url,
        "title": observe.get("title") or "",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "viewport": viewport,
        "classes": CLASS_NAMES,
        "labelType": "dom_weak",
        "labels": labels,
        "stats": {
            "labelCount": len(labels),
            "categoryDistribution": dict(Counter(label["category"] for label in labels)),
            "needsReview": len([label for label in labels if label.get("needsReview")]),
        },
    }
    label_path.write_text(json.dumps(label_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    write_yolo(yolo_path, labels, viewport)
    return len(labels), Counter(label["category"] for label in labels)


def parse_categories(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def select_explore_targets(
    labels: list[dict[str, Any]],
    *,
    categories: set[str],
    limit: int,
    viewport: dict[str, int],
) -> list[dict[str, Any]]:
    weights = {
        "video_card": 1.0,
        "card": 0.92,
        "image": 0.75,
        "link": 0.65,
        "button": 0.55,
        "nav_item": 0.35,
    }
    candidates: list[tuple[float, dict[str, Any]]] = []
    for label in labels:
        category = str(label.get("category") or "")
        if category not in categories:
            continue
        bbox = label.get("bbox") or {}
        if int(bbox.get("w", 0)) <= 8 or int(bbox.get("h", 0)) <= 8:
            continue
        center = label.get("center") or {}
        cx, cy = int(center.get("x", 0)), int(center.get("y", 0))
        if cy < 72 and category in {"link", "button", "nav_item"}:
            continue
        area_bonus = min((int(bbox.get("w", 0)) * int(bbox.get("h", 0))) / max(viewport["width"] * viewport["height"], 1), 0.25)
        score = weights.get(category, 0.4) + float(label.get("confidence") or 0.0) * 0.15 + area_bonus
        candidates.append((score, label))
    candidates.sort(key=lambda item: item[0], reverse=True)

    selected: list[dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()
    for _, label in candidates:
        center = label.get("center") or {}
        bucket = (int(center.get("x", 0)) // 240, int(center.get("y", 0)) // 180)
        if bucket in occupied:
            continue
        occupied.add(bucket)
        selected.append(label)
        if len(selected) >= limit:
            break
    return selected


def load_existing_summary(root: Path) -> dict[str, Any] | None:
    path = root / "summary.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def merge_summary_pages(existing_summary: dict[str, Any] | None, new_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_pages = existing_summary.get("pages", []) if existing_summary else []
    if not isinstance(old_pages, list):
        old_pages = []
    return [*old_pages, *new_pages]


def next_page_index(image_dir: Path) -> int:
    max_index = 0
    for path in image_dir.glob("*.png"):
        match = re.match(r"^(\d{3,})_", path.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect weakly labeled web UI screenshots.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--out-dir", default="web_ui_dataset")
    parser.add_argument("--url", action="append", default=[], help="Either URL or name=URL. Can be repeated.")
    parser.add_argument("--url-file", default="", help="Text file with URL or name=URL per line.")
    parser.add_argument("--scroll-steps", type=int, default=1, help="Number of viewport captures per URL.")
    parser.add_argument("--scroll-delta", type=int, default=700)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--include-low-confidence", action="store_true")
    parser.add_argument("--append", action="store_true", help="Append to an existing dataset without overwriting files.")
    parser.add_argument("--explore-clicks", type=int, default=0, help="Click into this many candidate targets per URL and capture destination pages.")
    parser.add_argument("--explore-categories", default="video_card,card,image,link,button", help="Comma-separated label categories to click for exploration.")
    parser.add_argument("--explore-settle-seconds", type=float, default=2.5)
    args = parser.parse_args()

    pages: list[tuple[str, str]] = []
    if args.url_file:
        pages.extend(read_url_file(args.url_file))
    pages.extend(parse_page(raw) for raw in args.url)
    if not pages:
        pages = DEFAULT_URLS

    root = Path(args.out_dir).expanduser().resolve()
    image_dir = root / "images"
    label_dir = root / "labels"
    dom_dir = root / "dom"
    yolo_dir = root / "yolo"
    for directory in (image_dir, label_dir, dom_dir, yolo_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (root / "classes.json").write_text(json.dumps(CLASS_NAMES, ensure_ascii=False, indent=2), encoding="utf-8")
    write_dataset_yaml(root / "dataset.yaml", root)
    existing_summary = load_existing_summary(root) if args.append else None
    start_index = next_page_index(image_dir) if args.append else 1

    token = login(args.api_base, args.email, args.password)
    session_id = args.session_id or create_session(args.api_base, token, "web-ui-dataset-collector")

    summary_pages: list[dict[str, Any]] = []
    for run_index, (name, url) in enumerate(pages, start=1):
        page_index = start_index + run_index - 1
        print(f"[{run_index}/{len(pages)}] {name} -> {url}")
        try:
            navigate_url(args.api_base, token, session_id, url)
            wait_for_settle(args.api_base, token, session_id, args.settle_seconds)
        except Exception as exc:
            print(f"  page skipped before capture: {exc}")
            continue
        page_captures = 0
        page_labels = 0
        page_distribution: Counter[str] = Counter()
        first_observe: dict[str, Any] | None = None
        first_labels: list[dict[str, Any]] = []
        first_viewport = {"width": 1280, "height": 720}

        for step in range(max(1, args.scroll_steps)):
            try:
                observe = capture_dom(args.api_base, token, session_id)
            except Exception as exc:
                print(f"  capture skipped at step {step}: {exc}")
                break
            viewport = infer_viewport(observe)
            if first_observe is None:
                first_observe = observe
                first_viewport = viewport
                first_labels = build_labels(observe, include_low_confidence=args.include_low_confidence)
            stem = f"{page_index:03d}_{name}_{step:02d}"
            label_count, distribution = save_capture(
                root=root,
                image_dir=image_dir,
                label_dir=label_dir,
                dom_dir=dom_dir,
                yolo_dir=yolo_dir,
                stem=stem,
                requested_url=url,
                observe=observe,
                include_low_confidence=args.include_low_confidence,
            )
            page_captures += 1
            page_labels += label_count
            page_distribution.update(distribution)
            print(f"  step {step}: {label_count} labels -> {stem}.png")

            if step < args.scroll_steps - 1:
                try:
                    scroll_result = request_json(
                        args.api_base,
                        "/api/browser/scroll",
                        {"sessionId": session_id, "deltaY": args.scroll_delta, "x": 640, "y": 360},
                        token=token,
                        timeout=30,
                    )
                except Exception as exc:
                    print(f"  scroll skipped: {exc}")
                    break
                if scroll_result.get("ok") is False:
                    print(f"  scroll skipped: {scroll_result.get('error') or scroll_result}")
                    break
                time.sleep(max(0.5, args.settle_seconds / 2))

        if args.explore_clicks > 0 and first_observe is not None:
            targets = select_explore_targets(
                first_labels,
                categories=parse_categories(args.explore_categories),
                limit=args.explore_clicks,
                viewport=first_viewport,
            )
            for click_index, target in enumerate(targets, start=1):
                try:
                    navigate_url(args.api_base, token, session_id, url)
                    wait_for_settle(args.api_base, token, session_id, max(0.5, args.settle_seconds / 2))
                    center = target.get("center") or {}
                    click_result = request_json(
                        args.api_base,
                        "/api/browser/click",
                        {"sessionId": session_id, "x": int(center.get("x", 0)), "y": int(center.get("y", 0))},
                        token=token,
                        timeout=60,
                    )
                    if click_result.get("ok") is False:
                        print(f"  explore click {click_index} skipped: {click_result.get('error') or click_result}")
                        continue
                    time.sleep(args.explore_settle_seconds)
                    observe = capture_dom(args.api_base, token, session_id)
                    stem = f"{page_index:03d}_{name}_click{click_index:02d}"
                    label_count, distribution = save_capture(
                        root=root,
                        image_dir=image_dir,
                        label_dir=label_dir,
                        dom_dir=dom_dir,
                        yolo_dir=yolo_dir,
                        stem=stem,
                        requested_url=url,
                        observe=observe,
                        include_low_confidence=args.include_low_confidence,
                    )
                    page_captures += 1
                    page_labels += label_count
                    page_distribution.update(distribution)
                    print(
                        f"  click {click_index}: {target.get('category')} -> "
                        f"{label_count} labels -> {stem}.png"
                    )
                except Exception as exc:
                    print(f"  explore click {click_index} failed: {exc}")

        if page_captures == 0:
            continue
        summary_pages.append(
            {
                "name": name,
                "url": url,
                "captures": page_captures,
                "labelCount": page_labels,
                "categoryDistribution": dict(page_distribution),
            }
        )

    summary = {
        "version": 1,
        "sessionId": session_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "classNames": CLASS_NAMES,
        "pages": merge_summary_pages(existing_summary, summary_pages),
        "totals": {
            "captures": sum(page["captures"] for page in merge_summary_pages(existing_summary, summary_pages)),
            "labels": sum(page["labelCount"] for page in merge_summary_pages(existing_summary, summary_pages)),
            "categoryDistribution": dict(
                sum(
                    (Counter(page["categoryDistribution"]) for page in merge_summary_pages(existing_summary, summary_pages)),
                    Counter(),
                )
            ),
        },
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved dataset to {root}")
    print(f"captures={summary['totals']['captures']} labels={summary['totals']['labels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
