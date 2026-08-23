#!/usr/bin/env python3
"""
BFL SportsCenter Video Reel Generator
=====================================
Generates Full HD 1080p (1920x1080) broadcast visual cards and compiles
them with topic-synced audio into a dynamic MP4 video show with rapid transitions.
"""

import os
import re
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def strip_emojis(text: str) -> str:
    """Removes emoji characters that cause missing glyph boxes in PIL TrueType rendering."""
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff]|"
        "[\uD800-\uDBFF][\uDC00-\uDFFF]|"
        "[\u2600-\u27BF]|"
        "[\u2300-\u23FF]|"
        "[\u2B50-\u2B55]|"
        "[\u203C-\u2049]|"
        "[\u25A0-\u25FF]",
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text).strip()

def create_slide_card(title: str, subtitle: str, content_items: list, output_path: str, badge_text: str = "SPORTSCENTER HIGHLIGHT", accent_color=(231, 76, 60)):
    """Creates a broadcast-quality Full HD 1920x1080 visual slide card."""
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), color=(12, 16, 26))
    draw = ImageDraw.Draw(img)

    # Fonts
    try:
        font_badge = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
        font_title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Impact.ttf", 60)
        font_sub = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
        font_item_tag = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
        font_item_header = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 32)
        font_item_desc = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
        font_footer = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 20)
    except:
        font_badge = font_title = font_sub = font_item_tag = font_item_header = font_item_desc = font_footer = ImageFont.load_default()

    # Top Header Background
    draw.rectangle([(0, 0), (width, 160)], fill=(18, 24, 38))
    draw.rectangle([(0, 154), (width, 160)], fill=accent_color)

    # Category Pill Badge
    clean_badge = strip_emojis(badge_text).upper()
    badge_bbox = draw.textbbox((0, 0), clean_badge, font=font_badge)
    badge_w = badge_bbox[2] - badge_bbox[0]
    draw.rounded_rectangle([(70, 22), (70 + badge_w + 28, 56)], radius=6, fill=accent_color)
    draw.text((84, 27), clean_badge, fill=(255, 255, 255), font=font_badge)

    # Title & Subtitle
    clean_title = strip_emojis(title).upper()
    draw.text((70, 72), clean_title, fill=(255, 255, 255), font=font_title)

    clean_sub = strip_emojis(subtitle)
    sub_bbox = draw.textbbox((0, 0), clean_sub, font=font_sub)
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text((width - 70 - sub_w, 95), clean_sub, fill=(160, 175, 200), font=font_sub)

    # Main Card Container Box
    draw.rounded_rectangle([(70, 190), (width - 70, height - 70)], radius=12, fill=(18, 24, 38), outline=(32, 44, 68), width=2)

    # Render Content Items
    y = 225
    for item in content_items:
        raw_tag = strip_emojis(item.get('tag', '')).upper()
        raw_header = strip_emojis(item.get('header', ''))
        raw_desc = strip_emojis(item.get('desc', ''))

        # Background card row
        draw.rounded_rectangle([(95, y), (width - 95, y + 105)], radius=8, fill=(25, 33, 52))

        # Tag Badge inside row
        x_cursor = 115
        if raw_tag:
            tag_bbox = draw.textbbox((0, 0), raw_tag, font=font_item_tag)
            tag_w = tag_bbox[2] - tag_bbox[0]
            draw.rounded_rectangle([(x_cursor, y + 16), (x_cursor + tag_w + 18, y + 46)], radius=6, fill=(35, 95, 160))
            draw.text((x_cursor + 9, y + 21), raw_tag, fill=(255, 255, 255), font=font_item_tag)
            x_cursor += tag_w + 35

        # Header (Team Name & Owner Name)
        draw.text((x_cursor, y + 15), raw_header, fill=(255, 255, 255), font=font_item_header)

        # Description / Narrative
        if raw_desc:
            draw.text((115, y + 60), raw_desc, fill=(175, 190, 210), font=font_item_desc)

        y += 125

    # Footer
    draw.text((95, height - 48), "BEASTS FOOTBALL LEAGUE (BFL) • TUESDAY MORNING HANGOVER BROADCAST", fill=(100, 115, 140), font=font_footer)

    img.save(output_path)
    return output_path

def generate_topic_synced_video(scene_slides: list, scene_durations: list, audio_path: str, output_mp4_path: str, pid: int = 0):
    """
    Compiles a dynamic MP4 video where slides transition synchronously with each spoken scene/topic.
    """
    if len(scene_slides) != len(scene_durations) or not os.path.exists(audio_path):
        print("❌ Slide count does not match duration count or missing audio.")
        return ""

    total_dur = sum(scene_durations)
    print(f"🎬 Compiling {len(scene_slides)} Topic-Synced Scenes ({total_dur:.1f}s total runtime)...")

    temp_dir = Path(__file__).resolve().parent / f"temp_video_sync_{pid}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    concat_file = temp_dir / "img_sync_concat.txt"
    with open(concat_file, 'w') as f:
        for img_p, dur in zip(scene_slides, scene_durations):
            f.write(f"file '{img_p}'\n")
            f.write(f"duration {max(dur, 1.0):.2f}\n")
        f.write(f"file '{scene_slides[-1]}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "5",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        output_mp4_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if concat_file.exists(): concat_file.unlink()
    try: temp_dir.rmdir()
    except: pass

    print(f"🎉 Master Topic-Synced MP4 Video Reel Created: {output_mp4_path}")
    return output_mp4_path
