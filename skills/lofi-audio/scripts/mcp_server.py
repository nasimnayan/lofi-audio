#!/usr/bin/env python3
"""
Optional MCP server that exposes lofi_fx.py as tools for Claude Code (or any
MCP client). Requires:  pip install -r requirements-mcp.txt

Register with Claude Code:
    claude mcp add lofi-audio -- python /ABSOLUTE/PATH/scripts/mcp_server.py
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from lofi_fx import PRESETS, build_settings, process  # noqa: E402

mcp = FastMCP("lofi-audio")


@mcp.tool()
def make_lofi(
    input_path: str,
    output_path: str,
    preset: str = "slowed-reverb",
    speed: float | None = None,
    mode: str | None = None,
    room_size: float | None = None,
    damping: float | None = None,
    wet_level: float | None = None,
    dry_level: float | None = None,
    tail_seconds: float | None = None,
    lowpass_hz: float | None = None,
    highpass_hz: float | None = None,
    bit_depth: float | None = None,
    wobble: float | None = None,
    vinyl: float | None = None,
    rain: float | None = None,
    ambience: str | None = None,
    ambience_level: float | None = None,
    mp3_quality: str | None = None,
    seed: int | None = None,
    overwrite: bool = False,
) -> dict:
    """Create a Slowed + Reverb, Lofi or Dreamy version of an audio file.

    Args:
        input_path: absolute path to a .mp3/.wav/.flac/.ogg file.
        output_path: absolute path for the result (.mp3, .wav or .flac).
        preset: "slowed-reverb" (default), "lofi" or "dreamy".
        speed: 0.5-1.0, e.g. 0.8 for 80 % speed. Overrides the preset.
        mode: "vinyl" (pitch drops with tempo, classic sound) or "stretch"
            (pitch kept).
        room_size, damping, wet_level, dry_level: reverb controls, 0-1.
        tail_seconds: reverb tail to keep, 0-30.
        lowpass_hz, highpass_hz: tone filters in Hz (e.g. 4500 and 90 for lofi).
        bit_depth: 1-32 for grit (e.g. 12); wobble: tape wobble 0-1.
        vinyl, rain: procedural background layer levels, 0-1 (0 = off).
        ambience: optional path to your own rain/cafe/vinyl loop.
        ambience_level: level for that file, 0-1.
        mp3_quality: "V0"-"V9" or a bitrate such as "320".
        seed: random seed for the noise layers.
        overwrite: replace output_path if it already exists.

    Returns a summary with the output path, durations and settings used.
    """
    try:
        settings = build_settings(
            preset, speed=speed, mode=mode, room_size=room_size,
            damping=damping, wet_level=wet_level, dry_level=dry_level,
            tail_seconds=tail_seconds, lowpass_hz=lowpass_hz,
            highpass_hz=highpass_hz, bit_depth=bit_depth, wobble=wobble,
            vinyl=vinyl, rain=rain, ambience=ambience,
            ambience_level=ambience_level, mp3_quality=mp3_quality, seed=seed)
        return process(input_path, output_path, settings, overwrite=overwrite)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def list_presets() -> dict:
    """List the available presets and their default settings."""
    return {name: asdict(s) for name, s in PRESETS.items()}


if __name__ == "__main__":
    mcp.run()  # stdio transport
