from __future__ import annotations

import asyncio
import base64
from collections import Counter, defaultdict
import ipaddress
import json
import logging
import shlex
import socket
import time
from typing import Any

import httpx

from app.config import DOCKER_HOST_ADDR, CONTAINER_PREFIX as _PREFIX
from app.db import get_pool
from app.device_presets import DEVICE_PRESETS, DEFAULT_PRESET, get_preset
from app.fingerprint import (
    attach_network_profile,
    declared_network_profile,
    failed_network_profile,
    generate_profile,
    normalize_network_probe,
)

logger = logging.getLogger("container")

CONTAINER_PREFIX = f"{_PREFIX}-"
SHM_SIZE = "2g"

_STATIC_ENV = {
    "SE_VNC_NO_PASSWORD": "1",
    "SE_SCREEN_DEPTH": "24",
    "SE_NODE_SESSION_TIMEOUT": "86400",
    "SE_NODE_OVERRIDE_MAX_SESSIONS": "true",
    "SE_NODE_MAX_SESSIONS": "1",
}

GRID_READY_TIMEOUT = 15
GRID_POLL_INTERVAL = 0.5
BROWSER_NETWORK_PROBE_TIMEOUT = 10
FAST_NETWORK_PROBE_TIMEOUT = 3.0
DEEP_NETWORK_PROBE_TIMEOUT = 30.0
NETWORK_PROFILE_CACHE_TTL = 600.0

_BROWSER_NETWORK_APIS = [
    ("ipwho.is", "https://ipwho.is/"),
    ("api.ip.sb", "https://api.ip.sb/geoip"),
    ("freeipapi.com", "https://freeipapi.com/api/json/"),
    ("ip.guide", "https://ip.guide/"),
    (
        "ip-api.com",
        "http://ip-api.com/json?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query",
    ),
    ("ipinfo.io", "https://ipinfo.io/json"),
    ("ip234.in", "https://ip234.in/ip.json"),
]

_BROWSER_FAST_OBSERVED_IP_APIS = [
    ("api.ipify.org", "https://api.ipify.org?format=json"),
]

_BROWSER_FAST_IP_GEO_APIS = [
    ("ipwho.is", "https://ipwho.is/{ip}"),
]

_BROWSER_FAST_NETWORK_APIS = [
    ("api.ip.sb", "https://api.ip.sb/geoip"),
]

_BROWSER_OBSERVED_IP_APIS = [
    ("api.ipify.org", "https://api.ipify.org?format=json"),
    ("api64.ipify.org", "https://api64.ipify.org?format=json"),
    ("cloudflare.com", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("country.is", "https://api.country.is/"),
]

_BROWSER_IP_GEO_APIS = [
    ("ipwho.is", "https://ipwho.is/{ip}"),
    ("freeipapi.com", "https://freeipapi.com/api/json/{ip}"),
    ("ip.guide", "https://ip.guide/{ip}"),
]

_CONTAINER_NEUTRAL_NETWORK_APIS = [
    ("ipwho.is", "https://ipwho.is/"),
    ("api.ip.sb", "https://api.ip.sb/geoip"),
    ("freeipapi.com", "https://freeipapi.com/api/json/"),
    ("ip.guide", "https://ip.guide/"),
]

_NETWORK_PROFILE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_BACKGROUND_NETWORK_TASKS: set[str] = set()


def container_name(session_id: str) -> str:
    return f"{CONTAINER_PREFIX}{session_id[:12]}"


def _dns_servers_from_profile(fingerprint_profile: dict | None) -> list[str]:
    servers = []
    if isinstance(fingerprint_profile, dict):
        raw_servers = fingerprint_profile.get("network", {}).get("dnsServers", [])
        if isinstance(raw_servers, list):
            servers = raw_servers

    valid: list[str] = []
    for server in servers:
        try:
            ipaddress.ip_address(str(server))
        except ValueError:
            continue
        valid.append(str(server))
    return valid[:3]


def _runtime_warnings(profile: dict) -> list[str]:
    warnings = profile.get("runtimeWarnings")
    if not isinstance(warnings, list):
        warnings = []
    return [str(w) for w in warnings if str(w or "").strip()]


def _add_runtime_warning(profile: dict, warning: str) -> None:
    warnings = _runtime_warnings(profile)
    if warning not in warnings:
        warnings.append(warning)
    profile["runtimeWarnings"] = warnings


def _network_signature(network: dict | None) -> tuple:
    if not isinstance(network, dict):
        return ("", "", "", "", "", (), "", "", None, None)
    return (
        str(network.get("ip") or ""),
        str(network.get("countryCode") or ""),
        str(network.get("region") or ""),
        str(network.get("city") or ""),
        str(network.get("timezone") or ""),
        tuple(str(s) for s in (network.get("dnsServers") or [])),
        str(network.get("asn") or ""),
        str(network.get("isp") or ""),
        network.get("lat"),
        network.get("lon"),
    )


def _needs_browser_network_reconcile(fingerprint_profile: dict | None) -> bool:
    if not isinstance(fingerprint_profile, dict):
        return False
    network = fingerprint_profile.get("network")
    if not isinstance(network, dict):
        return True
    return network.get("observedVia") not in ("browser", "browser-hidden-cdp", "container-fallback-neutral")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def _json_clone(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, separators=(",", ":")))


def _network_cache_key(
    *,
    image_name: str | None,
    proxy: str | None,
    fingerprint_profile: dict | None,
) -> str:
    payload = {
        "image": image_name or "",
        "proxy": proxy or "",
        "dns": _dns_servers_from_profile(fingerprint_profile),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _cached_network_profile(cache_key: str) -> dict[str, Any] | None:
    cached = _NETWORK_PROFILE_CACHE.get(cache_key)
    if not cached:
        return None
    created_at, network = cached
    if time.monotonic() - created_at > NETWORK_PROFILE_CACHE_TTL:
        _NETWORK_PROFILE_CACHE.pop(cache_key, None)
        return None
    result = _json_clone(network)
    result["cacheHit"] = True
    result["probeStatus"] = "cached"
    return result


def _store_network_profile_cache(cache_key: str, network: dict[str, Any]) -> None:
    cached = _json_clone(network)
    cached.pop("cacheHit", None)
    _NETWORK_PROFILE_CACHE[cache_key] = (time.monotonic(), cached)


def _extract_observed_ip(api_name: str, data: dict[str, Any]) -> str | None:
    value: Any = data.get("ip") or data.get("query")
    ip = str(value or "").strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


def _ip_sort_key(ip: str) -> tuple[int, str]:
    try:
        parsed = ipaddress.ip_address(ip)
        return (0 if parsed.version == 4 else 1, ip)
    except ValueError:
        return (2, ip)


def _compact_network_observation(network: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": network.get("source", ""),
        "ip": network.get("ip", ""),
        "countryCode": network.get("countryCode", ""),
        "timezone": network.get("timezone", ""),
        "asn": network.get("asn", ""),
        "isp": network.get("isp", ""),
    }


def _select_network_consensus(networks: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any] | None:
    if not networks:
        return None

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for network in networks:
        key = (str(network.get("countryCode") or ""), str(network.get("timezone") or ""))
        grouped[key].append(network)

    winner_key, winner_group = max(
        grouped.items(),
        key=lambda item: (len(item[1]), bool(item[0][0]), bool(item[0][1])),
    )
    winner_group = sorted(
        winner_group,
        key=lambda item: _ip_sort_key(str(item.get("ip") or "")),
    )
    selected = dict(winner_group[0])
    observations = [_compact_network_observation(network) for network in networks]
    selected["observations"] = observations
    selected["observationCount"] = len(networks)
    selected["consensusKey"] = {
        "countryCode": winner_key[0],
        "timezone": winner_key[1],
        "sources": [str(item.get("source") or "") for item in winner_group],
    }

    distinct_geo = Counter((str(n.get("countryCode") or ""), str(n.get("timezone") or "")) for n in networks)
    distinct_ips = Counter(str(n.get("ip") or "") for n in networks if n.get("ip"))
    if len(winner_group) >= 2 and len(distinct_geo) == 1:
        selected["confidence"] = "high"
    elif len(winner_group) >= 2:
        selected["confidence"] = "medium"
    else:
        selected["confidence"] = "low"

    if len(distinct_geo) > 1:
        warnings.append(
            "network_source_disagreement: neutral probes returned multiple country/timezone results: "
            + ", ".join(f"{cc or '?'}@{tz or '?'}={count}" for (cc, tz), count in distinct_geo.items())
        )
    if len(distinct_ips) > 1:
        warnings.append(
            "network_ip_variance: neutral probes returned multiple outbound IPs: "
            + ", ".join(f"{ip}={count}" for ip, count in distinct_ips.items())
        )
    if selected["confidence"] == "low":
        warnings.append("network_consensus_low_confidence: fewer than two neutral probes agreed on network geography")

    selected["warnings"] = warnings
    return selected


async def _run(cmd: str, timeout: float = 30) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", f"timeout after {timeout}s", -1
    return (
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
        proc.returncode or 0,
    )


async def exec_in_container(session_id: str, cmd: str, timeout: float = 10) -> str:
    name = container_name(session_id)
    stdout, stderr, rc = await _run(f"docker exec {name} {cmd}", timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"docker exec failed (rc={rc}): {stderr[:300]}")
    return stdout


async def sync_fingerprint_profile_to_container(
    session_id: str,
    fingerprint_profile: dict,
    *,
    restart_agent: bool = True,
) -> None:
    """Update runtime profile files inside a running browser container."""
    name = container_name(session_id)
    encoded = base64.b64encode(
        json.dumps(fingerprint_profile, separators=(",", ":")).encode()
    ).decode()
    script = (
        f"printf %s {shlex.quote(encoded)} | base64 -d > /tmp/fingerprint-profile.json "
        "&& printf 'var __FP__=' > /opt/stealth-ext/fp-profile.js "
        "&& cat /tmp/fingerprint-profile.json >> /opt/stealth-ext/fp-profile.js "
        "&& printf ';\\n' >> /opt/stealth-ext/fp-profile.js"
    )
    stdout, stderr, rc = await _run(
        f"docker exec {name} sh -lc {shlex.quote(script)}",
        timeout=8,
    )
    if rc != 0:
        raise RuntimeError(f"profile sync failed: {(stderr or stdout)[:200]}")
    if restart_agent:
        _, stderr, rc = await _run(
            f"docker exec {name} supervisorctl restart cdp-fingerprint-agent",
            timeout=10,
        )
        if rc != 0:
            logger.warning("Failed to restart cdp-fingerprint-agent in %s: %s", name, stderr[:200])


def _json_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)
    parsed: dict[str, Any] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key.strip()] = value.strip()
    if parsed:
        return parsed
    raise ValueError("probe response was not JSON or key=value text")


def _browser_probe_tasks(mode: str) -> dict[str, Any]:
    if mode == "fast":
        return {
            "observed": [
                {"source": source, "url": url, "geo": [
                    {"source": geo_source, "url": geo_url}
                    for geo_source, geo_url in _BROWSER_FAST_IP_GEO_APIS
                ]}
                for source, url in _BROWSER_FAST_OBSERVED_IP_APIS
            ],
            "direct": [{"source": source, "url": url} for source, url in _BROWSER_FAST_NETWORK_APIS],
        }
    return {
        "observed": [
            {"source": source, "url": url, "geo": [
                {"source": geo_source, "url": geo_url}
                for geo_source, geo_url in _BROWSER_IP_GEO_APIS
            ]}
            for source, url in _BROWSER_OBSERVED_IP_APIS
        ],
        "direct": [{"source": source, "url": url} for source, url in _BROWSER_NETWORK_APIS],
    }


def _hidden_cdp_probe_script(tasks: dict[str, Any], per_url_timeout: float, total_timeout: float) -> str:
    script = r'''
import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request

TASKS = __TASKS__
PER_URL_TIMEOUT = __PER_URL_TIMEOUT__
TOTAL_TIMEOUT = __TOTAL_TIMEOUT__


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock, text, opcode=1):
    payload = text.encode("utf-8") if isinstance(text, str) else text
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    sock.sendall(bytes(header) + masked)


def recv_message(sock, deadline):
    chunks = []
    while True:
        remaining = max(0.1, deadline - time.time())
        sock.settimeout(remaining)
        b1, b2 = recv_exact(sock, 2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", recv_exact(sock, 8))[0]
        mask = recv_exact(sock, 4) if masked else b""
        payload = recv_exact(sock, length) if length else b""
        if masked:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(length))
        if opcode == 8:
            raise RuntimeError("websocket closed")
        if opcode == 9:
            send_frame(sock, payload, opcode=10)
            continue
        if opcode in (1, 0):
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")


def open_ws(ws_url):
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    sock = socket.create_connection((host, port), timeout=2)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET " + path + " HTTP/1.1\r\n"
        "Host: " + host + ":" + str(port) + "\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: " + key + "\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("websocket handshake failed")
        response += chunk
    if b" 101 " not in response.split(b"\r\n", 1)[0]:
        raise RuntimeError("websocket handshake rejected")
    return sock


class CDP:
    def __init__(self, sock):
        self.sock = sock
        self.next_id = 0

    def call(self, method, params=None, session_id=None, timeout=2):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        send_frame(self.sock, json.dumps(msg, separators=(",", ":")))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = recv_message(self.sock, deadline)
            data = json.loads(raw)
            if data.get("id") != self.next_id:
                continue
            if "error" in data:
                raise RuntimeError(method + " failed: " + json.dumps(data["error"], separators=(",", ":")))
            return data.get("result", {})
        raise RuntimeError(method + " timed out")


def parse_probe_text(text):
    raw = str(text or "").strip()
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)
    data = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip()
    return data


def extract_ip(text):
    data = parse_probe_text(text)
    value = str(data.get("ip") or data.get("query") or "").strip()
    if value:
        return value
    raise RuntimeError("missing observed IP")


def has_budget(min_seconds=0.35):
    return time.time() + min_seconds < STARTED_AT + TOTAL_TIMEOUT


def read_page(cdp, session_id, url):
    if not has_budget():
        raise RuntimeError("probe time budget exhausted")
    fetch_deadline = min(time.time() + PER_URL_TIMEOUT, STARTED_AT + TOTAL_TIMEOUT - 0.1)
    try:
        result = cdp.call(
            "Runtime.evaluate",
            {
                "expression": "fetch(" + json.dumps(url) + ", {cache: 'no-store'}).then(r => r.text())",
                "awaitPromise": True,
                "returnByValue": True,
            },
            session_id=session_id,
            timeout=max(0.2, fetch_deadline - time.time()),
        )
        value = ((result.get("result") or {}).get("value") or "").strip()
        if value:
            return value
    except Exception:
        pass
    try:
        cdp.call("Page.navigate", {"url": url}, session_id=session_id, timeout=0.8)
    except Exception:
        pass
    deadline = min(time.time() + PER_URL_TIMEOUT, STARTED_AT + TOTAL_TIMEOUT - 0.1)
    last_error = ""
    expression = """(() => {
      const body = document.body ? (document.body.innerText || document.body.textContent || '') : '';
      const doc = document.documentElement ? (document.documentElement.innerText || document.documentElement.textContent || '') : '';
      return String(body || doc || '').trim();
    })()"""
    while time.time() < deadline:
        try:
            timeout = min(0.8, max(0.2, deadline - time.time()))
            result = cdp.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session_id=session_id,
                timeout=timeout,
            )
            value = ((result.get("result") or {}).get("value") or "").strip()
            if value:
                return value
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.15)
    raise RuntimeError(last_error or "empty probe response")


def main():
    global STARTED_AT
    STARTED_AT = time.time()
    version = get_json("http://127.0.0.1:9222/json/version")
    sock = open_ws(version["webSocketDebuggerUrl"])
    cdp = CDP(sock)
    context_id = None
    target_id = None
    session_id = None
    out = []
    try:
        context_id = cdp.call("Target.createBrowserContext", {"disposeOnDetach": True}, timeout=2)["browserContextId"]
        target_id = cdp.call(
            "Target.createTarget",
            {"url": "about:blank", "browserContextId": context_id},
            timeout=2,
        )["targetId"]
        session_id = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True}, timeout=2)["sessionId"]
        try:
            cdp.call("Runtime.runIfWaitingForDebugger", session_id=session_id, timeout=1)
        except Exception:
            pass
        cdp.call("Page.enable", session_id=session_id, timeout=2)
        cdp.call("Runtime.enable", session_id=session_id, timeout=2)
        try:
            cdp.call("Runtime.runIfWaitingForDebugger", session_id=session_id, timeout=1)
        except Exception:
            pass

        for item in TASKS.get("observed", []):
            if not has_budget():
                out.append({"kind": "observed_ip", "source": item.get("source", ""), "url": item.get("url", ""), "ok": False, "error": "probe time budget exhausted"})
                break
            ip_source = item["source"]
            try:
                ip_text = read_page(cdp, session_id, item["url"])
                observed_ip = extract_ip(ip_text)
            except Exception as exc:
                out.append({"kind": "observed_ip", "source": ip_source, "url": item.get("url", ""), "ok": False, "error": str(exc)})
                continue
            for geo in item.get("geo", []):
                if not has_budget():
                    out.append({"kind": "observed_geo", "ipSource": ip_source, "geoSource": geo.get("source", ""), "ok": False, "error": "probe time budget exhausted"})
                    break
                geo_source = geo["source"]
                url = geo["url"].replace("{ip}", observed_ip)
                try:
                    text = read_page(cdp, session_id, url)
                    out.append({
                        "kind": "observed_geo",
                        "ipSource": ip_source,
                        "geoSource": geo_source,
                        "url": url,
                        "ok": True,
                        "text": text[:20000],
                    })
                except Exception as exc:
                    out.append({
                        "kind": "observed_geo",
                        "ipSource": ip_source,
                        "geoSource": geo_source,
                        "url": url,
                        "ok": False,
                        "error": str(exc),
                    })

        for item in TASKS.get("direct", []):
            if not has_budget():
                out.append({"kind": "direct", "source": item.get("source", ""), "url": item.get("url", ""), "ok": False, "error": "probe time budget exhausted"})
                break
            source = item["source"]
            try:
                text = read_page(cdp, session_id, item["url"])
                out.append({"kind": "direct", "source": source, "url": item["url"], "ok": True, "text": text[:20000]})
            except Exception as exc:
                out.append({"kind": "direct", "source": source, "url": item.get("url", ""), "ok": False, "error": str(exc)})
    finally:
        if target_id:
            try:
                cdp.call("Target.closeTarget", {"targetId": target_id}, timeout=1)
            except Exception:
                pass
        if context_id:
            try:
                cdp.call("Target.disposeBrowserContext", {"browserContextId": context_id}, timeout=1)
            except Exception:
                pass
        try:
            sock.close()
        except Exception:
            pass
    print(json.dumps(out, separators=(",", ":")))


main()
'''
    return (
        script
        .replace("__TASKS__", json.dumps(tasks, separators=(",", ":")))
        .replace("__PER_URL_TIMEOUT__", repr(per_url_timeout))
        .replace("__TOTAL_TIMEOUT__", repr(total_timeout))
    )


async def _run_hidden_browser_probe(
    session_id: str,
    *,
    mode: str,
    timeout: float,
) -> list[dict[str, Any]]:
    name = container_name(session_id)
    per_url_timeout = 1.5 if mode == "fast" else 3.0
    script = _hidden_cdp_probe_script(_browser_probe_tasks(mode), per_url_timeout, timeout)
    stdout, stderr, rc = await _run(
        f"docker exec {name} /usr/bin/python3 -c {shlex.quote(script)}",
        timeout=timeout + 1,
    )
    if rc != 0:
        raise RuntimeError(f"hidden CDP probe failed: {(stderr or stdout)[:300]}")
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"hidden CDP probe returned invalid JSON: {stdout[:300]}") from exc
    if not isinstance(result, list):
        raise RuntimeError("hidden CDP probe returned non-list payload")
    return result


async def _resolve_hidden_browser_network(
    session_id: str,
    *,
    mode: str,
    timeout: float,
    warnings: list[str],
) -> dict[str, Any] | None:
    started_at = time.monotonic()
    networks: list[dict[str, Any]] = []
    results = await _run_hidden_browser_probe(session_id, mode=mode, timeout=timeout)
    for item in results:
        kind = str(item.get("kind") or "")
        if not item.get("ok"):
            source = item.get("source") or item.get("ipSource") or item.get("geoSource") or "unknown"
            warnings.append(f"{source} hidden browser probe failed: {item.get('error', '')}")
            continue
        try:
            data = _json_from_text(str(item.get("text") or ""))
            if kind == "observed_geo":
                geo_source = str(item.get("geoSource") or "")
                ip_source = str(item.get("ipSource") or "")
                network = normalize_network_probe(geo_source, data)
                if network:
                    network["source"] = f"browser:{ip_source}+{geo_source}"
                    network["observedIpSource"] = ip_source
                else:
                    warnings.append(f"{ip_source}+{geo_source} response missing usable geo/timezone")
                    continue
            else:
                source = str(item.get("source") or "")
                network = normalize_network_probe(source, data)
                if network:
                    network["source"] = f"browser:{source}"
                else:
                    warnings.append(f"{source} response missing usable IP/timezone")
                    continue
            network["observedVia"] = "browser-hidden-cdp"
            networks.append(network)
        except Exception as exc:
            source = item.get("source") or item.get("ipSource") or item.get("geoSource") or "unknown"
            warnings.append(f"{source} hidden browser response parse failed: {exc}")
    selected = _select_network_consensus(networks, warnings)
    if selected:
        selected["observedVia"] = "browser-hidden-cdp"
        selected["probeMode"] = mode
        selected["probeStatus"] = "ready"
        selected["probeDurationMs"] = int((time.monotonic() - started_at) * 1000)
    return selected


async def _resolve_neutral_network_via_container(
    session_id: str,
    *,
    warnings: list[str],
) -> dict[str, Any] | None:
    name = container_name(session_id)
    script = """
import json
import urllib.request

apis = %s
out = []
for name, url in apis:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("utf-8", "replace").strip()
        out.append({"source": name, "ok": True, "text": text})
    except Exception as exc:
        out.append({"source": name, "ok": False, "error": str(exc)})
print(json.dumps(out, separators=(",", ":")))
""" % json.dumps(_CONTAINER_NEUTRAL_NETWORK_APIS, separators=(",", ":"))
    stdout, stderr, rc = await _run(
        f"docker exec {name} python3 -c {shlex.quote(script)}",
        timeout=35,
    )
    if rc != 0:
        warnings.append(f"neutral container fallback failed: {(stderr or stdout)[:200]}")
        return None
    networks: list[dict[str, Any]] = []
    try:
        for item in json.loads(stdout):
            source = str(item.get("source") or "")
            if not item.get("ok"):
                warnings.append(f"{source} container fallback failed: {item.get('error', '')}")
                continue
            try:
                data = _json_from_text(str(item.get("text") or ""))
                network = normalize_network_probe(source, data)
                if network:
                    network["source"] = f"container:{source}"
                    network["observedVia"] = "container-fallback-neutral"
                    networks.append(network)
                else:
                    warnings.append(f"{source} container fallback missing usable geo/timezone")
            except Exception as exc:
                warnings.append(f"{source} container fallback parse failed: {exc}")
    except Exception as exc:
        warnings.append(f"neutral container fallback response parse failed: {exc}")
        return None
    selected = _select_network_consensus(networks, warnings)
    if selected:
        selected["observedVia"] = "container-fallback-neutral"
        logger.info(
            "Resolved network via neutral container fallback: ip=%s tz=%s dns=%s confidence=%s",
            selected.get("ip"),
            selected.get("timezone"),
            ",".join(selected.get("dnsServers", [])),
            selected.get("confidence"),
        )
    return selected


async def resolve_network_via_browser(
    ports: dict[str, int],
    session_id: str | None = None,
    *,
    mode: str = "fast",
) -> dict[str, Any]:
    """Resolve network metadata through a hidden browser-level CDP target.

    The probe creates a temporary off-the-record BrowserContext inside Chrome,
    so probe URLs do not touch the user's visible tab or persisted history.
    """
    if not session_id:
        return failed_network_profile("hidden browser probe requires a session id", [])

    warnings: list[str] = []
    timeout = FAST_NETWORK_PROBE_TIMEOUT if mode == "fast" else DEEP_NETWORK_PROBE_TIMEOUT
    try:
        selected = await _resolve_hidden_browser_network(
            session_id,
            mode=mode,
            timeout=timeout,
            warnings=warnings,
        )
        if selected:
            logger.info(
                "Resolved network via hidden browser CDP (%s): ip=%s tz=%s dns=%s confidence=%s",
                mode,
                selected.get("ip"),
                selected.get("timezone"),
                ",".join(selected.get("dnsServers", [])),
                selected.get("confidence"),
            )
            return selected
    except Exception as exc:
        warnings.append(f"hidden browser probe failed: {exc}")

    if mode != "fast":
        fallback = await _resolve_neutral_network_via_container(session_id, warnings=warnings)
        if fallback:
            fallback["probeMode"] = mode
            fallback["probeStatus"] = "ready"
            return fallback

    logger.warning("Hidden browser network probe failed: %s", "; ".join(warnings))
    return failed_network_profile("hidden browser network probe failed", warnings)


def _add_network_runtime_warnings(fingerprint_profile: dict, network: dict[str, Any]) -> None:
    observed_via = str(network.get("observedVia") or "")
    if observed_via == "container-fallback-neutral":
        _add_runtime_warning(
            fingerprint_profile,
            "browser_network_probe_fallback: browser probe failed; neutral network consensus was resolved through the running container path and container DNS was reconciled.",
        )
    network_warnings = [str(w) for w in network.get("warnings", []) if str(w or "").strip()]
    if any(w.startswith("network_source_disagreement:") for w in network_warnings):
        _add_runtime_warning(
            fingerprint_profile,
            "network_source_disagreement: neutral network probes disagree; this session should be treated as untrusted until the proxy/network path is stabilized.",
        )
    if any(w.startswith("network_consensus_low_confidence:") for w in network_warnings):
        _add_runtime_warning(
            fingerprint_profile,
            "network_consensus_low_confidence: network profile is based on fewer than two agreeing neutral probes.",
        )


def _schedule_background_network_consensus(
    session_id: str,
    ports: dict[str, int],
    *,
    cache_key: str,
) -> None:
    if session_id in _BACKGROUND_NETWORK_TASKS:
        return
    _BACKGROUND_NETWORK_TASKS.add(session_id)
    task = asyncio.create_task(
        _background_network_consensus(session_id, dict(ports), cache_key=cache_key)
    )

    def _done(done_task: asyncio.Task) -> None:
        _BACKGROUND_NETWORK_TASKS.discard(session_id)
        try:
            done_task.result()
        except Exception as exc:
            logger.warning("Background network consensus failed for %s: %s", session_id, exc)

    task.add_done_callback(_done)


async def _background_network_consensus(
    session_id: str,
    ports: dict[str, int],
    *,
    cache_key: str,
) -> None:
    observed = await resolve_network_via_browser(ports, session_id=session_id, mode="deep")
    pool = get_pool()
    row = await pool.fetchrow("SELECT fingerprint_profile FROM sessions WHERE id = $1", session_id)
    if not row or not isinstance(row["fingerprint_profile"], dict):
        return

    fingerprint_profile = row["fingerprint_profile"]
    if observed.get("source") == "unresolved":
        _add_runtime_warning(
            fingerprint_profile,
            "network_probe_pending: background network consensus did not complete; provisional network profile is still in use.",
        )
        await pool.execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
            fingerprint_profile,
            session_id,
        )
        return

    old_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    old_signature = _network_signature(old_network)
    old_dns = _dns_servers_from_profile(fingerprint_profile)

    attach_network_profile(fingerprint_profile, observed)
    new_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    new_network["observedVia"] = str(observed.get("observedVia") or "browser-hidden-cdp")
    new_network.setdefault("provisionalSource", old_network.get("source") or "")
    new_network["probeStatus"] = "background-ready"
    fingerprint_profile["network"] = new_network
    _add_network_runtime_warnings(fingerprint_profile, new_network)

    new_signature = _network_signature(new_network)
    new_dns = _dns_servers_from_profile(fingerprint_profile)
    if old_signature != new_signature:
        _add_runtime_warning(
            fingerprint_profile,
            "network_probe_background_reconciled: background neutral network consensus updated the runtime fingerprint profile.",
        )
    if old_dns != new_dns:
        _add_runtime_warning(
            fingerprint_profile,
            "dns_recreate_required: background network consensus changed DNS requirements; restart this session to apply container DNS.",
        )

    await pool.execute(
        "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
        fingerprint_profile,
        session_id,
    )
    _store_network_profile_cache(cache_key, new_network)
    try:
        await sync_fingerprint_profile_to_container(session_id, fingerprint_profile)
    except Exception as exc:
        logger.warning("Background profile sync failed for %s: %s", session_id, exc)


async def create_container(
    session_id: str,
    width: int = 1920,
    height: int = 1080,
    user_agent: str | None = None,
    proxy: str | None = None,
    fingerprint_profile: dict | None = None,
    browser_lang: str = "zh-CN",
    image_name: str | None = None,
) -> str:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    env = {
        **_STATIC_ENV,
        "SE_SCREEN_WIDTH": str(width),
        "SE_SCREEN_HEIGHT": str(height),
        "BROWSER_LANG": browser_lang,
    }
    if user_agent:
        env["BROWSER_UA"] = user_agent
    if proxy:
        env["BROWSER_PROXY"] = proxy
    if fingerprint_profile is not None:
        env["FINGERPRINT_PROFILE"] = base64.b64encode(
            json.dumps(fingerprint_profile, separators=(",", ":")).encode()
        ).decode()
    env_args = " ".join(f"-e {k}='{v}'" for k, v in env.items())
    dns_args = " ".join(f"--dns {shlex.quote(server)}" for server in _dns_servers_from_profile(fingerprint_profile))
    if not image_name:
        raise RuntimeError(
            "No browser image available. Build one in Settings > Browser Images."
        )
    logger.info("Creating container: %s (%dx%d)", name, width, height)
    last_error = ""
    for _attempt in range(5):
        selenium_port = _find_free_port()
        vnc_port = _find_free_port()
        cmd = (
            f"docker run -d --name {name} "
            f"--label {_PREFIX}.session_id={session_id} "
            f"-p {selenium_port}:4444 -p {vnc_port}:7900 "
            f"--shm-size={SHM_SIZE} "
            f"{dns_args + ' ' if dns_args else ''}"
            f"-v {vol_name}:/home/seluser/chrome-data "
            f"{env_args} "
            f"{image_name}"
        )
        stdout, stderr, rc = await _run(cmd, timeout=30)
        if rc == 0:
            logger.info(
                "Container created: %s -> %s (selenium=%s vnc=%s)",
                name,
                stdout[:12],
                selenium_port,
                vnc_port,
            )
            return stdout
        last_error = stderr or stdout
        if "port is already allocated" not in last_error.lower():
            break
    raise RuntimeError(f"docker run failed: {last_error[:300]}")


async def start_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Starting container: %s", name)
    _, stderr, rc = await _run(f"docker start {name}", timeout=15)
    if rc != 0:
        raise RuntimeError(f"docker start failed: {stderr[:300]}")


async def stop_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Stopping container: %s", name)
    _, stderr, rc = await _run(f"docker stop {name}", timeout=30)
    if rc != 0:
        raise RuntimeError(f"docker stop failed: {stderr[:300]}")


async def pause_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Pausing container: %s", name)
    _, stderr, rc = await _run(f"docker pause {name}", timeout=10)
    if rc != 0:
        raise RuntimeError(f"docker pause failed: {stderr[:300]}")


async def unpause_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Unpausing container: %s", name)
    _, stderr, rc = await _run(f"docker unpause {name}", timeout=10)
    if rc != 0:
        raise RuntimeError(f"docker unpause failed: {stderr[:300]}")


async def remove_container(session_id: str, *, keep_volume: bool = False) -> None:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    logger.info("Removing container: %s (keep_volume=%s)", name, keep_volume)
    _, stderr, rc = await _run(f"docker rm -f {name}", timeout=15)
    if rc != 0:
        logger.warning("docker rm -f %s failed (rc=%d): %s — ignoring", name, rc, stderr[:200])
    if not keep_volume:
        await _run(f"docker volume rm -f {vol_name}", timeout=10)


async def get_container_ports(session_id: str) -> dict[str, int]:
    name = container_name(session_id)
    stdout, stderr, rc = await _run(f"docker port {name}", timeout=5)
    if rc != 0:
        raise RuntimeError(f"docker port failed: {stderr[:300]}")
    ports: dict[str, int] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # Format: "4444/tcp -> 0.0.0.0:49152"  or  "7900/tcp -> 0.0.0.0:49153"
        left, _, right = line.partition("->")
        container_port = left.strip().split("/")[0]
        host_port = right.strip().rsplit(":", 1)[-1]
        if container_port == "4444":
            ports["selenium_port"] = int(host_port)
        elif container_port == "7900":
            ports["vnc_port"] = int(host_port)
    if "selenium_port" not in ports or "vnc_port" not in ports:
        raise RuntimeError(f"Could not parse ports from: {stdout}")
    return ports


async def get_container_status(session_id: str) -> str:
    name = container_name(session_id)
    stdout, _, rc = await _run(
        f"docker inspect --format '{{{{.State.Status}}}}' {name}", timeout=5
    )
    if rc != 0:
        return "not_found"
    return stdout.strip("'\" \n")


async def start_cdp_logger(session_id: str) -> None:
    name = container_name(session_id)
    await _run(f"docker exec {name} supervisorctl start cdp-logger", timeout=5)


async def stop_cdp_logger(session_id: str) -> None:
    name = container_name(session_id)
    await _run(f"docker exec {name} supervisorctl stop cdp-logger", timeout=5)


async def get_all_container_statuses() -> dict[str, str]:
    """Batch query all bp- containers. Returns {container_name: status}."""
    stdout, _, rc = await _run(
        f'docker ps -a --filter "name={CONTAINER_PREFIX}" --format '
        '"{{.Names}}\\t{{.State}}"',
        timeout=10,
    )
    if rc != 0:
        return {}
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if "\t" not in line:
            continue
        cname, state = line.split("\t", 1)
        prefix = cname.removeprefix(CONTAINER_PREFIX)
        result[prefix] = state.strip().lower()
    return result


async def _wait_grid_ready(selenium_port: int) -> None:
    url = f"http://{DOCKER_HOST_ADDR}:{selenium_port}/status"
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=3) as client:
        while elapsed < GRID_READY_TIMEOUT:
            try:
                resp = await client.get(url)
                data = resp.json()
                if data.get("value", {}).get("ready"):
                    logger.info("Grid ready on port %d (%.1fs)", selenium_port, elapsed)
                    return
            except Exception:
                pass
            await asyncio.sleep(GRID_POLL_INTERVAL)
            elapsed += GRID_POLL_INTERVAL
    logger.warning("Grid readiness timeout on port %d after %.1fs", selenium_port, elapsed)


async def reconcile_browser_network_profile(
    session_id: str,
    ports: dict[str, int],
    *,
    width: int,
    height: int,
    user_agent: str | None,
    proxy: str | None,
    fingerprint_profile: dict | None,
    browser_lang: str,
    image_name: str | None,
    allow_dns_recreate: bool = True,
) -> tuple[dict[str, int], dict | None]:
    """Replace provisional network data with what running Chrome observes."""
    if not _needs_browser_network_reconcile(fingerprint_profile):
        return ports, fingerprint_profile
    if not isinstance(fingerprint_profile, dict):
        return ports, fingerprint_profile

    cache_key = _network_cache_key(
        image_name=image_name,
        proxy=proxy,
        fingerprint_profile=fingerprint_profile,
    )
    observed = _cached_network_profile(cache_key)
    if observed is None:
        observed = await resolve_network_via_browser(ports, session_id=session_id, mode="fast")
    if observed.get("source") == "unresolved":
        _add_runtime_warning(
            fingerprint_profile,
            "network_probe_pending: fast hidden browser network probe did not complete; provisional network profile is still in use while background consensus continues.",
        )
        await get_pool().execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
            fingerprint_profile,
            session_id,
        )
        _schedule_background_network_consensus(session_id, ports, cache_key=cache_key)
        return ports, fingerprint_profile

    old_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    old_signature = _network_signature(old_network)
    old_dns = _dns_servers_from_profile(fingerprint_profile)

    attach_network_profile(fingerprint_profile, observed)
    new_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    observed_via = str(observed.get("observedVia") or "browser-hidden-cdp")
    new_network["observedVia"] = observed_via
    new_network.setdefault("provisionalSource", old_network.get("source") or "")
    fingerprint_profile["network"] = new_network
    _add_network_runtime_warnings(fingerprint_profile, new_network)

    new_signature = _network_signature(new_network)
    new_dns = _dns_servers_from_profile(fingerprint_profile)
    if old_signature != new_signature:
        _add_runtime_warning(
            fingerprint_profile,
            "network_profile_reconciled: browser-observed network differed from provisional profile; fingerprint profile was updated before first navigation.",
        )

    await get_pool().execute(
        "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
        fingerprint_profile,
        session_id,
    )

    if allow_dns_recreate and old_dns != new_dns:
        try:
            logger.info(
                "Recreating %s to apply browser-observed DNS: %s -> %s",
                container_name(session_id),
                ",".join(old_dns),
                ",".join(new_dns),
            )
            ports = await recreate_container(
                session_id,
                width=width,
                height=height,
                user_agent=user_agent,
                proxy=proxy,
                fingerprint_profile=fingerprint_profile,
                browser_lang=browser_lang,
                image_name=image_name,
                reconcile_network=False,
            )
        except Exception as exc:
            _add_runtime_warning(
                fingerprint_profile,
                f"dns_recreate_failed: DNS needed container recreation but recreation failed: {exc}",
            )
            await get_pool().execute(
                "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
                fingerprint_profile,
                session_id,
            )
            logger.warning("DNS recreate failed for %s: %s", session_id, exc)
            try:
                await sync_fingerprint_profile_to_container(session_id, fingerprint_profile)
            except Exception as sync_exc:
                logger.warning("Profile sync after DNS recreate failure also failed for %s: %s", session_id, sync_exc)
    else:
        try:
            await sync_fingerprint_profile_to_container(session_id, fingerprint_profile)
        except Exception as exc:
            _add_runtime_warning(
                fingerprint_profile,
                f"profile_sync_failed: browser-observed network was stored but running container profile sync failed: {exc}",
            )
            await get_pool().execute(
                "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
                fingerprint_profile,
                session_id,
            )
            logger.warning("Profile sync failed for %s: %s", session_id, exc)

    _store_network_profile_cache(cache_key, new_network)
    updated_cache_key = _network_cache_key(
        image_name=image_name,
        proxy=proxy,
        fingerprint_profile=fingerprint_profile,
    )
    if updated_cache_key != cache_key:
        _store_network_profile_cache(updated_cache_key, new_network)
    _schedule_background_network_consensus(session_id, ports, cache_key=cache_key)
    return ports, fingerprint_profile


async def _session_container_params(session_id: str) -> tuple[int, int, str | None, str | None, dict | None, str, str | None]:
    """Read device_preset + proxy_url + fingerprint_profile + browser_lang + chrome_version from DB and resolve to container params."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT device_preset, proxy_url, fingerprint_profile, browser_lang, tenant_id, chrome_version FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        preset_data = get_preset(DEFAULT_PRESET)
        return preset_data["width"], preset_data["height"], None, None, None, "zh-CN", None
    preset_data = get_preset(row["device_preset"] or DEFAULT_PRESET)
    proxy = row["proxy_url"] or None
    browser_lang = row["browser_lang"] or "zh-CN"

    fp_profile = row["fingerprint_profile"]
    if fp_profile is None and row["tenant_id"]:
        fp_profile = await generate_profile(row["tenant_id"], browser_lang=browser_lang)
        await pool.execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb WHERE id = $2",
            fp_profile, session_id,
        )
        logger.info("Lazy-generated fingerprint profile for session %s", session_id)

    chrome_version = row.get("chrome_version")
    tenant_id = row["tenant_id"]
    image_name: str | None = None
    if chrome_version and tenant_id:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' LIMIT 1",
            tenant_id, chrome_version,
        )
        if img_row:
            image_name = img_row["image_tag"]

    if not image_name and tenant_id:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND status = 'ready' ORDER BY chrome_major DESC LIMIT 1",
            row["tenant_id"],
        )
        if img_row:
            image_name = img_row["image_tag"]

    if isinstance(fp_profile, dict) and not fp_profile.get("network"):
        network = declared_network_profile(proxy, image_name)
        attach_network_profile(fp_profile, network)
        await pool.execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb WHERE id = $2",
            fp_profile,
            session_id,
        )
        logger.info("Attached network profile for session %s before container start", session_id)

    ua = None
    if fp_profile and isinstance(fp_profile, dict):
        ua = fp_profile.get("navigator", {}).get("userAgent")
    return preset_data["width"], preset_data["height"], ua, proxy, fp_profile, browser_lang, image_name


async def ensure_container_running(session_id: str) -> dict[str, int]:
    status = await get_container_status(session_id)

    if status == "not_found":
        width, height, ua, proxy, fp_profile, lang, img = await _session_container_params(session_id)
        await create_container(session_id, width=width, height=height, user_agent=ua, proxy=proxy, fingerprint_profile=fp_profile, browser_lang=lang, image_name=img)
        ports = await get_container_ports(session_id)
        await _wait_grid_ready(ports["selenium_port"])
        return ports

    if status == "paused":
        await unpause_container(session_id)
        return await get_container_ports(session_id)

    if status != "running":
        width, height, ua, proxy, fp_profile, lang, img = await _session_container_params(session_id)
        await start_container(session_id)
        ports = await get_container_ports(session_id)
        await _wait_grid_ready(ports["selenium_port"])
        return ports

    ports = await get_container_ports(session_id)
    await _wait_grid_ready(ports["selenium_port"])
    return ports


async def recreate_container(
    session_id: str,
    width: int,
    height: int,
    user_agent: str | None = None,
    proxy: str | None = None,
    fingerprint_profile: dict | None = None,
    browser_lang: str = "zh-CN",
    image_name: str | None = None,
    reconcile_network: bool = False,
) -> dict[str, int]:
    """Stop, remove (keep volume), create with new params, wait for grid."""
    try:
        await stop_container(session_id)
    except Exception:
        pass
    await remove_container(session_id, keep_volume=True)
    await create_container(session_id, width=width, height=height, user_agent=user_agent, proxy=proxy, fingerprint_profile=fingerprint_profile, browser_lang=browser_lang, image_name=image_name)
    ports = await get_container_ports(session_id)
    await _wait_grid_ready(ports["selenium_port"])
    if reconcile_network:
        ports, _ = await reconcile_browser_network_profile(
            session_id,
            ports,
            width=width,
            height=height,
            user_agent=user_agent,
            proxy=proxy,
            fingerprint_profile=fingerprint_profile,
            browser_lang=browser_lang,
            image_name=image_name,
        )
    return ports
