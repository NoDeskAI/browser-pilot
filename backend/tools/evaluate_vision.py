#!/usr/bin/env python3
"""Run Browser Pilot vision observe against a small URL suite.

The script intentionally uses only stdlib HTTP calls so it can run inside the
backend environment without adding another client dependency.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_PAGES = [
    ("youtube_cats", "https://www.youtube.com/results?search_query=cats"),
    ("csdn_home", "https://www.csdn.net/"),
    ("github_trending", "https://github.com/trending"),
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


def create_session(base_url: str, token: str) -> str:
    data = request_json(base_url, "/api/sessions", {"name": "vision-eval"}, token=token)
    session = data.get("session") if isinstance(data.get("session"), dict) else data
    session_id = session.get("id") if isinstance(session, dict) else None
    if not session_id:
        raise RuntimeError(f"create session did not return an id: {data}")
    request_json(base_url, f"/api/sessions/{session_id}/container/start", {}, token=token, timeout=180)
    return str(session_id)


def parse_page(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        parsed = urllib.parse.urlparse(raw)
        name = (parsed.netloc or "page").replace("www.", "").replace(".", "_")
        return name, raw
    name, url = raw.split("=", 1)
    return name.strip() or "page", url.strip()


def host_matches(current_url: str, target_url: str) -> bool:
    current = urllib.parse.urlparse(current_url).netloc.replace("www.", "")
    target = urllib.parse.urlparse(target_url).netloc.replace("www.", "")
    return bool(current and target and current == target)


def wait_for_navigation(base_url: str, token: str, session_id: str, url: str) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(12):
        time.sleep(1.0)
        last = request_json(
            base_url,
            "/api/browser/observe",
            {"sessionId": session_id, "mode": "dom", "maxCandidates": 5},
            token=token,
            timeout=30,
        )
        if host_matches(str(last.get("url", "")), url):
            return last
    return last


def ensure_ok(data: dict[str, Any], action: str) -> None:
    if data.get("ok") is False:
        raise RuntimeError(f"{action} failed: {data.get('error') or data}")


def save_base64_png(path: Path, data: str | None) -> bool:
    if not data:
        return False
    if data.startswith("data:image"):
        data = data.split(",", 1)[1]
    path.write_bytes(base64.b64decode(data))
    return True


def summarize_observe(name: str, url: str, data: dict[str, Any]) -> dict[str, Any]:
    candidates = list(data.get("visionCandidates") or [])
    groups = list(data.get("visionGroups") or [])
    families = Counter(str(item.get("family") or "unknown") for item in candidates)
    sources = Counter(str(item.get("semanticSource") or "unknown") for item in candidates)
    unknown = families.get("unknown", 0)
    total = len(candidates)
    return {
        "name": name,
        "url": url,
        "observedUrl": data.get("url"),
        "title": data.get("title"),
        "candidateCount": total,
        "groupCount": len(groups),
        "familyDistribution": dict(families),
        "unknownRatio": round(unknown / total, 4) if total else 0,
        "semanticSourceDistribution": dict(sources),
        "trace": data.get("trace") or {},
        "topCandidates": [
            {
                "id": item.get("id"),
                "family": item.get("family"),
                "label": item.get("label"),
                "score": item.get("score"),
                "semanticSource": item.get("semanticSource"),
                "textHint": item.get("textHint"),
                "bbox": item.get("bbox"),
            }
            for item in candidates[:10]
        ],
        "topGroups": [
            {
                "id": item.get("id"),
                "family": item.get("family"),
                "label": item.get("label"),
                "score": item.get("score"),
                "textHint": item.get("textHint"),
                "bbox": item.get("bbox"),
            }
            for item in groups[:10]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Browser Pilot vision observe.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--out-dir", default="vision_eval")
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--url", action="append", default=[], help="Either URL or name=URL. Can be repeated.")
    args = parser.parse_args()

    pages = [parse_page(raw) for raw in args.url] if args.url else DEFAULT_PAGES
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    token = login(args.api_base, args.email, args.password)
    session_id = args.session_id or create_session(args.api_base, token)

    summaries: list[dict[str, Any]] = []
    for idx, (name, url) in enumerate(pages, start=1):
        ensure_ok(
            request_json(args.api_base, "/api/browser/navigate", {"sessionId": session_id, "url": url}, token=token),
            f"navigate {url}",
        )
        wait_for_navigation(args.api_base, token, session_id, url)
        observed = request_json(
            args.api_base,
            "/api/browser/observe",
            {
                "sessionId": session_id,
                "mode": "vision",
                "maxCandidates": args.max_candidates,
                "threshold": args.threshold,
                "includeScreenshot": True,
                "includeAnnotatedScreenshot": True,
            },
            token=token,
            timeout=180,
        )

        stem = f"{idx:02d}_{name}"
        (out_dir / f"{stem}_observe.json").write_text(
            json.dumps(observed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        save_base64_png(out_dir / f"{stem}_annotated.png", observed.get("annotatedScreenshot"))
        summary = summarize_observe(name, url, observed)
        summaries.append(summary)
        print(
            f"{name}: {summary['candidateCount']} candidates, "
            f"{summary['groupCount']} groups, unknown={summary['unknownRatio']:.1%}"
        )

    (out_dir / "summary.json").write_text(
        json.dumps({"sessionId": session_id, "pages": summaries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved results to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
