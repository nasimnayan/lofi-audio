# Changelog

## 1.0.0

First public release as a Claude Code plugin.

### Fixed
- Low sample-rate files (8–22 kHz) no longer crash when vinyl or rain is on. This includes the `lofi` preset.
- 88.2/96 kHz files can now be saved as MP3. The output is resampled to 48 kHz.
- Surround files (5.1 and others) keep their centre channel when mixed down to stereo.
- A very short ambience loop no longer causes an error.
- A missing library is reported clearly instead of closing the MCP server.

### Added
- `--check`: one dependency check that works on Windows, macOS and Linux.
- `--overwrite`: existing files are no longer replaced unless you ask.
- `--max-minutes` (default 12): stops very long files from using up memory.
- Output is written to a temporary file first, so a failed run never leaves a broken file.
- More input checks (filters, bit depth, tail, peak level).
- The MCP tool exposes every setting.
- Test suite using generated test tones.

### Changed
- `--speed` must now be between 0.5 and 1.0, matching the documentation.
- Requires Python 3.10+. The minimum library versions are the oldest the tests passed on
  (pedalboard 0.9.14, numpy 2.1, scipy 1.14).
- The MCP library moved to `requirements-mcp.txt`, so skill-only installs stay small.
- Libraries are installed into a virtual environment (`~/.venvs/lofi-audio`), never into system Python.
