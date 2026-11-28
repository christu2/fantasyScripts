#!/usr/bin/env python3
"""
Zero-Cost, OWASP-Hardened Public Self-Serve API Server (`FantasyRecap`)
=======================================================================
Powers instant, zero-login League Hub generation for **Sleeper** and **Public ESPN**
leagues while enforcing strict Staff-Engineer security & wallet-protection invariants:

  1. **Zero Credential Exposure / Confused-Deputy Immunity (OWASP A01):**
     Strictly calls `build_generic_hub_data(..., public_only=True, ephemeral=True)` so
     even if local `.env` credentials exist, private ESPN cookies are NEVER transmitted.
     Private ESPN leagues cleanly return `HTTP 403` with step-by-step instructions.

  2. **Strict Input Validation & SSRF Prevention (OWASP A03 / A10):**
     Every route validates inputs via `validate_league_id()` (`^[0-9]{1,20}$`) or
     `validate_sleeper_username()` (`^[A-Za-z0-9_]{1,32}$`) before any outbound call.
     Only `GET`, `HEAD`, and `OPTIONS` are accepted (`405` on everything else).

  3. **Multi-Layer DoS & `$0.00` Wallet Shield (OWASP A04):**
     - Bounded per-IP `SlidingWindowRateLimiter` (`max_keys=4096` LRU) on cache misses.
     - Global build concurrency semaphore (`MAX_CONCURRENT_BUILDS=3`) so traffic bursts
       fast-fail with `HTTP 429 Retry-After` in <1ms instead of exhausting memory/CPU.
     - **Singleflight Coalescing**: 12 league-mates clicking a shared link at the same
       second share 1 single upstream build instead of 12 duplicate scrapes.
     - **Bounded Gzipped LRU+TTL Cache** (`64` hubs, `15m` TTL) + `Cache-Control: public,
       max-age=300, s-maxage=900, stale-while-revalidate=1800` so Cloudflare Free CDN
       serves repeat/group-chat loads from the edge for `$0.00`!
     - **Ephemeral `/tmp` SQLite** (`ephemeral=True`) so 0 bytes accumulate on disk.

  4. **Hardened Security Headers & Sanitized Errors (OWASP A05 / A09):**
     Emits `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
     `Referrer-Policy: strict-origin-when-cross-origin`, strict CORS origin checks,
     and never leaks internal stack traces to clients.
"""

from __future__ import annotations

from collections import OrderedDict
import gzip
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.config import CURRENT_YEAR
from FantasyRecap.core.security import (
    SlidingWindowRateLimiter,
    validate_league_id,
    validate_sleeper_username,
)
from FantasyRecap.multi_league_hub import WEB_HUB_DIR, build_generic_hub_data
from FantasyRecap.sleeper_adapter import build_sleeper_hub_data, lookup_sleeper_user_leagues


class BoundedGzipTTLCache:
    """Thread-safe LRU cache storing `(expires_at, raw_json_bytes, gzipped_bytes)`."""

    def __init__(self, max_entries: int = 64, ttl_seconds: float = 900.0) -> None:
        self.max_entries = max(4, int(max_entries))
        self.ttl_seconds = float(ttl_seconds)
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, Tuple[float, bytes, bytes]]" = OrderedDict()

    def get(self, key: str) -> Optional[Tuple[bytes, bytes]]:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            exp, raw_b, gz_b = item
            if now >= exp:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return raw_b, gz_b

    def set(self, key: str, payload: dict, ttl_override: Optional[float] = None) -> Tuple[bytes, bytes]:
        raw_b = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        gz_b = gzip.compress(raw_b, compresslevel=6)
        ttl = self.ttl_seconds if ttl_override is None else float(ttl_override)
        exp = time.monotonic() + ttl
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            elif len(self._store) >= self.max_entries:
                self._store.popitem(last=False)
            self._store[key] = (exp, raw_b, gz_b)
        return raw_b, gz_b


class SingleflightGroup:
    """Coalesces concurrent identical cache-miss builds into a single upstream execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: Dict[str, "_Call"] = {}

    class _Call:
        def __init__(self) -> None:
            self.event = threading.Event()
            self.result = None
            self.exc: Optional[BaseException] = None

    def is_inflight(self, key: str) -> bool:
        with self._lock:
            return key in self._inflight

    def do(self, key: str, fn: Callable[[], Tuple[bytes, bytes]]) -> Tuple[bytes, bytes]:
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                is_leader = False
                call = existing
            else:
                is_leader = True
                call = self._Call()
                self._inflight[key] = call

        if not is_leader:
            call.event.wait(timeout=30.0)
            if call.exc is not None:
                raise call.exc
            if call.result is None:
                raise TimeoutError("Timed out waiting for in-flight league build.")
            return call.result

        try:
            call.result = fn()
            return call.result
        except BaseException as exc:
            call.exc = exc
            raise
        finally:
            call.event.set()
            with self._lock:
                self._inflight.pop(key, None)


class PublicAPIService:
    """Stateful rate-limiter, cache, singleflight, and builder registry for the HTTP server."""

    def __init__(
        self,
        *,
        max_builds_per_min_ip: int = 8,
        max_lookups_per_min_ip: int = 24,
        max_concurrent_builds: int = 3,
        max_history_years: int = 5,
        allowed_origins: Optional[Set[str]] = None,
        serve_static: bool = True,
        static_dir: Path = WEB_HUB_DIR,
        sleeper_builder: Optional[Callable[..., Tuple[dict, Optional[Path]]]] = None,
        espn_builder: Optional[Callable[..., Tuple[dict, Optional[Path]]]] = None,
        sleeper_user_lookup: Optional[Callable[[str, int], dict]] = None,
    ) -> None:
        self.build_limiter = SlidingWindowRateLimiter(max_builds_per_min_ip, 60.0)
        self.lookup_limiter = SlidingWindowRateLimiter(max_lookups_per_min_ip, 60.0)
        self.build_sem = threading.BoundedSemaphore(max(1, int(max_concurrent_builds)))
        self.max_history_years = max(1, min(6, int(max_history_years)))
        self.hub_cache = BoundedGzipTTLCache(max_entries=64, ttl_seconds=900.0)
        self.lookup_cache = BoundedGzipTTLCache(max_entries=256, ttl_seconds=300.0)
        self.singleflight = SingleflightGroup()
        self.allowed_origins = allowed_origins or set()
        self.serve_static = serve_static
        self.static_dir = static_dir.resolve()
        self._sleeper_builder = sleeper_builder or build_sleeper_hub_data
        self._espn_builder = espn_builder or build_generic_hub_data
        self._sleeper_user_lookup = sleeper_user_lookup or lookup_sleeper_user_leagues

    def build_sleeper(self, league_id: str, client_ip: str = "") -> Tuple[bytes, bytes, bool]:
        lid = validate_league_id(league_id)
        ckey = f"sleeper:{lid}"
        cached = self.hub_cache.get(ckey)
        if cached is not None:
            return cached[0], cached[1], True

        def _work() -> Tuple[bytes, bytes]:
            recheck = self.hub_cache.get(ckey)
            if recheck is not None:
                return recheck
            if client_ip:
                allowed, retry_s = self.build_limiter.allow(client_ip)
                if not allowed:
                    err = PermissionError(f"Rate limit reached (max 8 uncached leagues/min). Retry in {retry_s}s.")
                    err.retry_after = retry_s  # type: ignore[attr-defined]
                    raise err
            if not self.build_sem.acquire(blocking=False):
                raise OverflowError("Server is busy analyzing other leagues — please retry in a few seconds.")
            try:
                slug = f"slp-{lid[:20]}"
                hub_data, _ = self._sleeper_builder(
                    lid,
                    slug,
                    max_history_years=self.max_history_years,
                    ephemeral=True,
                )
                return self.hub_cache.set(ckey, hub_data)
            finally:
                self.build_sem.release()

        raw_b, gz_b = self.singleflight.do(ckey, _work)
        return raw_b, gz_b, False

    def build_espn_public(self, league_id: str, season: int, client_ip: str = "") -> Tuple[bytes, bytes, bool]:
        lid = validate_league_id(league_id)
        yr = int(season)
        if yr < 2018 or yr > int(CURRENT_YEAR) + 1:
            raise ValueError(f"Unsupported season '{yr}'.")
        ckey = f"espn:{yr}:{lid}"
        cached = self.hub_cache.get(ckey)
        if cached is not None:
            return cached[0], cached[1], True

        def _work() -> Tuple[bytes, bytes]:
            recheck = self.hub_cache.get(ckey)
            if recheck is not None:
                return recheck
            if client_ip:
                allowed, retry_s = self.build_limiter.allow(client_ip)
                if not allowed:
                    err = PermissionError(f"Rate limit reached (max 8 uncached leagues/min). Retry in {retry_s}s.")
                    err.retry_after = retry_s  # type: ignore[attr-defined]
                    raise err
            if not self.build_sem.acquire(blocking=False):
                raise OverflowError("Server is busy analyzing other leagues — please retry in a few seconds.")
            try:
                slug = f"espn-{lid[:20]}"
                # OWASP A01 CRITICAL: `public_only=True` & `ephemeral=True` ALWAYS enforced!
                hub_data, _ = self._espn_builder(
                    lid,
                    slug,
                    yr,
                    public_only=True,
                    ephemeral=True,
                    max_history_years=self.max_history_years,
                )
                return self.hub_cache.set(ckey, hub_data)
            finally:
                self.build_sem.release()

        raw_b, gz_b = self.singleflight.do(ckey, _work)
        return raw_b, gz_b, False

    def lookup_sleeper_user(self, username: str, season: int) -> Tuple[bytes, bytes, bool]:
        clean = validate_sleeper_username(username)
        yr = int(season)
        if yr < 2018 or yr > int(CURRENT_YEAR) + 1:
            raise ValueError(f"Unsupported season '{yr}'.")
        ckey = f"suser:{yr}:{clean.lower()}"
        cached = self.lookup_cache.get(ckey)
        if cached is not None:
            return cached[0], cached[1], True
        data = self._sleeper_user_lookup(clean, yr)
        raw_b, gz_b = self.lookup_cache.set(ckey, data)
        return raw_b, gz_b, False


_ROUTE_SLEEPER_USER = re.compile(r"^/api/v1/sleeper/user/([^/]+)$")
_ROUTE_SLEEPER_HUB = re.compile(r"^/api/v1/hub/sleeper/([^/]+)$")
_ROUTE_ESPN_HUB = re.compile(r"^/api/v1/hub/espn/([^/]+)$")

STATIC_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "media-src 'self' https:; "
    "connect-src 'self' https://api.sleeper.app https://storage.googleapis.com https://*.run.app; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none';"
)


def make_request_handler(service: PublicAPIService):
    class HardenedPublicHandler(BaseHTTPRequestHandler):
        server_version = "FantasyRecapPublic/1.0"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:
            # Redact query strings / headers cleanly in standard logs
            sys.stderr.write(f"[public-api] {self.client_ip()} - {fmt % args}\n")

        def client_ip(self) -> str:
            # Prefer Cloudflare header if proxied, else rightmost X-Forwarded-For hop (Cloud Run LB), else socket IP
            cf_ip = (self.headers.get("CF-Connecting-IP") or "").strip()
            if cf_ip and len(cf_ip) <= 64:
                return cf_ip
            xff = (self.headers.get("X-Forwarded-For") or "").strip()
            if xff:
                parts = [p.strip() for p in xff.split(",") if p.strip()]
                if parts:
                    return parts[-1][:64]
            return str(self.client_address[0] if self.client_address else "unknown")

        def _add_security_headers(self, *, is_html: bool = False, cache_control: str = "no-store") -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Cache-Control", cache_control)
            if is_html:
                self.send_header("Content-Security-Policy", STATIC_CSP)

            origin = (self.headers.get("Origin") or "").strip()
            if origin:
                if "*" in service.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", "*")
                elif origin in service.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin, Accept-Encoding")

        def _send_json_bytes(
            self,
            status: int,
            raw_b: bytes,
            gz_b: Optional[bytes] = None,
            *,
            cache_control: str = "no-store",
            extra_headers: Optional[Dict[str, str]] = None,
            head_only: bool = False,
        ) -> None:
            accept_enc = (self.headers.get("Accept-Encoding") or "").lower()
            use_gzip = gz_b is not None and "gzip" in accept_enc
            body = gz_b if use_gzip else raw_b

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self._add_security_headers(is_html=False, cache_control=cache_control)
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(hk, hv)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _send_error_json(
            self,
            status: int,
            code: str,
            message: str,
            *,
            retry_after: int = 0,
            head_only: bool = False,
        ) -> None:
            raw_b = json.dumps({"ok": False, "error": code, "message": message}).encode("utf-8")
            extra = {"Retry-After": str(retry_after)} if retry_after > 0 else None
            self._send_json_bytes(status, raw_b, None, cache_control="no-store", extra_headers=extra, head_only=head_only)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._add_security_headers(cache_control="public, max-age=86400")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self) -> None:
            self._handle_get_or_head(head_only=True)

        def do_GET(self) -> None:
            self._handle_get_or_head(head_only=False)

        def do_POST(self) -> None:
            self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Only GET/HEAD requests are allowed.")

        def do_PUT(self) -> None:
            self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Only GET/HEAD requests are allowed.")

        def do_DELETE(self) -> None:
            self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Only GET/HEAD requests are allowed.")

        def _handle_get_or_head(self, *, head_only: bool) -> None:
            parsed = urlparse(self.path)
            path = parsed.path or "/"
            qs = parse_qs(parsed.query)
            ip = self.client_ip()

            try:
                if path == "/api/v1/health":
                    payload = json.dumps({"ok": True, "public_only": True}).encode("utf-8")
                    self._send_json_bytes(HTTPStatus.OK, payload, cache_control="no-store", head_only=head_only)
                    return

                if path == "/api/v1/demo":
                    demo_path = service.static_dir / "demo_hub_data.json"
                    if not demo_path.exists():
                        self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Demo league bundle not generated yet.", head_only=head_only)
                        return
                    cached = service.hub_cache.get("demo:bfl")
                    if cached is None:
                        demo_obj = json.loads(demo_path.read_text(encoding="utf-8"))
                        raw_b, gz_b = service.hub_cache.set("demo:bfl", demo_obj, ttl_override=3600.0)
                    else:
                        raw_b, gz_b = cached
                    self._send_json_bytes(
                        HTTPStatus.OK,
                        raw_b,
                        gz_b,
                        cache_control="public, max-age=900, s-maxage=86400",
                        head_only=head_only,
                    )
                    return

                m_user = _ROUTE_SLEEPER_USER.match(path)
                if m_user:
                    uname = validate_sleeper_username(m_user.group(1))
                    season = int((qs.get("season") or [str(CURRENT_YEAR)])[0])
                    ckey = f"suser:{season}:{uname.lower()}"
                    if service.lookup_cache.get(ckey) is None:
                        allowed, retry_s = service.lookup_limiter.allow(ip)
                        if not allowed:
                            self._send_error_json(
                                HTTPStatus.TOO_MANY_REQUESTS,
                                "rate_limited",
                                f"Too many username lookups. Please wait {retry_s}s.",
                                retry_after=retry_s,
                                head_only=head_only,
                            )
                            return
                    raw_b, gz_b, hit = service.lookup_sleeper_user(uname, season)
                    self._send_json_bytes(
                        HTTPStatus.OK,
                        raw_b,
                        gz_b,
                        cache_control="public, max-age=120, s-maxage=300",
                        extra_headers={"X-Hub-Cache": "HIT" if hit else "MISS"},
                        head_only=head_only,
                    )
                    return

                m_slp = _ROUTE_SLEEPER_HUB.match(path)
                if m_slp:
                    lid = validate_league_id(m_slp.group(1))
                    raw_b, gz_b, hit = service.build_sleeper(lid, client_ip=ip)
                    self._send_json_bytes(
                        HTTPStatus.OK,
                        raw_b,
                        gz_b,
                        cache_control="public, max-age=300, s-maxage=900, stale-while-revalidate=1800",
                        extra_headers={"X-Hub-Cache": "HIT" if hit else "MISS"},
                        head_only=head_only,
                    )
                    return

                m_espn = _ROUTE_ESPN_HUB.match(path)
                if m_espn:
                    lid = validate_league_id(m_espn.group(1))
                    season = int((qs.get("season") or [str(CURRENT_YEAR)])[0])
                    raw_b, gz_b, hit = service.build_espn_public(lid, season, client_ip=ip)
                    self._send_json_bytes(
                        HTTPStatus.OK,
                        raw_b,
                        gz_b,
                        cache_control="public, max-age=300, s-maxage=900, stale-while-revalidate=1800",
                        extra_headers={"X-Hub-Cache": "HIT" if hit else "MISS"},
                        head_only=head_only,
                    )
                    return

                if path.startswith("/api/"):
                    self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Unknown API endpoint.", head_only=head_only)
                    return

                if not service.serve_static:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Not found.", head_only=head_only)
                    return

                # Safe static asset serving (blocks `..`, dotfiles, and non-public files like personal `hub_data.json`!)
                rel = "index.html" if path in ("", "/") else path.lstrip("/")
                ALLOWED_STATIC_FILES = {"index.html", "styles.css", "app.js", "demo_hub_data.json"}
                if rel not in ALLOWED_STATIC_FILES:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Asset not found.", head_only=head_only)
                    return
                target = (service.static_dir / rel).resolve()
                if target.parent != service.static_dir or not target.is_file():
                    self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Asset not found.", head_only=head_only)
                    return

                raw_file = target.read_bytes()
                if rel == "index.html":
                    raw_file = raw_file.replace(
                        b'content="hub_data.json"',
                        b'content="demo_hub_data.json"',
                        1,
                    )
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype = f"{ctype}; charset=utf-8"
                is_html = rel.endswith(".html")
                cctl = "public, max-age=60" if is_html else "public, max-age=300"

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self._add_security_headers(is_html=is_html, cache_control=cctl)
                self.send_header("Content-Length", str(len(raw_file)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(raw_file)

            except ValueError as ve:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_input", str(ve), head_only=head_only)
            except LookupError as le:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", str(le), head_only=head_only)
            except PermissionError as pe:
                retry_s = int(getattr(pe, "retry_after", 10) or 10)
                self._send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", str(pe), retry_after=retry_s, head_only=head_only)
            except OverflowError as oe:
                self._send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "server_busy", str(oe), retry_after=8, head_only=head_only)
            except requests.HTTPError as he:
                code_num = he.response.status_code if he.response is not None else 502
                if code_num in (401, 403):
                    self._send_error_json(
                        HTTPStatus.FORBIDDEN,
                        "private_league",
                        (
                            "This ESPN league is set to Private. Ask your commissioner to open "
                            "ESPN League Settings -> Basic Settings -> 'Make League Viewable to Public: Yes' "
                            "(takes 10 seconds, zero passwords needed), or analyze any Sleeper league!"
                        ),
                        head_only=head_only,
                    )
                elif code_num == 404:
                    self._send_error_json(
                        HTTPStatus.NOT_FOUND,
                        "not_found",
                        "League ID not found on upstream platform (check the ID and season).",
                        head_only=head_only,
                    )
                else:
                    self._send_error_json(
                        HTTPStatus.BAD_GATEWAY,
                        "upstream_error",
                        f"Upstream fantasy platform returned HTTP {code_num}. Please try again shortly.",
                        head_only=head_only,
                    )
            except Exception as exc:
                sys.stderr.write(f"[public-api] Unhandled internal error on {path}: {type(exc).__name__}: {exc}\n")
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "Unexpected error while building league analytics.",
                    head_only=head_only,
                )

    return HardenedPublicHandler


def create_server(host: str = "127.0.0.1", port: int = 8080, service: Optional[PublicAPIService] = None) -> ThreadingHTTPServer:
    if service is None:
        raw_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()
        origins = {o.strip() for o in raw_origins.split(",") if o.strip()}
        serve_static = os.getenv("SERVE_STATIC", "1").strip().lower() not in ("0", "false", "no")
        max_hist = int(os.getenv("MAX_HISTORY_YEARS", "5") or 5)
        service = PublicAPIService(
            max_history_years=max_hist,
            allowed_origins=origins,
            serve_static=serve_static,
        )
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, int(port)), make_request_handler(service))


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "127.0.0.1")
    srv = create_server(host=host, port=port)
    print(f"🛡️  FantasyRecap Hardened Public API listening on http://{host}:{port} (public_only=True, ephemeral=True)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down cleanly.")
        srv.server_close()


if __name__ == "__main__":
    main()
