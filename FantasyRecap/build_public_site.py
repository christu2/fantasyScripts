#!/usr/bin/env python3
"""
Builds the Zero-PII, Zero-Podcast Static Public Bundle (`dist_public/`)
=======================================================================
Outputs a self-contained static directory ready to deploy onto **Cloudflare Pages**
Free Tier (unmetered `$0.00` static bandwidth + automatic edge WAF):

  - `index.html`        (with optional `--api-base` injected into `<meta name="frecap-api-base">` & CSP)
  - `styles.css`
  - `app.js`
  - `demo_hub_data.json` (100% anonymized 16-team 18-year BFL showcase, 0 podcast references)
  - `_headers`          (Cloudflare Pages OWASP security & caching headers)

Strictly excludes personal `hub_data.json` and `leagues/` subdirectories.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for p in (str(PROJECT_ROOT), str(SCRIPT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from FantasyRecap.anonymize_bfl_demo import build_anonymized_demo
    from FantasyRecap.core.security import safe_http_url
except ImportError:
    from anonymize_bfl_demo import build_anonymized_demo  # type: ignore
    from core.security import safe_http_url  # type: ignore

WEB_HUB_DIR = SCRIPT_DIR / "web_hub"
DEFAULT_OUT_DIR = SCRIPT_DIR / "dist_public"


def build_public_bundle(out_dir: Path = DEFAULT_OUT_DIR, api_base: str = "") -> Path:
    clean_api = ""
    extra_csp_origin = ""
    if api_base.strip():
        clean_api = safe_http_url(api_base.strip().rstrip("/"))
        if not clean_api:
            raise ValueError(f"Invalid --api-base URL: {api_base}")
        p = urlparse(clean_api)
        extra_csp_origin = f" {p.scheme}://{p.netloc}"

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build fresh zero-PII anonymized demo_hub_data.json
    demo_src = WEB_HUB_DIR / "hub_data.json"
    demo_out = out_dir / "demo_hub_data.json"
    if demo_src.exists():
        build_anonymized_demo(src_path=demo_src, out_path=demo_out)
        # Also keep web_hub/demo_hub_data.json updated for local dev & tests
        shutil.copyfile(demo_out, WEB_HUB_DIR / "demo_hub_data.json")
    elif (WEB_HUB_DIR / "demo_hub_data.json").exists():
        shutil.copyfile(WEB_HUB_DIR / "demo_hub_data.json", demo_out)
    else:
        raise FileNotFoundError("Neither web_hub/hub_data.json nor web_hub/demo_hub_data.json found.")

    # 2. Render index.html with injected API base, demo default bundle & strict CSP
    html_txt = (WEB_HUB_DIR / "index.html").read_text(encoding="utf-8")
    html_txt = html_txt.replace(
        '<meta name="frecap-default-bundle" content="hub_data.json" />',
        '<meta name="frecap-default-bundle" content="demo_hub_data.json" />',
        1,
    )
    if clean_api:
        html_txt = html_txt.replace(
            '<meta name="frecap-api-base" content="" />',
            f'<meta name="frecap-api-base" content="{clean_api}" />',
            1,
        )
        if extra_csp_origin and extra_csp_origin.strip() not in html_txt:
            html_txt = html_txt.replace(
                "connect-src 'self'",
                f"connect-src 'self'{extra_csp_origin}",
                1,
            )
    (out_dir / "index.html").write_text(html_txt, encoding="utf-8")

    # 3. Copy styles.css and app.js ONLY (never personal hub_data.json!)
    shutil.copyfile(WEB_HUB_DIR / "styles.css", out_dir / "styles.css")
    shutil.copyfile(WEB_HUB_DIR / "app.js", out_dir / "app.js")

    # 4. Write Cloudflare Pages `_headers` file (OWASP A05 hardening + smart caching)
    cf_csp = (
        f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: https:; media-src 'self' https:; "
        f"connect-src 'self'{extra_csp_origin} https://api.sleeper.app https://storage.googleapis.com https://*.run.app; "
        f"object-src 'none'; base-uri 'self'; frame-ancestors 'none';"
    )
    headers_txt = f"""/*
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Content-Security-Policy: {cf_csp}

/index.html
  Cache-Control: public, max-age=60, s-maxage=300

/app.js
  Cache-Control: public, max-age=120, s-maxage=600

/styles.css
  Cache-Control: public, max-age=300, s-maxage=1800

/demo_hub_data.json
  Cache-Control: public, max-age=900, s-maxage=86400
"""
    (out_dir / "_headers").write_text(headers_txt, encoding="utf-8")

    # Final safety check: assert personal hub_data.json does NOT exist in dist_public
    assert not (out_dir / "hub_data.json").exists(), "Personal hub_data.json must never be copied to dist_public!"
    raw_idx = (out_dir / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"Beasts Football|Jabroni|Hangover", raw_idx, re.IGNORECASE)

    print(f"📦 Built clean public Cloudflare Pages bundle at: {out_dir}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build zero-PII public static bundle for Cloudflare Pages")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--api-base", type=str, default="", help="Optional Cloud Run / Worker API base URL")
    args = parser.parse_args()
    build_public_bundle(out_dir=args.out_dir, api_base=args.api_base)


if __name__ == "__main__":
    main()
