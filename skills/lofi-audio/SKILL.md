---
name: lofi-audio
description: Turn a song or any audio file into a "Slowed + Reverb", "Lofi" or "Dreamy" version, optionally with vinyl crackle, soft rain, or a user-supplied ambience loop. Use this skill whenever the user asks to slow down a track, add reverb or echo, make something lofi, chill, vaporwave, nightcore-in-reverse, "slowed and reverbed", add vinyl or rain sounds behind music, or otherwise apply mood effects to an .mp3, .wav or .flac file — even if they do not say "skill" or name a library.
---

# Lofi / Slowed + Reverb audio

Claude cannot listen to audio, so all processing goes through `scripts/lofi_fx.py`
in this skill's folder (the base directory shown when this skill loads; written
`<skill-dir>` below). Never try to hand-write DSP code for these requests; run the script.

## 1. Check dependencies (once per machine)

Python 3.10+ is needed. First look for the skill's own virtual environment,
`~/.venvs/lofi-audio`. Its Python is `~/.venvs/lofi-audio/bin/python` on
macOS/Linux and `~/.venvs/lofi-audio/Scripts/python.exe` on Windows. If it exists,
use it as `<python>`. Otherwise use the first command that works: `python3`
(macOS/Linux), `py` (Windows), or `python`. The check works the same in bash, zsh and PowerShell:

```
<python> "<skill-dir>/scripts/lofi_fx.py" --check
```

It prints JSON such as `{"ok": false, "missing": ["pedalboard"], ...}`.
If `ok` is false, **ask the user before installing anything**, then create the
virtual environment and install into it. Do not pip-install into the system
Python: Homebrew and recent Linux refuse it ("externally-managed-environment").

```
<system python> -m venv ~/.venvs/lofi-audio
<venv python> -m pip install -r "<skill-dir>/requirements.txt"
```

Re-run `--check` with the venv Python. From then on, **use the venv Python for
every command below**, not the bare `python`.

## 2. Pick a preset from the user's words

| User says | Preset | Notes |
|---|---|---|
| "slowed + reverb", "slowed", "TikTok slowed" | `slowed-reverb` | 0.8x, pitch drops, big room |
| "lofi", "chill", "study beats", "old tape" | `lofi` | 0.85x, muffled highs, wobble, vinyl crackle |
| "dreamy", "ethereal", "underwater", "spacey" | `dreamy` | 0.78x, huge reverb, long tail |

If the user wants the pitch to stay the same, add `--mode stretch`.
Map "a bit slower" to `--speed 0.9`, "really slow" to `--speed 0.7` (allowed range 0.5-1.0).
"Add rain" -> `--rain 0.15`. "Add vinyl / crackle" -> `--vinyl 0.2`.
If they supply their own rain/cafe file, use `--ambience <file> --ambience-level 0.2`.

## 3. Run it

Default output name: `<input-stem>_<preset>.<ext>` next to the input, unless the
user names one. `<ext>` is the input's extension if it is mp3, wav or flac,
otherwise `mp3` (.ogg/.aiff/.m4a cannot be written). Never overwrite the input file. The script refuses to
replace an existing output; only add `--overwrite` if the user says so.

```
<python> "<skill-dir>/scripts/lofi_fx.py" "<input>" "<output>" --preset lofi --rain 0.15 --json
```

Always pass `--json` and read the result. On `"status": "error"`, explain the
error in plain words and fix the command:
- wrong path, unsupported format or a value out of range -> correct it and retry
- `FileExistsError` -> pick a new name (e.g. add `_2`) or ask about overwriting
- "minutes long; the limit is ..." -> the file is long; tell the user it needs
  about 200 MB of memory per minute and, if they agree, add `--max-minutes <n>`
- `.m4a`/`.aac` that fails to open -> convert first: `ffmpeg -i in.m4a in.wav`

## 4. Report back

Tell the user: output path, length before -> after, preset and any layers added.
Offer one or two tweaks (e.g. "want it slower, or less reverb?"). Because you
cannot hear the result, do not claim how it sounds; describe what was applied.

## All options

Run `<python> "<skill-dir>/scripts/lofi_fx.py" --help`. Main ones: `--speed`,
`--mode vinyl|stretch`, `--room-size`, `--damping`, `--wet`, `--dry`, `--tail`,
`--lowpass`, `--highpass`, `--bit-depth`, `--wobble`, `--vinyl`, `--rain`,
`--ambience`, `--ambience-level`, `--mp3-quality`, `--seed`, `--max-minutes`,
`--overwrite`. Inputs: .mp3 .wav .flac .ogg .aiff (any sample rate, mono to 5.1).
Outputs: .mp3 .wav .flac.

## Copyright note

Processing a track does not change who owns it. If the user plans to publish a
slowed version of someone else's song, remind them once that they need the
rights holder's permission.
