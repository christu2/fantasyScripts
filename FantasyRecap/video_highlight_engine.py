#!/usr/bin/env python3
"""
BFL SportsCenter Video Reel Generator
=====================================
Generates Full HD 1080p (1920x1080) broadcast visual cards and compiles
them with the podcast audio into a real MP4 video recap show.
"""

import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def create_slide_card(title: str, subtitle: str, content_items: list, output_path: str, badge_text: str = "SPORTSCENTER HIGHLIGHT", accent_color=(231, 76, 60)):
    """Creates a broadcast-quality Full HD 1920x1080 visual slide card."""
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), color=(15, 20, 30))
    draw = ImageDraw.Draw(img)

    # Top header bar
    draw.rectangle([(0, 0), (width, 160)], fill=(22, 29, 44))
    draw.rectangle([(0, 155), (width, 160)], fill=accent_color)

    # Fonts
    try:
        font_badge = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 60)
        font_sub = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
        font_card_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
        font_card_body = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
        font_footer = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except:
        font_badge = font_title = font_sub = font_card_title = font_card_body = font_footer = ImageFont.load_default()

    # Draw Badge
    draw.rectangle([(60, 25), (60 + len(badge_text)*14, 60)], fill=accent_color)
    draw.text((70, 30), badge_text, fill=(255, 255, 255), font=font_badge)

    # Draw Title & Subtitle
    draw.text((60, 70), title, fill=(255, 255, 255), font=font_title)
    draw.text((width - 600, 85), subtitle, fill=(189, 195, 199), font=font_sub)

    # Draw Content Box
    draw.rectangle([(60, 190), (width - 60, height - 80)], fill=(24, 32, 48), outline=(44, 62, 80), width=2)

    # Draw Content Items
    y = 230
    for item in content_items:
        header = item.get('header', '')
        desc = item.get('desc', '')
        tag = item.get('tag', '')

        # Accent icon
        draw.rectangle([(90, y + 5), (96, y + 45)], fill=accent_color)
        if tag:
            draw.rectangle([(110, y + 5), (110 + len(tag)*13, y + 38)], fill=(41, 128, 185))
            draw.text((118, y + 10), tag, fill=(255, 255, 255), font=font_card_body)
            draw.text((125 + len(tag)*13, y + 6), header, fill=(255, 255, 255), font=font_card_title)
        else:
            draw.text((115, y + 6), header, fill=(255, 255, 255), font=font_card_title)

        if desc:
            draw.text((115, y + 50), desc, fill=(189, 195, 199), font=font_card_body)
            y += 115
        else:
            y += 75

    # Footer
    draw.text((80, height - 55), "BEASTS FOOTBALL LEAGUE (BFL) • TUESDAY MORNING HANGOVER BROADCAST", fill=(127, 140, 141), font=font_footer)
    img.save(output_path)
    return output_path

def generate_video_from_audio(slide_images: list, audio_path: str, output_mp4_path: str = "bfl_show.mp4"):
    """Combines slide images with audio file using ffmpeg to produce an MP4 video."""
    if not slide_images or not os.path.exists(audio_path):
        print("❌ Missing slide images or audio file.")
        return ""

    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    try:
        duration = float(res.stdout.strip())
    except:
        duration = 60.0

    per_slide_duration = duration / len(slide_images)
    print(f"🎬 Compiling {len(slide_images)}-slide MP4 video ({duration:.1f}s total, {per_slide_duration:.1f}s per slide)...")

    temp_dir = Path(__file__).resolve().parent / "temp_video"
    temp_dir.mkdir(exist_ok=True)

    concat_file = temp_dir / "img_concat.txt"
    with open(concat_file, 'w') as f:
        for img_p in slide_images:
            f.write(f"file '{img_p}'\n")
            f.write(f"duration {per_slide_duration:.2f}\n")
        f.write(f"file '{slide_images[-1]}'\n")

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

    print(f"🎉 Master MP4 Video Reel Created: {output_mp4_path}")
    return output_mp4_path
