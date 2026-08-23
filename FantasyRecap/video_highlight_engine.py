#!/usr/bin/env python3
"""
BFL Video Highlight & SportsCenter Visual Reel Generator
========================================================
Generates broadcast-quality Full HD (1920x1080) visual slide decks
and compiles them with the podcast audio into a real MP4 video review show.
"""

import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def create_slide_image(title: str, subtitle: str, bullet_points: list, output_path: str, header_color=(41, 128, 185)):
    """Creates a sleek, 1920x1080 broadcast graphic card."""
    width, height = 1920, 1080
    img = Image.new('RGB', (width, height), color=(18, 22, 34)) # Dark modern theme
    draw = ImageDraw.Draw(img)
    
    # Top banner bar
    draw.rectangle([(0, 0), (width, 140)], fill=header_color)
    
    # Try loading default/truetype fonts
    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
        font_sub = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
        font_body = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 38)
        font_footer = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except:
        font_title = font_sub = font_body = font_footer = ImageFont.load_default()

    # Draw Title & Subtitle in top banner
    draw.text((60, 25), title, fill=(255, 255, 255), font=font_title)
    draw.text((60, 95), subtitle, fill=(236, 240, 241), font=font_sub)
    
    # Draw content box
    draw.rectangle([(60, 180), (width - 60, height - 80)], fill=(28, 34, 52), outline=(52, 73, 94), width=2)
    
    # Draw bullet points
    y = 230
    for bp in bullet_points:
        # Draw accent icon bar
        draw.rectangle([(90, y + 5), (96, y + 42)], fill=header_color)
        draw.text((120, y), bp, fill=(245, 245, 245), font=font_body)
        y += 75
        
    # Footer
    draw.text((80, height - 60), "BEASTS FOOTBALL LEAGUE • SUNDAY NIGHT PRIME BROADCAST", fill=(127, 140, 141), font=font_footer)
    img.save(output_path)
    return output_path

def generate_video_from_slides_and_audio(slide_images: list, audio_path: str, output_mp4_path: str = "bfl_show_latest.mp4"):
    """
    Combines slide images and podcast MP3 into an MP4 video using ffmpeg.
    """
    if not slide_images or not os.path.exists(audio_path):
        print("❌ Missing slide images or audio file.")
        return ""
        
    # Get audio duration via ffprobe
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
    res = subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True)
    try:
        duration = float(res.stdout.strip())
    except:
        duration = 60.0
        
    per_slide_duration = duration / len(slide_images)
    print(f"🎬 Creating {len(slide_images)}-slide video ({duration:.1f}s total, {per_slide_duration:.1f}s per slide)...")
    
    temp_dir = Path(__file__).resolve().parent / "temp_video"
    temp_dir.mkdir(exist_ok=True)
    
    # Create concat file for images
    concat_img_file = temp_dir / "img_concat.txt"
    with open(concat_img_file, 'w') as f:
        for img_p in slide_images:
            f.write(f"file '{img_p}'\n")
            f.write(f"duration {per_slide_duration:.2f}\n")
        # Final image repeat for ffmpeg concat quirk
        f.write(f"file '{slide_images[-1]}'\n")
        
    # Run ffmpeg to combine image slides + audio into MP4
    cmd_ffmpeg = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_img_file),
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        output_mp4_path
    ]
    subprocess.run(cmd_ffmpeg, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(f"🎉 Master Video Broadcast saved to: {output_mp4_path}")
    
    # Cleanup temp
    if concat_img_file.exists(): concat_img_file.unlink()
    try: temp_dir.rmdir()
    except: pass
    
    return output_mp4_path

if __name__ == "__main__":
    print("Testing BFL Slide & Video Generation...")
    s1 = create_slide_image("🏈 BFL SUNDAY NIGHT PRIME", "Week 1 Review & Highlights", [
        "🔥 Game of the Week: Adam Olen def. Emelie Lovasko (91.32 - 88.28)",
        "🔨 Beatdown of the Week: Sydney Miller def. Shawn Lukose (+31.16 margin)",
        "⚔️ North Division Clash: Nick Christus def. Dino Davros (100.82 - 85.48)",
        "🌴 South Division Battle: Abe Thomas def. Saagar Gupta (117.86 - 93.42)"
    ], "test_slide_1.png")
    print(f"✅ Slide created: {s1}")
