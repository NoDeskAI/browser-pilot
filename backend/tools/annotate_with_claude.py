#!/usr/bin/env python3
"""Annotate web UI screenshots with a Claude multimodal model.

The script is resumable and never stores the API key. Provide it via
ND_API_KEY, or pass --api-key-env to use another environment variable.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
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

DEFAULT_API_BASE = "http://172.168.20.130:8080"
DEFAULT_MODEL = "us.anthropic.claude-opus-4-7"
DEFAULT_CHANNEL_URL = "https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-opus-4-7/invoke"


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate screenshots with Claude multimodal.")
    parser.add_argument("--dataset", required=True, help="Dataset root containing images/.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-key-env", default="ND_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--max-labels", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"Set {args.api_key_env} before running this script.")

    root = Path(args.dataset).expanduser().resolve()
    image_dir = root / "images"
    out_dir = root / "vlm_labels"
    yolo_dir = root / "vlm_yolo"
    raw_dir = root / "vlm_raw"
    for directory in (out_dir, yolo_dir, raw_dir):
        directory.mkdir(parents=True, exist_ok=True)

    images = sorted(image_dir.glob("*.png"))
    if args.start:
        images = images[args.start :]
    if args.limit:
        images = images[: args.limit]

    processed = 0
    failures: list[dict[str, Any]] = []
    for index, image_path in enumerate(images, start=args.start):
        label_path = out_dir / f"{image_path.stem}.json"
        raw_path = raw_dir / f"{image_path.stem}.json"
        yolo_path = yolo_dir / f"{image_path.stem}.txt"
        if label_path.exists() and not args.overwrite:
            continue

        try:
            payload = build_request(image_path, model=args.model, channel_url=args.channel_url, max_labels=args.max_labels, max_tokens=args.max_tokens)
            response = post_json(args.api_base, "/default/passthrough", payload, api_key=api_key, timeout=240)
            raw_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
            content = extract_text(response)
            parsed = parse_json_response(content)
            label_doc = normalize_label_doc(parsed, image_path=image_path, root=root)
            label_path.write_text(json.dumps(label_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            write_yolo(yolo_path, label_doc["labels"], label_doc["imageSize"])
            processed += 1
            print(f"[{index + 1}] {image_path.name}: {len(label_doc['labels'])} labels")
        except Exception as exc:
            failures.append({"image": str(image_path), "error": str(exc)})
            print(f"[{index + 1}] {image_path.name}: failed: {exc}")
        time.sleep(args.sleep)

    write_vlm_summary(root, failures=failures)
    print(f"processed={processed} failures={len(failures)}")
    return 0 if not failures else 1


def build_request(image_path: Path, *, model: str, channel_url: str, max_labels: int, max_tokens: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(image_path) as img:
        width, height = img.size
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = f"""
You are building a web UI object detection dataset.

Task: label visible UI elements in this browser viewport screenshot.

Image size: width={width}, height={height}. Coordinates must be pixel coordinates in this original image.

Allowed categories:
{", ".join(CLASS_NAMES)}

Label rules:
- Return only visible web page UI elements, not imagined elements.
- Prefer clickable or semantically important UI regions.
- For cards, label the outer visible card/container when it is a coherent item.
- For video results, use video_card when the thumbnail/title/actions form one item.
- For icons, use icon only for standalone icon controls or meaningful small symbols.
- Use text_block only for visually important non-clickable text blocks.
- Avoid duplicate boxes for the same exact element.
- Do not label the browser chrome unless it appears inside the screenshot as page content.
- Keep at most {max_labels} labels.

Return strict JSON only, no markdown:
{{
  "imageSize": {{"width": {width}, "height": {height}}},
  "labels": [
    {{
      "category": "button",
      "bbox": {{"x": 0, "y": 0, "w": 100, "h": 40}},
      "confidence": 0.0,
      "description": "short visible reason"
    }}
  ]
}}
""".strip()
    return {
        "channel": "aws",
        "channel_url": channel_url,
        "model": model,
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def post_json(base_url: str, path: str, payload: dict[str, Any], *, api_key: str, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {text[:800]}") from exc


def extract_text(response: dict[str, Any]) -> str:
    content = response.get("content")
    if isinstance(content, list):
        parts = [str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text"]
        return "\n".join(parts).strip()
    if isinstance(content, str):
        return content
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        return str(message.get("content", ""))
    return json.dumps(response, ensure_ascii=False)


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            cleaned = match.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise RuntimeError("model response is not a JSON object")
    return data


def normalize_label_doc(data: dict[str, Any], *, image_path: Path, root: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(image_path) as img:
        width, height = img.size
    image_size = {"width": width, "height": height}
    labels = []
    for raw in data.get("labels") or []:
        if not isinstance(raw, dict):
            continue
        category = normalize_category(str(raw.get("category") or ""))
        if category not in CLASS_INDEX:
            continue
        bbox = clamp_bbox(raw.get("bbox") or {}, image_size)
        if not bbox:
            continue
        confidence = raw.get("confidence")
        try:
            confidence_value = max(0.0, min(float(confidence), 1.0))
        except Exception:
            confidence_value = 0.5
        labels.append(
            {
                "id": f"vlm-{len(labels) + 1:04d}",
                "category": category,
                "classId": CLASS_INDEX[category],
                "bbox": bbox,
                "center": {"x": bbox["x"] + bbox["w"] // 2, "y": bbox["y"] + bbox["h"] // 2},
                "confidence": round(confidence_value, 3),
                "source": "claude_vlm",
                "description": str(raw.get("description") or "")[:200],
            }
        )
    return {
        "version": 1,
        "image": str(image_path.relative_to(root)),
        "imageSize": image_size,
        "classes": CLASS_NAMES,
        "labelType": "claude_vlm",
        "labels": dedupe_labels(labels),
    }


def normalize_category(category: str) -> str:
    key = category.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "checkbox": "checkbox_toggle",
        "toggle": "checkbox_toggle",
        "switch": "checkbox_toggle",
        "video": "video_card",
        "video_item": "video_card",
        "text": "text_block",
        "textblock": "text_block",
        "menu": "menu_item",
        "navigation": "nav_item",
        "nav": "nav_item",
    }
    return aliases.get(key, key)


def clamp_bbox(raw: dict[str, Any], image_size: dict[str, int]) -> dict[str, int] | None:
    try:
        x = int(round(float(raw.get("x", 0))))
        y = int(round(float(raw.get("y", 0))))
        w = int(round(float(raw.get("w", 0))))
        h = int(round(float(raw.get("h", 0))))
    except Exception:
        return None
    x1 = max(0, min(x, image_size["width"]))
    y1 = max(0, min(y, image_size["height"]))
    x2 = max(0, min(x + w, image_size["width"]))
    y2 = max(0, min(y + h, image_size["height"]))
    if x2 <= x1 or y2 <= y1:
        return None
    if (x2 - x1) < 3 or (y2 - y1) < 3:
        return None
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def dedupe_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels.sort(key=lambda item: item["confidence"], reverse=True)
    kept = []
    for label in labels:
        if any(label["category"] == other["category"] and iou(label["bbox"], other["bbox"]) >= 0.82 for other in kept):
            continue
        kept.append(label)
    for index, label in enumerate(kept, start=1):
        label["id"] = f"vlm-{index:04d}"
    return kept


def iou(a: dict[str, int], b: dict[str, int]) -> float:
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = max(a["w"] * a["h"] + b["w"] * b["h"] - inter, 1)
    return inter / union


def write_yolo(path: Path, labels: list[dict[str, Any]], image_size: dict[str, int]) -> None:
    lines = []
    for label in labels:
        bbox = label["bbox"]
        cx = (bbox["x"] + bbox["w"] / 2) / image_size["width"]
        cy = (bbox["y"] + bbox["h"] / 2) / image_size["height"]
        w = bbox["w"] / image_size["width"]
        h = bbox["h"] / image_size["height"]
        lines.append(f"{label['classId']} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_vlm_summary(root: Path, *, failures: list[dict[str, Any]]) -> None:
    out_dir = root / "vlm_labels"
    labels = sorted(out_dir.glob("*.json"))
    counts = {name: 0 for name in CLASS_NAMES}
    total = 0
    for path in labels:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for label in data.get("labels") or []:
            category = label.get("category")
            if category in counts:
                counts[category] += 1
                total += 1
    (root / "vlm_summary.json").write_text(
        json.dumps(
            {
                "annotatedImages": len(labels),
                "labels": total,
                "categoryDistribution": {key: value for key, value in counts.items() if value},
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
