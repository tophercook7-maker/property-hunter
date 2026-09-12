"""Polite HTTP client: identifies itself, honours robots.txt, rate limits.

We never try to defeat access controls. A 403/robots deny becomes an honest
"blocked" result that the source layer reports upward (spec 16).
"""
from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .config import HTTP_TIMEOUT, RATE_LIMIT_SECONDS, USER_AGENT

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


class Blocked(Exception):
    """Raised when a fetch is disallowed - never worked around."""


@dataclass
class Response:
    status: int
    text: str
    url: str
    json: object | None = None


def _host(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _throttle(host: str) -> None:
    with _lock:
        last = _last_hit.get(host, 0.0)
        wait = RATE_LIMIT_SECONDS - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def robots_allows(url: str) -> bool:
    host = _host(url)
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            with httpx.Client(timeout=15, follow_redirects=True,
                              headers={"User-Agent": USER_AGENT}) as c:
                r = c.get(host + "/robots.txt")
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])            # unreachable robots.txt -> treat as permissive
        _robots[host] = rp
    rp = _robots[host]
    try:
        return rp.can_fetch(USER_AGENT, url) if rp else True
    except Exception:
        return True


def get(url: str, params: dict | None = None, *, as_json: bool = False,
        timeout: float | None = None, check_robots: bool = True,
        accept: str = "*/*") -> Response:
    if check_robots and not robots_allows(url):
        raise Blocked(f"robots.txt disallows {url}")
    _throttle(_host(url))
    with httpx.Client(timeout=timeout or HTTP_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT, "Accept": accept}) as c:
        r = c.get(url, params=params)
    if r.status_code in (401, 403, 407, 429):
        raise Blocked(f"HTTP {r.status_code} from {url} - access restricted; not bypassed")
    payload = None
    if as_json:
        try:
            payload = r.json()
        except Exception as exc:
            raise ValueError(f"non-JSON response from {url}: {exc}") from exc
    return Response(status=r.status_code, text=r.text, url=str(r.url), json=payload)


def arcgis_query(service_url: str, layer: int, *, where: str = "1=1",
                 out_fields: str = "*", geometry: bool = False,
                 out_sr: int = 4326, extra: dict | None = None,
                 result_offset: int | None = None,
                 result_record_count: int | None = None,
                 timeout: float | None = None) -> dict:
    """One page of an Esri FeatureServer/MapServer query."""
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if geometry else "false",
        "outSR": out_sr,
        "f": "json",
    }
    if result_offset is not None:
        params["resultOffset"] = result_offset
    if result_record_count is not None:
        params["resultRecordCount"] = result_record_count
    if extra:
        params.update(extra)
    url = f"{service_url.rstrip('/')}/{layer}/query"
    # Esri REST endpoints are published for programmatic use; robots.txt on
    # these GIS hosts is typically absent, and we still rate-limit ourselves.
    resp = get(url, params=params, as_json=True, timeout=timeout, check_robots=False)
    data = resp.json or {}
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        raise RuntimeError(f"ArcGIS error {err.get('code')}: {err.get('message')} "
                           f"{'; '.join(err.get('details') or [])}")
    return data


def arcgis_count(service_url: str, layer: int, where: str = "1=1") -> int:
    data = arcgis_query(service_url, layer, where=where,
                        extra={"returnCountOnly": "true"})
    return int(data.get("count", 0))


def fetch_bytes(url: str, timeout: float | None = None) -> bytes:
    """Raw bytes (images) from a host we already talk to; still rate-limited."""
    _throttle(_host(url))
    with httpx.Client(timeout=timeout or HTTP_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
    if r.status_code in (401, 403, 407, 429):
        raise Blocked(f"HTTP {r.status_code} from {url}")
    r.raise_for_status()
    return r.content
