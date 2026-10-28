"""Tests for lofi_fx.py. They use generated test tones only, so no music files are needed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from pedalboard.io import AudioFile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "lofi-audio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lofi_fx  # noqa: E402
from lofi_fx import PRESETS, build_settings, process  # noqa: E402


def tone(path: Path, sr: int = 44100, seconds: float = 2.0,
         channels: int = 2) -> Path:
    t = np.arange(int(sr * seconds)) / sr
    mono = 0.3 * np.sin(2 * np.pi * 220 * t)
    audio = np.tile(mono, (channels, 1)).astype(np.float32)
    with AudioFile(str(path), "w", samplerate=sr, num_channels=channels) as f:
        f.write(audio)
    return path


def read(path: Path) -> tuple[np.ndarray, float]:
    with AudioFile(str(path)) as f:
        return f.read(f.frames), f.samplerate


@pytest.mark.parametrize("sr", [8000, 16000, 22050, 44100, 96000])
@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_rate_and_preset_to_mp3(tmp_path, sr, preset):
    src = tone(tmp_path / f"in{sr}.wav", sr=sr)
    s = build_settings(preset, rain=0.15, vinyl=0.2)
    result = process(str(src), str(tmp_path / "out.mp3"), s)
    assert result["status"] == "ok"
    assert result["sample_rate"] in lofi_fx.MP3_RATES
    audio, _ = read(tmp_path / "out.mp3")
    assert audio.shape[0] == 2 and np.isfinite(audio).all()
    assert np.max(np.abs(audio)) <= 1.0


@pytest.mark.parametrize("ext", [".wav", ".flac", ".mp3"])
def test_output_formats(tmp_path, ext):
    src = tone(tmp_path / "in.wav")
    result = process(str(src), str(tmp_path / f"out{ext}"), build_settings())
    assert Path(result["output"]).is_file()
    assert not list(tmp_path.glob(".*.tmp*")), "temp file left behind"


@pytest.mark.parametrize("channels", [1, 6, 4])
def test_mono_and_surround_become_stereo(tmp_path, channels):
    src = tone(tmp_path / "in.wav", channels=channels)
    result = process(str(src), str(tmp_path / "out.wav"), build_settings())
    assert result["channels"] == 2


def test_vinyl_mode_length(tmp_path):
    src = tone(tmp_path / "in.wav", seconds=2.0)
    s = build_settings(speed=0.8, tail_seconds=0)
    d = process(str(src), str(tmp_path / "out.wav"), s)["duration_seconds"]
    assert d["output"] == pytest.approx(2.0 / 0.8, abs=0.3)


def test_stretch_mode_length(tmp_path):
    src = tone(tmp_path / "in.wav", seconds=2.0)
    s = build_settings(speed=0.8, mode="stretch", tail_seconds=0)
    d = process(str(src), str(tmp_path / "out.wav"), s)["duration_seconds"]
    assert d["output"] == pytest.approx(2.0 / 0.8, abs=0.3)


def test_ambience_loop(tmp_path):
    src = tone(tmp_path / "in.wav", seconds=2.0)
    amb = tone(tmp_path / "amb.wav", sr=22050, seconds=0.5, channels=1)
    s = build_settings(ambience=str(amb), ambience_level=0.3)
    result = process(str(src), str(tmp_path / "out.wav"), s)
    assert result["background_layers"] == ["ambience:amb.wav@0.3"]


def test_refuses_to_overwrite(tmp_path):
    src = tone(tmp_path / "in.wav")
    dst = tmp_path / "out.wav"
    dst.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        process(str(src), str(dst), build_settings())
    assert dst.read_bytes() == b"keep me"
    process(str(src), str(dst), build_settings(), overwrite=True)
    assert dst.read_bytes() != b"keep me"


def test_refuses_same_input_and_output(tmp_path):
    src = tone(tmp_path / "in.wav")
    with pytest.raises(ValueError):
        process(str(src), str(src), build_settings(), overwrite=True)


def test_max_minutes(tmp_path):
    src = tone(tmp_path / "in.wav", seconds=2.0)
    s = build_settings(max_minutes=1 / 60)  # 1 second
    with pytest.raises(ValueError, match="minutes long"):
        process(str(src), str(tmp_path / "out.wav"), s)


@pytest.mark.parametrize("override", [
    {"speed": 0.3}, {"speed": 1.5}, {"room_size": 1.2}, {"rain": -0.1},
    {"lowpass_hz": 5}, {"highpass_hz": 5000, "lowpass_hz": 4000},
    {"bit_depth": 0}, {"tail_seconds": 60}, {"mode": "fast"},
])
def test_validation(tmp_path, override):
    src = tone(tmp_path / "in.wav")
    with pytest.raises(ValueError):
        process(str(src), str(tmp_path / "out.wav"), build_settings(**override))


def test_unknown_preset():
    with pytest.raises(ValueError):
        build_settings("nightcore")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "lofi_fx.py"), *args],
                          capture_output=True, text=True)


def test_cli_check():
    r = run_cli("--check")
    assert r.returncode == 0
    assert json.loads(r.stdout)["ok"] is True


def test_cli_json_success_and_error(tmp_path):
    src = tone(tmp_path / "in.wav")
    r = run_cli(str(src), str(tmp_path / "out.mp3"), "--preset", "lofi",
                "--rain", "0.15", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "ok"

    r = run_cli(str(tmp_path / "missing.mp3"), str(tmp_path / "x.mp3"), "--json")
    assert r.returncode == 1
    assert json.loads(r.stderr)["status"] == "error"
