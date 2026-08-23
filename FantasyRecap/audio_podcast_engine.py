#!/usr/bin/env python3
"""
BFL Audio Podcast Synthesis Engine
==================================
Converts the 2-host sports talk dialogue into a broadcast-quality MP3 podcast:
- [COMMISH]: Synthesized with ChristopherNeural (Anchor voice)
- [COLOR COMMENTATOR]: Synthesized with GuyNeural (Color analyst voice)
- [REPORTER]: Synthesized with EricNeural
- Stitches multi-speaker audio segments into a seamless MP3 file
"""

import os
import sys
import re
import asyncio
import edge_tts
from pathlib import Path
import subprocess

VOICE_MAP = {
    'COMMISH': 'en-US-ChristopherNeural',
    'COLOR COMMENTATOR': 'en-US-GuyNeural',
    'REPORTER': 'en-US-EricNeural',
    'DEFAULT': 'en-US-ChristopherNeural'
}

def parse_dialogue_segments(script_text: str) -> list:
    """Parses markdown script into speaker segments."""
    segments = []
    lines = script_text.split('\n')
    current_speaker = 'COMMISH'
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('---') or line.startswith('|'):
            continue
            
        # Check sound effect cues
        if line.startswith('[SFX:'):
            continue
            
        # Match speaker patterns
        commish_match = re.match(r'^\*{0,2}\[COMMISH\]\*{0,2}:?\s*(.*)', line, re.IGNORECASE)
        color_match = re.match(r'^\*{0,2}\[COLOR COMMENTATOR\]\*{0,2}:?\s*(.*)', line, re.IGNORECASE)
        reporter_match = re.match(r'^\*{0,2}\[REPORTER\]\*{0,2}:?\s*(.*)', line, re.IGNORECASE)
        quote_match = re.match(r'^💬\s*([A-Za-z\s]+):?\s*["“](.*)["”]', line)
        
        if commish_match:
            current_speaker = 'COMMISH'
            text = commish_match.group(1).strip()
            if text: segments.append((current_speaker, text))
        elif color_match:
            current_speaker = 'COLOR COMMENTATOR'
            text = color_match.group(1).strip()
            if text: segments.append((current_speaker, text))
        elif reporter_match:
            current_speaker = 'REPORTER'
            text = reporter_match.group(1).strip()
            if text: segments.append((current_speaker, text))
        elif quote_match:
            speaker_name = quote_match.group(1).strip()
            text = f"{speaker_name} said: {quote_match.group(2).strip()}"
            segments.append(('REPORTER', text))
        elif line.startswith('* ') or line.startswith('• '):
            text = line.lstrip('*• ').replace('**', '').replace('`', '')
            segments.append((current_speaker, text))
            
    return segments

async def synthesize_segment(text: str, voice: str, out_path: str):
    """Synthesizes text using edge-tts."""
    comm = edge_tts.Communicate(text, voice)
    await comm.save(out_path)

async def render_podcast_mp3(script_text: str, output_mp3_path: str = "bfl_podcast_latest.mp3") -> str:
    """Renders entire script into a single stitched MP3 podcast file via ffmpeg."""
    segments = parse_dialogue_segments(script_text)
    temp_dir = Path(__file__).resolve().parent / "temp_audio"
    temp_dir.mkdir(exist_ok=True)
    
    print(f"🎙️ Synthesizing {len(segments)} audio dialogue segments...")
    audio_files = []
    
    for idx, (speaker, text) in enumerate(segments):
        clean_text = text.replace('*', '').replace('`', '').replace('#', '').strip()
        if not clean_text:
            continue
            
        voice = VOICE_MAP.get(speaker, VOICE_MAP['DEFAULT'])
        seg_file = str(temp_dir / f"seg_{idx:03d}_{speaker}.mp3")
        try:
            await synthesize_segment(clean_text, voice, seg_file)
            audio_files.append(seg_file)
        except Exception as e:
            print(f"⚠️ Error synthesizing segment {idx}: {e}")
            
    if not audio_files:
        print("❌ No audio segments generated.")
        return ""
        
    print(f"🎵 Stitching {len(audio_files)} segments into master podcast MP3 via ffmpeg...")
    concat_list_file = temp_dir / "concat_list.txt"
    with open(concat_list_file, 'w') as f:
        for a_file in audio_files:
            f.write(f"file '{a_file}'\n")
            
    # Run ffmpeg to concat
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy", output_mp3_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(f"🎉 Master Podcast Audio saved to: {output_mp3_path}")
    
    # Cleanup temp files
    for f in temp_dir.glob("*.mp3"):
        try: f.unlink()
        except: pass
    if concat_list_file.exists():
        concat_list_file.unlink()
    try: temp_dir.rmdir()
    except: pass
    
    return output_mp3_path

if __name__ == "__main__":
    test_script = """
    [COMMISH]: Welcome to BFL Sunday Night Prime! I'm your Commissioner, alongside our lead analyst.
    [COLOR COMMENTATOR]: What a wild week of fantasy football! We had heartbreaks, blowouts, and terrible bench decisions.
    [COMMISH]: Let's start with our Game of the Week.
    """
    asyncio.run(render_podcast_mp3(test_script, "test_podcast.mp3"))
