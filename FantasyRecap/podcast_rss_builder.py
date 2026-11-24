#!/usr/bin/env python3
"""
BFL Private Podcast RSS 2.0 Feed Builder
========================================
Builds an Apple Podcasts / Spotify / Overcast compatible RSS 2.0 + iTunes XML
feed (`output/rss/feed.xml`) alongside high-quality 128 kbps MP3 files so league
members can subscribe once and get every Tuesday episode automatically on their
phone / CarPlay instead of listening to heavily compressed MP3s inside Discord.

Hosting options (all free or pennies/mo):
  - Cloudflare R2 (10 GB free, $0 egress) + public bucket URL
  - GitHub Pages (private/unlisted URL)
  - S3 / DigitalOcean Spaces / Mac Mini + Cloudflare Tunnel

Usage:
  python3 podcast_rss_builder.py --season 2026 --week 10 --audio path/to/file.mp3 --base-url https://pod.example.com
  python3 podcast_rss_builder.py --rebuild --base-url https://pod.example.com
"""

import argparse
import email.utils
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.dom import minidom
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from FantasyRecap.core.manifest import get_podcast_metadata, load_project_env
load_project_env()

RSS_DIR = SCRIPT_DIR / "output" / "rss"
EPISODES_DIR = RSS_DIR / "episodes"
CATALOG_PATH = RSS_DIR / "episodes.json"
FEED_PATH = RSS_DIR / "feed.xml"

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"


def _probe_duration_seconds(audio_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def _format_itunes_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _encode_hq_mp3(source_path: Path, dest_path: Path, bitrate: str = "128k") -> Path:
    """Encodes source audio to a clean stereo/mono 128 kbps MP3 for podcast players."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            str(dest_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and dest_path.exists():
            return dest_path
    # Fallback: direct copy if already MP3 or ffmpeg unavailable
    shutil.copy2(source_path, dest_path)
    return dest_path


def load_catalog(catalog_path: Path = CATALOG_PATH) -> list:
    if not catalog_path.exists():
        return []
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_catalog(episodes: list, catalog_path: Path = CATALOG_PATH) -> None:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(episodes, f, indent=2)


def build_rss_xml(
    episodes: list,
    base_url: str,
    *,
    metadata: Optional[dict] = None,
    artwork_url: str = "",
) -> str:
    """Generates a valid Apple Podcasts / Spotify RSS 2.0 XML document."""
    meta = metadata or get_podcast_metadata()
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"

    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("atom", ATOM_NS)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = meta.get("title", "BFL Tuesday Morning Hangover")
    ET.SubElement(channel, "link").text = clean_base
    ET.SubElement(channel, "description").text = meta.get(
        "description", "Weekly recap podcast for the Beasts Football League."
    )
    ET.SubElement(channel, "language").text = meta.get("language", "en-us")
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))

    atom_link = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_link.set("href", feed_url)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = meta.get("author", "BFL Studio Desk")
    ET.SubElement(channel, f"{{{ITUNES_NS}}}explicit").text = "true"
    ET.SubElement(channel, f"{{{ITUNES_NS}}}block").text = "yes"  # Keep unlisted from public directories

    cat = ET.SubElement(channel, f"{{{ITUNES_NS}}}category", {"text": meta.get("category", "Sports")})
    if meta.get("subcategory"):
        ET.SubElement(cat, f"{{{ITUNES_NS}}}category", {"text": meta["subcategory"]})

    art = artwork_url or os.getenv("PODCAST_ARTWORK_URL", "").strip()
    if art:
        ET.SubElement(channel, f"{{{ITUNES_NS}}}image", {"href": art})

    # Newest episodes first
    ordered = sorted(
        episodes,
        key=lambda e: (e.get("season", 0), e.get("week", 0), e.get("pub_epoch", 0)),
        reverse=True,
    )

    for ep in ordered:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep.get("description", "")
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = ep["guid"]
        ET.SubElement(item, "pubDate").text = ep["pub_date_rfc2822"]

        ep_url = f"{clean_base}/episodes/{ep['filename']}"
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": ep_url,
                "length": str(ep.get("size_bytes", 0)),
                "type": "audio/mpeg",
            },
        )
        ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = _format_itunes_duration(ep.get("duration_sec", 0))
        ET.SubElement(item, f"{{{ITUNES_NS}}}season").text = str(ep.get("season", 2026))
        ET.SubElement(item, f"{{{ITUNES_NS}}}episode").text = str(ep.get("week", 1))
        ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "true"

    raw_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    pretty = minidom.parseString(raw_bytes).toprettyxml(indent="  ", encoding="utf-8")
    return pretty.decode("utf-8")


def _get_gcs_access_token(credentials_path: str = "") -> str:
    """Obtains a short-lived OAuth2 bearer token for GCS using a Service Account JSON key (uses std google-auth)."""
    cred_file = (
        credentials_path
        or os.getenv("GCS_CREDENTIALS_PATH", "").strip()
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )
    if not cred_file or not os.path.exists(cred_file):
        raise FileNotFoundError(
            "GCS credentials JSON not found. Set `GCS_CREDENTIALS_PATH=/path/to/service-account.json` in `.env`."
        )
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds = service_account.Credentials.from_service_account_file(
        cred_file,
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


def upload_file_to_gcs(
    local_path: Path,
    bucket: str,
    object_name: str,
    *,
    content_type: str,
    cache_control: str = "public, max-age=3600",
    credentials_path: str = "",
) -> str:
    """
    Uploads a local file to Google Cloud Storage via the JSON REST API (zero extra SDKs needed)
    and returns its public HTTPS URL.
    """
    token = _get_gcs_access_token(credentials_path)
    encoded_name = urllib.parse.quote(object_name, safe="")
    upload_url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=media&name={encoded_name}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "Cache-Control": cache_control,
    }
    with open(local_path, "rb") as fp:
        resp = requests.post(upload_url, headers=headers, data=fp, timeout=180)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GCS upload failed for '{object_name}' (HTTP {resp.status_code}): {resp.text[:300]}")

    # Explicitly patch object cacheControl metadata so Apple Podcasts respects no-cache on feed.xml
    meta_url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encoded_name}"
    requests.patch(
        meta_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"cacheControl": cache_control, "contentType": content_type},
        timeout=15,
    )
    return f"https://storage.googleapis.com/{bucket}/{object_name}"


def sync_rss_to_gcs(
    rss_dir: Path,
    bucket: str,
    *,
    mp3_filename: str = "",
    credentials_path: str = "",
) -> dict:
    """Uploads the new MP3 (if specified) and updated `feed.xml` to a Google Cloud Storage bucket."""
    uploaded = {}
    if mp3_filename:
        mp3_local = rss_dir / "episodes" / mp3_filename
        if mp3_local.exists():
            uploaded["episode_url"] = upload_file_to_gcs(
                mp3_local,
                bucket,
                f"episodes/{mp3_filename}",
                content_type="audio/mpeg",
                cache_control="public, max-age=31536000",
                credentials_path=credentials_path,
            )

    feed_local = rss_dir / "feed.xml"
    if feed_local.exists():
        uploaded["feed_url"] = upload_file_to_gcs(
            feed_local,
            bucket,
            "feed.xml",
            content_type="application/rss+xml; charset=utf-8",
            cache_control="no-cache, max-age=60",
            credentials_path=credentials_path,
        )
    return uploaded


def add_episode_to_rss(
    season: int,
    week: int,
    audio_path: str,
    *,
    title: str = "",
    notes: str = "",
    episode_type: str = "notebooklm",
    base_url: str = "",
    gcs_bucket: str = "",
    bitrate: str = "128k",
    rss_dir: Path = RSS_DIR,
) -> dict:
    """
    Encodes a high-quality MP3 into `rss_dir/episodes/`, updates `episodes.json`,
    and writes `feed.xml`. If `gcs_bucket` or `GCS_PODCAST_BUCKET` is configured,
    automatically uploads both the MP3 and `feed.xml` to Google Cloud Storage.
    Idempotent per `(season, week, episode_type)`.
    """
    src = Path(audio_path)
    if not src.exists():
        raise FileNotFoundError(f"Source audio not found: {src}")

    eff_bucket = (gcs_bucket or os.getenv("GCS_PODCAST_BUCKET", "")).strip()
    eff_base = (
        base_url
        or os.getenv("PODCAST_RSS_BASE_URL", "").strip()
        or (f"https://storage.googleapis.com/{eff_bucket}" if eff_bucket else "")
        or "https://your-gcs-bucket-url.example.com"
    ).rstrip("/")
    ep_dir = rss_dir / "episodes"
    catalog_path = rss_dir / "episodes.json"
    feed_path = rss_dir / "feed.xml"

    filename = f"bfl_{episode_type}_s{season}_w{week:02d}.mp3"
    dest_mp3 = ep_dir / filename
    _encode_hq_mp3(src, dest_mp3, bitrate=bitrate)

    dur_sec = _probe_duration_seconds(dest_mp3)
    size_bytes = dest_mp3.stat().st_size
    now_utc = datetime.now(timezone.utc)
    guid = f"bfl-{episode_type}-{season}-week-{week}"

    ep_title = title or f"Week {week} ({season}) — BFL Tuesday Morning Hangover"
    ep_desc = notes or f"Beasts Football League Season {season} Week {week} recap, live swings, and playoff odds."

    catalog = load_catalog(catalog_path)
    catalog = [e for e in catalog if e.get("guid") != guid]
    entry = {
        "guid": guid,
        "season": int(season),
        "week": int(week),
        "episode_type": episode_type,
        "title": ep_title,
        "description": ep_desc,
        "filename": filename,
        "size_bytes": size_bytes,
        "duration_sec": round(dur_sec, 2),
        "pub_epoch": int(now_utc.timestamp()),
        "pub_date_rfc2822": email.utils.format_datetime(now_utc),
    }
    catalog.append(entry)
    save_catalog(catalog, catalog_path)

    xml_str = build_rss_xml(catalog, eff_base)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    feed_path.write_text(xml_str, encoding="utf-8")

    gcs_uploaded = False
    if eff_bucket:
        sync_rss_to_gcs(rss_dir, eff_bucket, mp3_filename=filename)
        gcs_uploaded = True

    return {
        "mp3_path": str(dest_mp3),
        "feed_path": str(feed_path),
        "episode_url": f"{eff_base}/episodes/{filename}",
        "feed_url": f"{eff_base}/feed.xml",
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "duration_fmt": _format_itunes_duration(dur_sec),
        "gcs_uploaded": gcs_uploaded,
    }


def main():
    parser = argparse.ArgumentParser(description="Build/Update BFL Private Podcast RSS Feed (with optional Google Cloud Storage upload)")
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--week", type=int, default=1)
    parser.add_argument("--audio", type=str, default="", help="Path to source MP3/M4A/WAV file")
    parser.add_argument("--title", type=str, default="", help="Optional custom episode title")
    parser.add_argument("--notes", type=str, default="", help="Optional episode description")
    parser.add_argument("--type", type=str, default="notebooklm", choices=["notebooklm", "gemini", "commissioner"])
    parser.add_argument("--base-url", type=str, default="", help="Public HTTPS base URL where output/rss/ is hosted")
    parser.add_argument("--gcs-bucket", type=str, default="", help="Google Cloud Storage bucket name for automatic upload")
    parser.add_argument("--bitrate", type=str, default="128k", help="Target HQ MP3 bitrate (default: 128k)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild feed.xml from existing episodes.json (and sync to GCS if configured)")
    args = parser.parse_args()

    eff_bucket = (args.gcs_bucket or os.getenv("GCS_PODCAST_BUCKET", "")).strip()
    eff_base = (
        args.base_url
        or os.getenv("PODCAST_RSS_BASE_URL", "").strip()
        or (f"https://storage.googleapis.com/{eff_bucket}" if eff_bucket else "")
        or "https://your-gcs-bucket-url.example.com"
    ).rstrip("/")

    if args.rebuild:
        catalog = load_catalog()
        xml_str = build_rss_xml(catalog, eff_base)
        FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        FEED_PATH.write_text(xml_str, encoding="utf-8")
        print(f"✅ Rebuilt {FEED_PATH} ({len(catalog)} episodes) for {eff_base}/feed.xml")
        if eff_bucket:
            sync_rss_to_gcs(RSS_DIR, eff_bucket)
            print(f"☁️  Synced feed.xml to Google Cloud Storage: https://storage.googleapis.com/{eff_bucket}/feed.xml")
        return

    if not args.audio:
        parser.error("--audio is required unless --rebuild is passed.")

    info = add_episode_to_rss(
        args.season,
        args.week,
        args.audio,
        title=args.title,
        notes=args.notes,
        episode_type=args.type,
        base_url=args.base_url,
        gcs_bucket=eff_bucket,
        bitrate=args.bitrate,
    )
    print(f"✅ Added HQ Episode to RSS: {info['mp3_path']} ({info['size_mb']} MB, {info['duration_fmt']})")
    print(f"📡 RSS Feed updated: {info['feed_path']}")
    if info["gcs_uploaded"]:
        print(f"☁️  Uploaded MP3 & feed.xml to Google Cloud Storage!")
    print(f"🔗 Hosted Feed URL:  {info['feed_url']}")


if __name__ == "__main__":
    main()
