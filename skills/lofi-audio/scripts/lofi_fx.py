#!/usr/bin/env python3
"""
lofi_fx.py - turn any song into a "Slowed + Reverb" or "Lofi" version.

Pipeline
    1. Load audio (.mp3 / .wav / .flac / .ogg / .m4a*)  -> float32, stereo-safe
       Low sample rates (< 32 kHz) are resampled to 44.1 kHz first.
    2. Slow down
         - "vinyl" mode (default): tempo AND pitch drop together, like playing a
           record at the wrong speed. This is the classic slowed + reverb sound.
         - "stretch" mode: tempo drops, pitch stays the same (time-stretch).
    3. Tone shaping (lofi preset): high-pass, low-pass, gentle bit-crush,
       chorus wobble, soft compression
    4. Reverb, with a silent tail added so the echo is not cut off
    5. Optional background layer: procedural vinyl crackle, procedural rain,
       and/or your own ambience file (looped to fit)
    6. Peak limiting + normalisation, then write .wav / .mp3 / .flac
       (via a temporary file, so a failed run never leaves a broken output)

Only Python libraries are needed (pedalboard reads and writes MP3 itself).
*.m4a / .aac reading depends on your OS codecs; convert with ffmpeg if it fails.

Examples
    python lofi_fx.py --check
    python lofi_fx.py song.mp3 song_slowed.mp3
    python lofi_fx.py song.wav out.wav --preset lofi --vinyl 0.25 --rain 0.15
    python lofi_fx.py song.mp3 out.mp3 --speed 0.85 --mode stretch --room-size 0.9
    python lofi_fx.py song.mp3 out.wav --ambience rain.wav --ambience-level 0.2 --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

__version__ = "1.0.0"

REQUIRED_MODULES = ("numpy", "scipy", "pedalboard")

try:
    import numpy as np
    from pedalboard import (
        Bitcrush,
        Chorus,
        Compressor,
        HighpassFilter,
        Limiter,
        LowpassFilter,
        Pedalboard,
        Reverb,
        time_stretch,
    )
    from pedalboard.io import AudioFile
    from scipy import signal
except ImportError as _exc:  # reported by --check and process(), never exits
    _IMPORT_ERROR: ImportError | None = _exc
else:
    _IMPORT_ERROR = None

SUPPORTED_INPUT = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif", ".m4a", ".aac"}
SUPPORTED_OUTPUT = {".wav", ".mp3", ".flac"}
MP3_RATES = {32000, 44100, 48000}  # sample rates the MP3 encoder accepts
MIN_WORK_RATE = 32000.0  # below this, upsample so filters and MP3 output work
WORK_RATE = 44100.0
READ_CHUNK = 1 << 18  # frames per read; keeps memory steady on long files


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    speed: float = 0.8              # 0.5 - 1.0 ; 0.8 = 80 % speed
    mode: str = "vinyl"             # "vinyl" (pitch drops) | "stretch" (pitch kept)
    # Reverb
    room_size: float = 0.75         # 0 - 1
    damping: float = 0.5            # 0 - 1 ; higher = darker tail
    wet_level: float = 0.33         # 0 - 1
    dry_level: float = 0.7          # 0 - 1
    width: float = 1.0              # 0 - 1 stereo width of the tail
    tail_seconds: float = 4.0       # silence appended so reverb can ring out
    # Lofi tone shaping (0 / None = off)
    lowpass_hz: float | None = None
    highpass_hz: float | None = None
    bit_depth: float | None = None  # e.g. 12 for subtle grit; None = off
    wobble: float = 0.0             # 0 - 1 chorus-based tape wobble
    compress: bool = False
    # Background layers (0 = off). Levels are relative to the music peak.
    vinyl: float = 0.0
    rain: float = 0.0
    ambience: str | None = None
    ambience_level: float = 0.2
    # Output
    target_peak_db: float = -1.0
    mp3_quality: str = "V0"         # LAME VBR preset used for .mp3 output
    seed: int | None = 42
    # Safety: longest input accepted, so a huge file cannot exhaust memory
    # (peak use is roughly 200 MB per input minute)
    max_minutes: float = 12.0


PRESETS: dict[str, Settings] = {
    "slowed-reverb": Settings(),
    "lofi": Settings(
        speed=0.85,
        room_size=0.55,
        damping=0.7,
        wet_level=0.22,
        dry_level=0.8,
        tail_seconds=3.0,
        lowpass_hz=4500,
        highpass_hz=90,
        bit_depth=12,
        wobble=0.35,
        compress=True,
        vinyl=0.18,
    ),
    "dreamy": Settings(
        speed=0.78,
        room_size=0.95,
        damping=0.35,
        wet_level=0.45,
        dry_level=0.6,
        tail_seconds=6.0,
        lowpass_hz=9000,
    ),
}


def missing_dependencies() -> list[str]:
    return [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def load_audio(path: Path, max_minutes: float | None = None
               ) -> tuple[np.ndarray, float]:
    """Return (audio[channels, frames] float32, sample_rate). Mono -> stereo."""
    chunks = []
    with AudioFile(str(path)) as f:
        sr = f.samplerate
        if max_minutes and f.duration > max_minutes * 60:
            raise ValueError(
                f"{path.name} is {f.duration / 60:.1f} minutes long; the limit "
                f"is {max_minutes:g}. Raise it with --max-minutes if your "
                f"computer has enough memory.")
        # MP3 length can be an estimate, so read until the decoder stops.
        while True:
            block = f.read(READ_CHUNK)
            if block.shape[-1] == 0:
                break
            chunks.append(block)
    if not chunks:
        raise ValueError(f"No audio frames could be read from {path}")
    audio = np.concatenate(chunks, axis=1).astype(np.float32)
    if audio.shape[0] == 1:
        audio = np.vstack([audio, audio])
    elif audio.shape[0] > 2:  # fold surround down to stereo
        audio = _downmix(audio)
    return audio, float(sr)


def _downmix(audio: np.ndarray) -> np.ndarray:
    if audio.shape[0] == 6:  # 5.1 in WAV order: L R C LFE Ls Rs (LFE dropped)
        c = 0.707 * audio[2]
        left = audio[0] + c + 0.707 * audio[4]
        right = audio[1] + c + 0.707 * audio[5]
        return np.vstack([left, right]).astype(np.float32)
    mid = audio.mean(axis=0)  # unknown layout: keep every channel, centred
    return np.vstack([mid, mid]).astype(np.float32)


def save_audio(path: Path, audio: np.ndarray, sr: float, mp3_quality: str) -> float:
    """Write via a temp file in the same folder, then rename. Returns the rate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if path.suffix.lower() == ".mp3":
        kwargs["quality"] = mp3_quality
        if int(round(sr)) not in MP3_RATES:
            target = 48000.0 if sr > 44100 else 44100.0
            audio, sr = _resample(audio, sr, target), target
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        with AudioFile(str(tmp), "w", samplerate=int(round(sr)),
                       num_channels=audio.shape[0], **kwargs) as f:
            f.write(audio)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return sr


# --------------------------------------------------------------------------- #
# DSP steps
# --------------------------------------------------------------------------- #
def slow_down(audio: np.ndarray, sr: float, speed: float, mode: str) -> np.ndarray:
    if abs(speed - 1.0) < 1e-6:
        return audio
    if mode == "stretch":
        return time_stretch(audio, sr, stretch_factor=speed,
                            high_quality=True).astype(np.float32)
    # "vinyl": resample so there are 1/speed times as many samples, then play
    # them back at the original rate -> slower and lower, like a turntable.
    up, down = _ratio(1.0 / speed)
    return signal.resample_poly(audio, up, down, axis=1).astype(np.float32)


def _resample(audio: np.ndarray, from_sr: float, to_sr: float) -> np.ndarray:
    up, down = _ratio(to_sr / from_sr)
    return signal.resample_poly(audio, up, down, axis=1).astype(np.float32)


def _ratio(x: float, max_den: int = 1000) -> tuple[int, int]:
    from fractions import Fraction
    fr = Fraction(x).limit_denominator(max_den)
    return fr.numerator, fr.denominator


def _band(lo: float, hi: float, sr: float) -> list[float]:
    """Normalised band edges, kept safely below Nyquist for any sample rate."""
    nyq = sr / 2
    hi = min(hi, 0.9 * nyq)
    return [min(lo, 0.5 * hi) / nyq, hi / nyq]


def tone_and_reverb(audio: np.ndarray, sr: float, s: Settings) -> np.ndarray:
    nyq_safe = 0.45 * sr
    chain = []
    if s.highpass_hz:
        chain.append(HighpassFilter(
            cutoff_frequency_hz=min(s.highpass_hz, nyq_safe)))
    if s.lowpass_hz:
        chain.append(LowpassFilter(
            cutoff_frequency_hz=min(s.lowpass_hz, nyq_safe)))
    if s.bit_depth:
        chain.append(Bitcrush(bit_depth=s.bit_depth))
    if s.wobble > 0:
        chain.append(Chorus(rate_hz=0.6, depth=0.15 * s.wobble,
                            centre_delay_ms=7.0, feedback=0.0, mix=0.5 * s.wobble))
    if s.compress:
        chain.append(Compressor(threshold_db=-18, ratio=3, attack_ms=10,
                                release_ms=150))
    chain.append(Reverb(room_size=s.room_size, damping=s.damping,
                        wet_level=s.wet_level, dry_level=s.dry_level,
                        width=s.width))

    tail = np.zeros((audio.shape[0], int(sr * max(s.tail_seconds, 0))),
                    dtype=np.float32)
    padded = np.concatenate([audio, tail], axis=1)
    out = Pedalboard(chain)(padded, sr)
    return _trim_trailing_silence(out, sr)


def _trim_trailing_silence(audio: np.ndarray, sr: float,
                           threshold_db: float = -70.0) -> np.ndarray:
    thr = 10 ** (threshold_db / 20)
    loud = np.where(np.max(np.abs(audio), axis=0) > thr)[0]
    if loud.size == 0:
        return audio
    end = min(audio.shape[1], loud[-1] + int(0.25 * sr))
    return audio[:, :end]


def vinyl_crackle(n: int, sr: float, rng: np.random.Generator) -> np.ndarray:
    """Procedural vinyl: soft hiss, dense dust crackle, rare louder pops."""
    hiss = rng.standard_normal((2, n), dtype=np.float32)
    b, a = signal.butter(2, _band(800, 6000, sr), btype="band")
    hiss = (signal.lfilter(b, a, hiss, axis=1) * 0.05).astype(np.float32)

    crackle = np.zeros((2, n), dtype=np.float32)
    n_clicks = int(n / sr * 25)                      # ~25 small clicks / sec
    idx = rng.integers(0, n, n_clicks)
    crackle[rng.integers(0, 2, n_clicks), idx] = (
        rng.standard_normal(n_clicks) * rng.uniform(0.1, 0.6, n_clicks))
    n_pops = max(1, int(n / sr * 0.6))               # a pop every ~2 seconds
    pidx = rng.integers(0, n, n_pops)
    crackle[:, pidx] += rng.uniform(0.6, 1.0, n_pops) * rng.choice([-1, 1], n_pops)
    b, a = signal.butter(2, _band(1000, 9000, sr), btype="band")
    crackle = signal.lfilter(b, a, crackle, axis=1).astype(np.float32)

    # 33 rpm rumble/thump for realism
    t = np.arange(n, dtype=np.float32) / np.float32(sr)
    rumble = (0.02 * np.sin(2 * np.pi * 0.55 * t))[None, :]
    return _peak_normalise((hiss + crackle + rumble).astype(np.float32))


def rain_noise(n: int, sr: float, rng: np.random.Generator) -> np.ndarray:
    """Procedural soft rain: filtered pink noise with slow swells + drips."""
    white = rng.standard_normal((2, n), dtype=np.float32)
    # Pink-ish noise (Paul Kellet's economy filter)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    pink = signal.lfilter(b, a, white, axis=1).astype(np.float32)
    del white
    bb, ba = signal.butter(2, _band(300, 7000, sr), btype="band")
    pink = signal.lfilter(bb, ba, pink, axis=1).astype(np.float32)

    t = np.arange(n, dtype=np.float32) / np.float32(sr)
    swell = 0.75 + 0.25 * np.sin(2 * np.pi * 0.07 * t + rng.uniform(0, 6.28))
    drips = np.zeros((2, n), dtype=np.float32)
    k = int(n / sr * 40)
    didx = rng.integers(0, n, k)
    drips[rng.integers(0, 2, k), didx] = rng.uniform(0.2, 0.8, k)
    db_, da_ = signal.butter(2, _band(2000, 8000, sr), btype="band")
    drips = signal.lfilter(db_, da_, drips, axis=1).astype(np.float32)
    return _peak_normalise((_peak_normalise(pink) * swell + 0.3 * drips)
                           .astype(np.float32))


def load_ambience(path: Path, n: int, sr: float) -> np.ndarray:
    amb, asr = load_audio(path)
    if abs(asr - sr) > 1:
        amb = _resample(amb, asr, sr)
    reps = int(np.ceil(n / amb.shape[1]))
    amb = np.tile(amb, (1, reps))[:, :n]
    # 1-second fade in/out so the loop never starts with a click
    fade = min(int(sr), n // 2)
    if fade > 0:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        amb[:, :fade] *= ramp
        amb[:, -fade:] *= ramp[::-1]
    return _peak_normalise(amb)


def _peak_normalise(x: np.ndarray, peak: float = 1.0) -> np.ndarray:
    m = float(np.max(np.abs(x))) if x.size else 0.0
    return x if m == 0 else (x / m * peak).astype(np.float32)


def master(audio: np.ndarray, sr: float, target_peak_db: float) -> np.ndarray:
    limited = Pedalboard([Limiter(threshold_db=target_peak_db - 0.5,
                                  release_ms=100)])(audio, sr)
    return _peak_normalise(limited, 10 ** (target_peak_db / 20))


# --------------------------------------------------------------------------- #
# Main entry point (also imported by the MCP server)
# --------------------------------------------------------------------------- #
def process(input_path: str, output_path: str, settings: Settings,
            overwrite: bool = False) -> dict:
    if _IMPORT_ERROR is not None:
        raise ImportError(
            f"Missing dependency ({_IMPORT_ERROR.name or _IMPORT_ERROR}). "
            f"Run:  pip install -r requirements.txt") from _IMPORT_ERROR
    t0 = time.time()
    src, dst = Path(input_path).expanduser(), Path(output_path).expanduser()
    _validate(src, dst, settings, overwrite)

    audio, sr = load_audio(src, settings.max_minutes)
    in_seconds = audio.shape[1] / sr
    if sr < MIN_WORK_RATE:  # e.g. 8-22 kHz voice notes
        audio, sr = _resample(audio, sr, WORK_RATE), WORK_RATE

    audio = slow_down(audio, sr, settings.speed, settings.mode)
    audio = tone_and_reverb(audio, sr, settings)

    rng = np.random.default_rng(settings.seed)
    music_peak = float(np.max(np.abs(audio))) or 1.0
    n = audio.shape[1]
    layers = []
    if settings.vinyl > 0:
        audio += vinyl_crackle(n, sr, rng) * settings.vinyl * music_peak * 0.5
        layers.append(f"vinyl@{settings.vinyl}")
    if settings.rain > 0:
        audio += rain_noise(n, sr, rng) * settings.rain * music_peak * 0.5
        layers.append(f"rain@{settings.rain}")
    if settings.ambience:
        amb = load_ambience(Path(settings.ambience).expanduser(), n, sr)
        audio += amb * settings.ambience_level * music_peak
        layers.append(f"ambience:{Path(settings.ambience).name}"
                      f"@{settings.ambience_level}")

    audio = master(audio, sr, settings.target_peak_db)
    out_seconds = audio.shape[1] / sr
    out_sr = save_audio(dst, audio, sr, settings.mp3_quality)

    return {
        "status": "ok",
        "input": str(src),
        "output": str(dst.resolve()),
        "sample_rate": int(round(out_sr)),
        "channels": int(audio.shape[0]),
        "duration_seconds": {
            "input": round(in_seconds, 2),
            "output": round(out_seconds, 2),
        },
        "background_layers": layers or ["none"],
        "settings": asdict(settings),
        "processing_seconds": round(time.time() - t0, 2),
    }


def _validate(src: Path, dst: Path, s: Settings, overwrite: bool = False) -> None:
    if not src.is_file():
        raise FileNotFoundError(f"Input file not found: {src}")
    if src.suffix.lower() not in SUPPORTED_INPUT:
        raise ValueError(f"Unsupported input type {src.suffix}. "
                         f"Use one of {sorted(SUPPORTED_INPUT)}")
    if dst.suffix.lower() not in SUPPORTED_OUTPUT:
        raise ValueError(f"Unsupported output type {dst.suffix}. "
                         f"Use one of {sorted(SUPPORTED_OUTPUT)}")
    if src.resolve() == dst.resolve():
        raise ValueError("Output path must differ from input path.")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {dst}. "
                              f"Choose another name or pass --overwrite.")
    if not 0.5 <= s.speed <= 1.0:
        raise ValueError("--speed must be between 0.5 and 1.0")
    if s.mode not in {"vinyl", "stretch"}:
        raise ValueError("--mode must be 'vinyl' or 'stretch'")
    for name in ("room_size", "damping", "wet_level", "dry_level", "width",
                 "wobble", "vinyl", "rain", "ambience_level"):
        v = getattr(s, name)
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1 (got {v})")
    for name in ("lowpass_hz", "highpass_hz"):
        v = getattr(s, name)
        if v is not None and not 20 <= v <= 20000:
            raise ValueError(f"{name} must be between 20 and 20000 Hz (got {v})")
    if s.lowpass_hz and s.highpass_hz and s.highpass_hz >= s.lowpass_hz:
        raise ValueError("highpass_hz must be lower than lowpass_hz")
    if s.bit_depth is not None and not 1 <= s.bit_depth <= 32:
        raise ValueError(f"bit_depth must be between 1 and 32 (got {s.bit_depth})")
    if not 0 <= s.tail_seconds <= 30:
        raise ValueError(f"tail_seconds must be between 0 and 30 (got {s.tail_seconds})")
    if not -24 <= s.target_peak_db <= 0:
        raise ValueError("target_peak_db must be between -24 and 0")
    if s.max_minutes <= 0:
        raise ValueError("max_minutes must be above 0")
    if s.ambience and not Path(s.ambience).expanduser().is_file():
        raise FileNotFoundError(f"Ambience file not found: {s.ambience}")


def build_settings(preset: str = "slowed-reverb", **overrides) -> Settings:
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset '{preset}'. Choose from {list(PRESETS)}")
    clean = {k: v for k, v in overrides.items() if v is not None}
    return replace(PRESETS[preset], **clean)


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Make a Slowed + Reverb or Lofi version of an audio file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", nargs="?",
                   help="input audio file (.mp3, .wav, .flac, ...)")
    p.add_argument("output", nargs="?", help="output file (.wav, .mp3 or .flac)")
    p.add_argument("--preset", default="slowed-reverb", choices=list(PRESETS))
    p.add_argument("--speed", type=float, help="0.5-1.0; 0.8 = 80%% speed")
    p.add_argument("--mode", choices=["vinyl", "stretch"],
                   help="vinyl: pitch drops with tempo; stretch: pitch kept")
    p.add_argument("--room-size", type=float)
    p.add_argument("--damping", type=float)
    p.add_argument("--wet", dest="wet_level", type=float)
    p.add_argument("--dry", dest="dry_level", type=float)
    p.add_argument("--tail", dest="tail_seconds", type=float,
                   help="seconds of reverb tail to keep")
    p.add_argument("--lowpass", dest="lowpass_hz", type=float)
    p.add_argument("--highpass", dest="highpass_hz", type=float)
    p.add_argument("--bit-depth", type=float)
    p.add_argument("--wobble", type=float)
    p.add_argument("--vinyl", type=float, help="vinyl crackle level 0-1")
    p.add_argument("--rain", type=float, help="procedural rain level 0-1")
    p.add_argument("--ambience", help="your own rain/vinyl/cafe file to loop")
    p.add_argument("--ambience-level", type=float)
    p.add_argument("--mp3-quality",
                   help="LAME VBR preset V0-V9, or a bitrate such as 320")
    p.add_argument("--seed", type=int, help="random seed for the noise layers")
    p.add_argument("--max-minutes", type=float,
                   help="refuse inputs longer than this (default 12; "
                        "about 200 MB of memory per minute)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace the output file if it already exists")
    p.add_argument("--check", action="store_true",
                   help="only check that the required libraries are installed")
    p.add_argument("--json", action="store_true",
                   help="print a JSON summary (handy for Claude Code)")
    p.add_argument("--version", action="version", version=__version__)
    args = vars(p.parse_args())

    if args.pop("check"):
        missing = missing_dependencies()
        print(json.dumps({"ok": not missing, "missing": missing,
                          "python": sys.executable, "version": __version__}))
        return 0 if not missing else 1

    as_json, overwrite = args.pop("json"), args.pop("overwrite")
    src, dst, preset = args.pop("input"), args.pop("output"), args.pop("preset")
    if not src or not dst:
        p.error("input and output are required (or use --check)")
    try:
        settings = build_settings(preset, **args)
        result = process(src, dst, settings, overwrite=overwrite)
    except Exception as exc:  # noqa: BLE001 - report any failure cleanly
        err = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(err, indent=2) if as_json else f"ERROR: {err['error']}",
              file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        d = result["duration_seconds"]
        print(f"Done -> {result['output']}\n"
              f"Length: {d['input']}s -> {d['output']}s | "
              f"layers: {', '.join(result['background_layers'])} | "
              f"took {result['processing_seconds']}s")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
