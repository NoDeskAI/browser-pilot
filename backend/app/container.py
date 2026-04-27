from __future__ import annotations

import asyncio
import base64
from collections import Counter, defaultdict
import ipaddress
import json
import logging
import shlex
import socket
from typing import Any

import httpx

from app.config import DOCKER_HOST_ADDR, CONTAINER_PREFIX as _PREFIX
from app.db import get_pool
from app.device_presets import DEVICE_PRESETS, DEFAULT_PRESET, get_preset
from app.fingerprint import (
    attach_network_profile,
    failed_network_profile,
    generate_profile,
    normalize_network_probe,
    resolve_network_via_container,
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
    return network.get("observedVia") not in ("browser", "container-fallback-neutral")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


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


async def _wd_request(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    method: str = "GET",
    body: Any = None,
    timeout: float = BROWSER_NETWORK_PROBE_TIMEOUT,
) -> Any:
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    resp = await client.request(method, f"{base_url}{path}", timeout=timeout, **kwargs)
    data = resp.json()
    value = data.get("value", data)
    if isinstance(value, dict) and value.get("error"):
        raise RuntimeError(f"WebDriver {value['error']}: {value.get('message', '')}")
    return value


async def _create_probe_webdriver_session(client: httpx.AsyncClient, base_url: str) -> str:
    value = await _wd_request(
        client,
        base_url,
        "/session",
        "POST",
        {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "goog:chromeOptions": {"debuggerAddress": "localhost:9222"},
                },
            }
        },
        timeout=15,
    )
    session_id = value.get("sessionId") if isinstance(value, dict) else None
    if not session_id:
        raise RuntimeError("WebDriver attach did not return a session id")
    return str(session_id)


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


async def _read_probe_page_text(
    client: httpx.AsyncClient,
    base_url: str,
    webdriver_session_id: str,
    url: str,
) -> str:
    await _wd_request(
        client,
        base_url,
        f"/session/{webdriver_session_id}/url",
        "POST",
        {"url": url},
        timeout=20,
    )
    await asyncio.sleep(1.0)
    text = await _wd_request(
        client,
        base_url,
        f"/session/{webdriver_session_id}/execute/sync",
        "POST",
        {
            "script": "return document.body ? document.body.innerText : document.documentElement.innerText",
            "args": [],
        },
        timeout=10,
    )
    return str(text or "")


async def _read_probe_page_json(
    client: httpx.AsyncClient,
    base_url: str,
    webdriver_session_id: str,
    url: str,
) -> dict[str, Any]:
    return _json_from_text(await _read_probe_page_text(client, base_url, webdriver_session_id, url))


async def _resolve_observed_ip_network(
    client: httpx.AsyncClient,
    base_url: str,
    webdriver_session_id: str,
    *,
    warnings: list[str],
) -> dict[str, Any] | None:
    networks: list[dict[str, Any]] = []
    for ip_api_name, ip_url in _BROWSER_OBSERVED_IP_APIS:
        try:
            ip_data = await _read_probe_page_json(client, base_url, webdriver_session_id, ip_url)
            observed_ip = _extract_observed_ip(ip_api_name, ip_data)
            if not observed_ip:
                warnings.append(f"{ip_api_name} response missing usable observed IP")
                continue

            for geo_api_name, geo_url in _BROWSER_IP_GEO_APIS:
                try:
                    geo_data = await _read_probe_page_json(
                        client,
                        base_url,
                        webdriver_session_id,
                        geo_url.format(ip=observed_ip),
                    )
                    network = normalize_network_probe(geo_api_name, geo_data)
                    if network:
                        network["source"] = f"browser:{ip_api_name}+{geo_api_name}"
                        network["observedVia"] = "browser"
                        network["observedIpSource"] = ip_api_name
                        networks.append(network)
                    else:
                        warnings.append(f"{ip_api_name}+{geo_api_name} response missing usable geo/timezone")
                except Exception as exc:
                    warnings.append(f"{ip_api_name}+{geo_api_name} browser probe failed: {exc}")
        except Exception as exc:
            warnings.append(f"{ip_api_name} browser IP probe failed: {exc}")
    return _select_network_consensus(networks, warnings)


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


async def resolve_network_via_browser(ports: dict[str, int], session_id: str | None = None) -> dict[str, Any]:
    """Resolve network metadata from the already-running Chrome page path."""
    base_url = f"http://{DOCKER_HOST_ADDR}:{ports['selenium_port']}"
    warnings: list[str] = []
    webdriver_session_id: str | None = None
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            webdriver_session_id = await _create_probe_webdriver_session(client, base_url)
            networks: list[dict[str, Any]] = []
            observed_ip_network = await _resolve_observed_ip_network(
                client,
                base_url,
                webdriver_session_id,
                warnings=warnings,
            )
            if observed_ip_network:
                networks.append(observed_ip_network)
            for api_name, url in _BROWSER_NETWORK_APIS:
                try:
                    data = await _read_probe_page_json(client, base_url, webdriver_session_id, url)
                    network = normalize_network_probe(api_name, data)
                    if network:
                        network["source"] = f"browser:{api_name}"
                        network["observedVia"] = "browser"
                        networks.append(network)
                    else:
                        warnings.append(f"{api_name} response missing usable IP/timezone")
                except Exception as exc:
                    warnings.append(f"{api_name} browser probe failed: {exc}")
            selected = _select_network_consensus(networks, warnings)
            if selected:
                selected["observedVia"] = "browser"
                logger.info(
                    "Resolved network via neutral browser consensus: ip=%s tz=%s dns=%s confidence=%s",
                    selected.get("ip"),
                    selected.get("timezone"),
                    ",".join(selected.get("dnsServers", [])),
                    selected.get("confidence"),
                )
                return selected
        except Exception as exc:
            warnings.append(f"browser probe setup failed: {exc}")
        finally:
            if webdriver_session_id:
                try:
                    await _wd_request(
                        client,
                        base_url,
                        f"/session/{webdriver_session_id}/window",
                        "POST",
                        {"handle": await _wd_request(client, base_url, f"/session/{webdriver_session_id}/window")},
                        timeout=3,
                    )
                except Exception:
                    pass
                try:
                    await _wd_request(
                        client,
                        base_url,
                        f"/session/{webdriver_session_id}/url",
                        "POST",
                        {"url": "chrome://newtab/"},
                        timeout=5,
                    )
                except Exception:
                    pass
                try:
                    await client.delete(f"{base_url}/session/{webdriver_session_id}", timeout=5)
                except Exception:
                    pass
    if session_id:
        fallback = await _resolve_neutral_network_via_container(session_id, warnings=warnings)
        if fallback:
            return fallback
    logger.warning("Browser network probes failed: %s", "; ".join(warnings))
    return failed_network_profile("all browser network probes failed", warnings)


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

    observed = await resolve_network_via_browser(ports, session_id=session_id)
    if observed.get("source") == "unresolved":
        _add_runtime_warning(
            fingerprint_profile,
            "browser_network_probe_failed: browser-observed network probe failed; provisional network profile is still in use.",
        )
        await get_pool().execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
            fingerprint_profile,
            session_id,
        )
        return ports, fingerprint_profile

    old_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    old_signature = _network_signature(old_network)
    old_dns = _dns_servers_from_profile(fingerprint_profile)

    attach_network_profile(fingerprint_profile, observed)
    new_network = fingerprint_profile.get("network") if isinstance(fingerprint_profile.get("network"), dict) else {}
    observed_via = str(observed.get("observedVia") or "browser")
    new_network["observedVia"] = observed_via
    new_network.setdefault("provisionalSource", old_network.get("source") or "")
    fingerprint_profile["network"] = new_network
    if observed_via == "container-fallback-neutral":
        _add_runtime_warning(
            fingerprint_profile,
            "browser_network_probe_fallback: browser probe failed; neutral network consensus was resolved through the running container path and container DNS was reconciled.",
        )
    network_warnings = [str(w) for w in new_network.get("warnings", []) if str(w or "").strip()]
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

    if isinstance(fp_profile, dict) and not fp_profile.get("network") and image_name:
        network = await resolve_network_via_container(proxy, image_name)
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
        ports, _ = await reconcile_browser_network_profile(
            session_id,
            ports,
            width=width,
            height=height,
            user_agent=ua,
            proxy=proxy,
            fingerprint_profile=fp_profile,
            browser_lang=lang,
            image_name=img,
        )
        return ports

    if status == "paused":
        await unpause_container(session_id)
        return await get_container_ports(session_id)

    if status != "running":
        width, height, ua, proxy, fp_profile, lang, img = await _session_container_params(session_id)
        await start_container(session_id)
        ports = await get_container_ports(session_id)
        await _wait_grid_ready(ports["selenium_port"])
        ports, _ = await reconcile_browser_network_profile(
            session_id,
            ports,
            width=width,
            height=height,
            user_agent=ua,
            proxy=proxy,
            fingerprint_profile=fp_profile,
            browser_lang=lang,
            image_name=img,
        )
        return ports

    width, height, ua, proxy, fp_profile, lang, img = await _session_container_params(session_id)
    ports = await get_container_ports(session_id)
    await _wait_grid_ready(ports["selenium_port"])
    ports, _ = await reconcile_browser_network_profile(
        session_id,
        ports,
        width=width,
        height=height,
        user_agent=ua,
        proxy=proxy,
        fingerprint_profile=fp_profile,
        browser_lang=lang,
        image_name=img,
    )
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
    reconcile_network: bool = True,
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
