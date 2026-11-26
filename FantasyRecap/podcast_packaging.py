#!/usr/bin/env python3
"""
BFL Podcast Show Packaging & Studio Audio Engine
================================================
Generates warm, broadcast-grade sports-talk show packaging (intro theme, act
transition bumpers, outro sting) using clean stereo synthesis — and automatically
supports custom user-supplied audio overrides in `FantasyRecap/assets/custom/`.

Custom drop-in files (optional):
  - FantasyRecap/assets/custom/intro_theme.(mp3|wav)
  - FantasyRecap/assets/custom/act_bumper.(mp3|wav)
  - FantasyRecap/assets/custom/outro_theme.(mp3|wav)
"""

from pathlib import Path
from typing import Dict, Optional
import math
import struct
import wave

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR / "assets"
CUSTOM_ASSETS_DIR = ASSETS_DIR / "custom"

SAMPLE_RATE = 44100


def _write_stereo_wav(stereo: np.ndarray, path: Path, sr: int = SAMPLE_RATE) -> Path:
    """Writes a float32 stereo array `shape=(N, 2)` in [-1, 1] to 16-bit PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(stereo, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def _rhodes_note(freq: float, dur: float, sr: int = SAMPLE_RATE, amp: float = 0.22, pan: float = 0.0) -> np.ndarray:
    """Warm Fender-Rhodes-style electric piano tone with gentle stereo spread."""
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr

    # Smooth attack + natural warm decay
    att = np.minimum(1.0, t / 0.014)
    env = att * np.exp(-t * 1.45)
    tine_env = att * np.exp(-t * 24.0)

    # Slight L/R detune for warm broadcast stereo width
    l_sig = (
        np.sin(2 * math.pi * (freq - 0.35) * t)
        + 0.20 * np.sin(2 * math.pi * 2.0 * (freq - 0.35) * t)
        + 0.05 * np.sin(2 * math.pi * 3.0 * freq * t)
    ) * env + 0.09 * np.sin(2 * math.pi * 6.0 * freq * t) * tine_env

    r_sig = (
        np.sin(2 * math.pi * (freq + 0.35) * t)
        + 0.20 * np.sin(2 * math.pi * 2.0 * (freq + 0.35) * t)
        + 0.05 * np.sin(2 * math.pi * 3.0 * freq * t)
    ) * env + 0.09 * np.sin(2 * math.pi * 6.0 * freq * t) * tine_env

    # Equal-power pan (-1 left .. +1 right)
    theta = (pan + 1.0) * math.pi / 4.0
    left = l_sig * amp * math.cos(theta)
    right = r_sig * amp * math.sin(theta)
    return np.stack([left, right], axis=1)


def _warm_bass_note(freq: float, dur: float, sr: int = SAMPLE_RATE, amp: float = 0.28) -> np.ndarray:
    """Punchy, warm sub-bass note for sports-desk groove."""
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr
    att = np.minimum(1.0, t / 0.008)
    env = att * np.exp(-t * 3.6)
    sig = np.tanh(1.5 * (np.sin(2 * math.pi * freq * t) + 0.35 * np.sin(2 * math.pi * 2.0 * freq * t))) * env * amp
    return np.stack([sig, sig], axis=1)


def _kick_hit(dur: float = 0.22, sr: int = SAMPLE_RATE, amp: float = 0.34) -> np.ndarray:
    """Tight, modern broadcast kick drum."""
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr
    # Pitch drop from 120 Hz -> 48 Hz
    phase = 2 * math.pi * (48.0 * t + (72.0 / 22.0) * (1.0 - np.exp(-22.0 * t)))
    env = np.minimum(1.0, t / 0.003) * np.exp(-t * 16.0)
    sig = np.tanh(1.6 * np.sin(phase)) * env * amp
    return np.stack([sig, sig], axis=1)


def _soft_hat(dur: float = 0.06, sr: int = SAMPLE_RATE, amp: float = 0.045, pan: float = 0.25, seed: int = 42) -> np.ndarray:
    """Crisp, subtle closed hi-hat tick."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr
    noise = rng.standard_normal(n)
    # Simple 2nd-order high-pass difference for airy sheen
    hp = np.diff(noise, n=2, prepend=[0.0, 0.0])
    env = np.minimum(1.0, t / 0.002) * np.exp(-t * 65.0)
    sig = hp * env * amp
    theta = (pan + 1.0) * math.pi / 4.0
    return np.stack([sig * math.cos(theta), sig * math.sin(theta)], axis=1)


def _add_at(buf: np.ndarray, clip: np.ndarray, start_sec: float, sr: int = SAMPLE_RATE) -> None:
    s = int(start_sec * sr)
    if s >= len(buf):
        return
    end = min(len(buf), s + len(clip))
    buf[s:end] += clip[: end - s]


def _apply_simple_reverb(stereo: np.ndarray, sr: int = SAMPLE_RATE, wet: float = 0.18) -> np.ndarray:
    """Adds subtle studio room ambience via multi-tap stereo delay."""
    out = stereo.copy()
    taps = [(0.029, 0.45), (0.047, 0.32), (0.073, 0.22), (0.111, 0.14)]
    for idx, (delay_s, decay) in enumerate(taps):
        d = int(delay_s * sr)
        if d < len(stereo):
            # Cross-channel taps for natural stereo width
            if idx % 2 == 0:
                out[d:, 0] += stereo[:-d, 1] * decay * wet
                out[d:, 1] += stereo[:-d, 0] * decay * wet
            else:
                out[d:, 0] += stereo[:-d, 0] * decay * wet
                out[d:, 1] += stereo[:-d, 1] * decay * wet
    return out


def _normalize_peak(stereo: np.ndarray, target_db: float = -3.0) -> np.ndarray:
    peak = float(np.max(np.abs(stereo))) + 1e-9
    target_lin = 10.0 ** (target_db / 20.0)
    return stereo * (target_lin / peak)


def synthesize_intro_theme(path: Path) -> Path:
    """
    Synthesizes a warm, modern 4.8s sports-talk intro theme (108 BPM):
    Dm9 -> G13 -> Cmaj9 Rhodes chords over a tight groove with a clean fade tail.
    """
    dur = 4.8
    beat = 60.0 / 108.0  # ~0.5556s per beat
    buf = np.zeros((int(SAMPLE_RATE * dur), 2), dtype=np.float64)

    # Chord progression: Dm9 (beats 0-2), G13 (beats 2-4), Cmaj9 (beats 4-8)
    chords = [
        (0.0, 1.35, [293.66, 349.23, 440.00, 523.25, 659.25], 73.42),   # Dm9 (D2 bass)
        (2.0 * beat, 1.35, [246.94, 349.23, 440.00, 523.25, 659.25], 98.00),  # G13 (G2 bass)
        (4.0 * beat, 2.50, [261.63, 329.63, 392.00, 493.88, 587.33], 65.41),  # Cmaj9 (C2 bass)
    ]
    pans = [-0.45, -0.2, 0.0, 0.25, 0.45]
    for start, cdur, notes, bass_f in chords:
        for i, f in enumerate(notes):
            _add_at(buf, _rhodes_note(f, cdur, amp=0.11, pan=pans[i % len(pans)]), start + i * 0.012)
        _add_at(buf, _warm_bass_note(bass_f, min(cdur, 1.0), amp=0.26), start)

    # Crisp sports-desk rhythmic pulse (subtle, not overpowering)
    for b_idx in [0.0, 1.5, 2.0, 3.5, 4.0]:
        _add_at(buf, _kick_hit(amp=0.28), b_idx * beat)
    for h_idx in range(11):
        amp = 0.04 if h_idx % 2 == 0 else 0.024
        _add_at(buf, _soft_hat(amp=amp, seed=100 + h_idx), h_idx * 0.5 * beat)

    # Warm shimmer arpeggio accent at beat 4.5
    for idx, f in enumerate([587.33, 659.25, 783.99]):
        _add_at(buf, _rhodes_note(f, 1.1, amp=0.065, pan=0.3 - idx * 0.3), (4.5 + idx * 0.25) * beat)

    out = _apply_simple_reverb(buf, wet=0.22)
    # Smooth fade-in (15ms) and broadcast bed fade-out (last 1.8s)
    fi = int(0.015 * SAMPLE_RATE)
    out[:fi] *= np.linspace(0.0, 1.0, fi)[:, None]
    fo = int(1.8 * SAMPLE_RATE)
    out[-fo:] *= (np.linspace(1.0, 0.0, fo) ** 1.7)[:, None]

    return _write_stereo_wav(_normalize_peak(out, target_db=-4.5), path)


def synthesize_act_bumper(path: Path) -> Path:
    """
    Synthesizes a crisp, tasteful 1.25s segment transition bumper
    (warm 2-chord broadcast stab + soft low thump with silky tail).
    """
    dur = 1.25
    buf = np.zeros((int(SAMPLE_RATE * dur), 2), dtype=np.float64)

    # Quick G9 pickup -> Cmaj9 resolve
    _add_at(buf, _kick_hit(dur=0.18, amp=0.22), 0.0)
    for i, f in enumerate([392.00, 493.88, 587.33]):
        _add_at(buf, _rhodes_note(f, 0.38, amp=0.10, pan=-0.3 + i * 0.3), 0.0 + i * 0.015)

    _add_at(buf, _warm_bass_note(130.81, 0.65, amp=0.20), 0.20)
    for i, f in enumerate([261.63, 329.63, 392.00, 493.88, 587.33]):
        _add_at(buf, _rhodes_note(f, 0.95, amp=0.105, pan=-0.4 + i * 0.2), 0.20 + i * 0.012)

    out = _apply_simple_reverb(buf, wet=0.24)
    fi = int(0.012 * SAMPLE_RATE)
    out[:fi] *= np.linspace(0.0, 1.0, fi)[:, None]
    fo = int(0.45 * SAMPLE_RATE)
    out[-fo:] *= (np.linspace(1.0, 0.0, fo) ** 1.8)[:, None]

    return _write_stereo_wav(_normalize_peak(out, target_db=-7.5), path)


def synthesize_outro_theme(path: Path) -> Path:
    """Synthesizes a warm 4.2s closing broadcast resolution theme."""
    dur = 4.2
    beat = 60.0 / 104.0
    buf = np.zeros((int(SAMPLE_RATE * dur), 2), dtype=np.float64)

    chords = [
        (0.0, 1.4, [349.23, 440.00, 523.25, 659.25], 87.31),            # Fmaj7
        (1.5 * beat, 1.3, [392.00, 493.88, 587.33, 659.25], 98.00),     # G6
        (3.0 * beat, 2.4, [261.63, 329.63, 392.00, 493.88, 587.33], 65.41),  # Cmaj9
    ]
    for start, cdur, notes, bass_f in chords:
        for i, f in enumerate(notes):
            _add_at(buf, _rhodes_note(f, cdur, amp=0.105, pan=-0.4 + i * 0.2), start + i * 0.015)
        _add_at(buf, _warm_bass_note(bass_f, min(cdur, 1.1), amp=0.23), start)

    for b_idx in [0.0, 1.5, 3.0]:
        _add_at(buf, _kick_hit(amp=0.22), b_idx * beat)

    out = _apply_simple_reverb(buf, wet=0.25)
    fi = int(0.35 * SAMPLE_RATE)
    out[:fi] *= np.linspace(0.0, 1.0, fi)[:, None]
    fo = int(1.6 * SAMPLE_RATE)
    out[-fo:] *= (np.linspace(1.0, 0.0, fo) ** 1.8)[:, None]

    return _write_stereo_wav(_normalize_peak(out, target_db=-5.5), path)


def get_packaging_assets(regenerate: bool = False) -> Dict[str, Path]:
    """
    Returns paths to `intro`, `bumper`, and `outro` audio assets.
    Checks `FantasyRecap/assets/custom/` first so Nick can drop in custom `.mp3`/`.wav`
    music files at any time; otherwise auto-synthesizes clean broadcast defaults.
    """
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    specs = {
        "intro": ("intro_theme", synthesize_intro_theme),
        "bumper": ("act_bumper", synthesize_act_bumper),
        "outro": ("outro_theme", synthesize_outro_theme),
    }
    resolved: Dict[str, Path] = {}
    for key, (stem, synth_fn) in specs.items():
        custom_hit: Optional[Path] = None
        if CUSTOM_ASSETS_DIR.exists():
            for ext in (".mp3", ".wav", ".m4a"):
                cand = CUSTOM_ASSETS_DIR / f"{stem}{ext}"
                if cand.exists() and cand.stat().st_size > 1000:
                    custom_hit = cand
                    break
        if custom_hit:
            resolved[key] = custom_hit
            continue

        default_wav = ASSETS_DIR / f"{stem}.wav"
        if regenerate or not default_wav.exists() or default_wav.stat().st_size < 1000:
            synth_fn(default_wav)
        resolved[key] = default_wav
    return resolved


if __name__ == "__main__":
    assets = get_packaging_assets(regenerate=True)
    for k, p in assets.items():
        print(f"🎵 Generated {k}: {p} ({p.stat().st_size / 1024:.1f} KB)")
