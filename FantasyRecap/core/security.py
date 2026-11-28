"""
OWASP Top 10 Input Validation, SSRF Guards & Multi-Tenant Token Helpers
=======================================================================
Shared security primitives used across multi-league ingestion, telemetry,
web hub deployment, and outbound webhooks.
"""

from __future__ import annotations

from collections import OrderedDict
import re
import secrets
import threading
import time
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

LEAGUE_ID_RE = re.compile(r"^[0-9]{1,20}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
SLEEPER_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")

ALLOWED_WEBHOOK_HOSTS = {
    "discord.com": ("/api/webhooks/",),
    "discordapp.com": ("/api/webhooks/",),
    "hooks.slack.com": ("/services/",),
    "api.groupme.com": ("/v3/bots/",),
}


def validate_league_id(league_id: str) -> str:
    """Validates ESPN / Sleeper numeric league IDs strictly to prevent SSRF & path injection."""
    val = str(league_id or "").strip()
    if not LEAGUE_ID_RE.match(val):
        raise ValueError(f"Invalid league_id '{val}': must be 1–20 ASCII digits.")
    return val


def extract_league_id_from_input(raw_input: str) -> str:
    """
    Accepts either a raw numeric League ID or a full ESPN / Sleeper league URL
    (e.g. `https://fantasy.espn.com/football/league?leagueId=755112359` or
    `https://sleeper.com/leagues/1313213902351273984/matchup`) and returns a
    strictly validated numeric `league_id`.
    """
    raw = str(raw_input or "").strip()
    if LEAGUE_ID_RE.match(raw):
        return raw
    if "://" in raw or raw.startswith("fantasy.espn.com") or raw.startswith("sleeper.com"):
        to_parse = raw if "://" in raw else f"https://{raw}"
        parsed = urlparse(to_parse)
        qs = parse_qs(parsed.query)
        if "leagueId" in qs and qs["leagueId"]:
            return validate_league_id(qs["leagueId"][0])
        m = re.search(r"/leagues/([0-9]{1,20})(?:/|$)", parsed.path or "")
        if m:
            return validate_league_id(m.group(1))
    raise ValueError("Invalid League ID: enter a 1–20 digit League ID or paste your ESPN/Sleeper league URL.")


def validate_sleeper_username(username: str) -> str:
    """Validates Sleeper usernames strictly (`1–32` ASCII letters, digits, or `_`, optional leading `@`)."""
    val = str(username or "").strip()
    if val.startswith("@"):
        val = val[1:].strip()
    if not SLEEPER_USERNAME_RE.match(val):
        raise ValueError("Invalid Sleeper username: must be 1–32 letters, numbers, or underscores.")
    return val


def validate_slug(slug: str) -> str:
    """Validates URL/GCS subpath slugs strictly to prevent path traversal (`../`)."""
    val = str(slug or "").strip().lower()
    if not SLUG_RE.match(val):
        raise ValueError(
            f"Invalid slug '{val}': must be 2–32 chars of lowercase letters, digits, '-' or '_'."
        )
    return val


def validate_webhook_url(url: str) -> str:
    """Strict HTTPS allowlist for outbound league chat webhooks (OWASP A10 SSRF protection)."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use https://")
    host = (parsed.hostname or "").lower()
    prefixes = ALLOWED_WEBHOOK_HOSTS.get(host)
    if not prefixes or not any(parsed.path.startswith(p) for p in prefixes):
        raise ValueError(f"Webhook host/path not in allowlist: {host}")
    return raw


def safe_http_url(url: str) -> str:
    """Returns URL only if it uses http:// or https:// (blocks javascript:, data:, file:)."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    return ""


def generate_unguessable_slug(base_slug: str) -> str:
    """Generates a private-by-link slug with 32 bits of cryptographic entropy (e.g. `jff-9f2a1c4b`)."""
    clean = re.sub(r"[^a-z0-9]+", "-", (base_slug or "league").lower()).strip("-")[:18] or "league"
    return f"{clean}-{secrets.token_hex(4)}"


class SlidingWindowRateLimiter:
    """
    Thread-safe, memory-bounded per-key sliding-window rate limiter (OWASP A04 DoS & Wallet-Exhaustion Guard).
    Evicts oldest keys via `OrderedDict` (`max_keys`) so spoofed-IP floods can never grow memory unboundedly.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = float(window_seconds)
        self.max_keys = max(64, int(max_keys))
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: "OrderedDict[str, list[float]]" = OrderedDict()

    def allow(self, key: str) -> Tuple[bool, int]:
        """Returns `(allowed, retry_after_seconds)`."""
        now = self._clock()
        cutoff = now - self.window_seconds
        k = str(key or "unknown")[:128]
        with self._lock:
            bucket = self._hits.get(k)
            if bucket is not None:
                bucket = [ts for ts in bucket if ts > cutoff]
                self._hits.move_to_end(k)
            else:
                bucket = []
                if len(self._hits) >= self.max_keys:
                    self._hits.popitem(last=False)
            if len(bucket) >= self.max_requests:
                self._hits[k] = bucket
                retry_after = max(1, int(round(bucket[0] + self.window_seconds - now)))
                return False, retry_after
            bucket.append(now)
            self._hits[k] = bucket
            return True, 0
