#!/usr/bin/env python3
"""
BFL SportsCenter Studio Video Reel Generator
============================================
Generates Full HD 1080p (1920x1080) television broadcast frames featuring:
- Top live broadcast network bar & category badge
- Left Studio Desk with illustrated anchors (Chris & Dave) + active "ON AIR" speaker indicator
- Main Broadcast Big Screen showing active Fantasy Team Names, Owner Names & Stat Lines
- Bottom TV Ticker running real scores and headlines
- Topic-synced slide transitions syncing with spoken dialogue segments
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

def render_tv_studio_frame(
    title: str,
    subtitle: str,
    category_badge: str,
    items: list,
    speaker: str, # 'CHRIS' or 'DAVE'
    output_path: str,
    accent_color=(231, 76, 60),
    ticker_text: str = "•  ABE DEFENDS JABRONI TROPHY (117.86)  •  JOSH ALLEN 394 YDS, 4 TDS (38.8 PTS)  •  SYDNEY DEMOLISHES LUKOSE (101-70)  •  ADAM ESCAPES EMELIE (91-88)"
):
    """Creates a broadcast-quality Full HD 1920x1080 TV Studio frame with live anchor switching."""
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), color=(10, 14, 24))
    draw = ImageDraw.Draw(img)

    # Fonts
    try:
        font_live = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
        font_net = ImageFont.truetype("/System/Library/Fonts/Supplemental/Impact.ttf", 36)
        font_title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Impact.ttf", 52)
        font_sub = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 26)
        font_item_tag = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
        font_item_header = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 28)
        font_item_desc = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
        font_host_name = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
        font_ticker = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
    except:
        font_live = font_net = font_title = font_sub = font_item_tag = font_item_header = font_item_desc = font_host_name = font_ticker = ImageFont.load_default()

    # --- TOP TV NETWORK BAR ---
    draw.rectangle([(0, 0), (width, 100)], fill=(16, 22, 36))
    draw.rectangle([(0, 96), (width, 100)], fill=accent_color)

    # Live Badge
    draw.rounded_rectangle([(40, 24), (130, 66)], radius=6, fill=(231, 76, 60))
    draw.text((55, 33), "LIVE", fill=(255, 255, 255), font=font_live)

    # Network Header
    draw.text((150, 28), "BFL BROADCAST NETWORK", fill=(255, 255, 255), font=font_net)
    draw.text((150 + 440, 36), "•  TUESDAY MORNING HANGOVER", fill=(180, 195, 220), font=font_sub)

    # Category Pill (Top Right)
    clean_badge = strip_emojis(category_badge).upper()
    badge_bbox = draw.textbbox((0, 0), clean_badge, font=font_live)
    badge_w = badge_bbox[2] - badge_bbox[0]
    draw.rounded_rectangle([(width - 60 - badge_w - 30, 26), (width - 60, 68)], radius=6, fill=accent_color)
    draw.text((width - 60 - badge_w - 15, 34), clean_badge, fill=(255, 255, 255), font=font_live)

    # --- LEFT DESK: HOST ANCHORS ---
    draw.rounded_rectangle([(40, 125), (460, height - 90)], radius=12, fill=(16, 22, 36), outline=(28, 38, 58), width=2)
    draw.text((60, 145), "STUDIO DESK", fill=(140, 160, 190), font=font_live)

    # Chris Anchor Box
    chris_active = (speaker.upper() == 'CHRIS')
    chris_border = (46, 204, 113) if chris_active else (40, 52, 75)
    chris_bg = (24, 32, 50) if chris_active else (18, 24, 38)
    draw.rounded_rectangle([(60, 180), (440, 500)], radius=10, fill=chris_bg, outline=chris_border, width=3 if chris_active else 1)

    assets_dir = Path(__file__).resolve().parent / "assets"
    chris_path = assets_dir / "anchor_chris.jpg"
    if chris_path.exists():
        try:
            chris_img = Image.open(chris_path).convert('RGB').resize((180, 180))
            img.paste(chris_img, (80, 200))
        except:
            draw.rectangle([(80, 200), (260, 380)], fill=(30, 45, 70))
    else:
        draw.rectangle([(80, 200), (260, 380)], fill=(30, 45, 70))

    draw.text((80, 400), "CHRIS", fill=(255, 255, 255), font=font_item_header)
    draw.text((80, 435), "Lead Anchor", fill=(160, 175, 200), font=font_host_name)
    if chris_active:
        draw.rounded_rectangle([(80, 460), (220, 490)], radius=4, fill=(46, 204, 113))
        draw.text((92, 465), "ON AIR", fill=(0, 0, 0), font=font_host_name)

    # Dave Anchor Box
    dave_active = (speaker.upper() == 'DAVE')
    dave_border = (231, 76, 60) if dave_active else (40, 52, 75)
    dave_bg = (24, 32, 50) if dave_active else (18, 24, 38)
    draw.rounded_rectangle([(60, 530), (440, 850)], radius=10, fill=dave_bg, outline=dave_border, width=3 if dave_active else 1)

    dave_path = assets_dir / "anchor_dave.jpg"
    if dave_path.exists():
        try:
            dave_img = Image.open(dave_path).convert('RGB').resize((180, 180))
            img.paste(dave_img, (80, 550))
        except:
            draw.rectangle([(80, 550), (260, 730)], fill=(30, 45, 70))
    else:
        draw.rectangle([(80, 550), (260, 730)], fill=(30, 45, 70))

    draw.text((80, 750), "DAVE", fill=(255, 255, 255), font=font_item_header)
    draw.text((80, 785), "Color Analyst & Roasts", fill=(160, 175, 200), font=font_host_name)
    if dave_active:
        draw.rounded_rectangle([(80, 810), (220, 840)], radius=4, fill=(231, 76, 60))
        draw.text((92, 815), "ON AIR", fill=(255, 255, 255), font=font_host_name)

    # --- RIGHT PANEL: MAIN BROADCAST SCREEN ---
    main_x1, main_y1 = 490, 125
    main_x2, main_y2 = width - 40, height - 90
    draw.rounded_rectangle([(main_x1, main_y1), (main_x2, main_y2)], radius=12, fill=(16, 22, 36), outline=(28, 38, 58), width=2)

    # Screen Header Bar
    clean_title = strip_emojis(title).upper()
    draw.text((main_x1 + 35, main_y1 + 25), clean_title, fill=(255, 255, 255), font=font_title)

    clean_sub = strip_emojis(subtitle)
    draw.text((main_x1 + 35, main_y1 + 85), clean_sub, fill=(170, 185, 210), font=font_sub)
    draw.line([(main_x1 + 35, main_y1 + 125), (main_x2 - 35, main_y1 + 125)], fill=(32, 44, 68), width=2)

    # Screen Content Item Cards
    y_card = main_y1 + 145
    for item in items:
        raw_tag = strip_emojis(item.get('tag', '')).upper()
        raw_header = strip_emojis(item.get('header', ''))
        raw_desc = strip_emojis(item.get('desc', ''))

        card_h = 95
        draw.rounded_rectangle([(main_x1 + 35, y_card), (main_x2 - 35, y_card + card_h)], radius=8, fill=(22, 30, 48), outline=(32, 44, 68), width=1)

        x_cursor = main_x1 + 55
        if raw_tag:
            tag_bbox = draw.textbbox((0, 0), raw_tag, font=font_item_tag)
            tag_w = tag_bbox[2] - tag_bbox[0]
            draw.rounded_rectangle([(x_cursor, y_card + 14), (x_cursor + tag_w + 18, y_card + 42)], radius=4, fill=(35, 95, 160))
            draw.text((x_cursor + 9, y_card + 18), raw_tag, fill=(255, 255, 255), font=font_item_tag)
            x_cursor += tag_w + 32

        draw.text((x_cursor, y_card + 14), raw_header, fill=(255, 255, 255), font=font_item_header)

        if raw_desc:
            draw.text((main_x1 + 55, y_card + 54), raw_desc, fill=(160, 175, 200), font=font_item_desc)

        y_card += 115

    # --- BOTTOM TICKER / BOTTOM-LINE CRAWL ---
    draw.rectangle([(0, height - 60), (width, height)], fill=(12, 16, 26))
    draw.rectangle([(0, height - 60), (180, height)], fill=(231, 76, 60))
    draw.text((25, height - 42), "BFL TICKER", fill=(255, 255, 255), font=font_ticker)
    draw.text((210, height - 42), ticker_text, fill=(200, 215, 235), font=font_ticker)

    img.save(output_path)
    return output_path

def generate_tv_studio_video(segment_frames: list, segment_durations: list, audio_path: str, output_mp4_path: str, pid: int = 0):
    """Compiles TV studio broadcast frames with active speaker switching into an MP4 video."""
    if len(segment_frames) != len(segment_durations) or not os.path.exists(audio_path):
        print("❌ Frame count does not match duration count or missing audio.")
        return ""

    total_dur = sum(segment_durations)
    print(f"🎬 Compiling {len(segment_frames)} Dynamic TV Studio Shots ({total_dur:.1f}s total runtime)...")

    temp_dir = Path(__file__).resolve().parent / f"temp_tv_sync_{pid}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    concat_file = temp_dir / "tv_sync_concat.txt"
    with open(concat_file, 'w') as f:
        for img_p, dur in zip(segment_frames, segment_durations):
            f.write(f"file '{img_p}'\n")
            f.write(f"duration {max(dur, 0.5):.2f}\n")
        f.write(f"file '{segment_frames[-1]}'\n")

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

    print(f"🎉 Master TV Studio Show Created: {output_mp4_path}")
    return output_mp4_path
