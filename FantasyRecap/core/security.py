"""
OWASP Top 10 Input Validation, SSRF Guards & Multi-Tenant Token Helpers
=======================================================================
Shared security primitives used across multi-league ingestion, telemetry,
web hub deployment, and outbound webhooks.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import urlparse

LEAGUE_ID_RE = re.compile(r"^[0-9]{1,20}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

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
