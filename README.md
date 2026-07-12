# adEdits — Montage Suite

An automated montage & time-remapping pipeline for gameplay highlight reels. Point it at a folder of raw clips and a music track; it finds the "kill" moment in each clip, finds the heavy beat drops in the track, syncs every kill to a beat, and renders a fully time-remapped montage — speed ramps into the action, a slow-motion tail out, and an impact flash on the exact kill frame — with zero manual editing.

A web dashboard (`server.py` + `frontend/`) sits on top of the pipeline for picking a velocity curve, previewing clips and the audio waveform, and watching a render happen in real time. The pipeline is also fully usable headless via `pipeline.py`.

## How it works

1. **Audio analysis** (`audio_engine.py`) — loads the track with `librosa`, computes the onset envelope, and keeps only onsets whose local RMS energy clears a percentile threshold (so hi-hats and ambient noise don't count as "beats"). Onsets within a small time window of each other are collapsed to the single highest-energy hit. Output: a list of beat-drop timestamps.
2. **Kill detection** (`cv_engine.py`) — template-matches a crop of the game's kill-confirm UI icon (`assets/kill_template.png`) against each clip using `OpenCV`. Scans backward from the end of the clip in coarse steps to find the icon, then frame-by-frame to pin down the exact first frame it appears. Clips where no kill icon is ever found are flagged `[INVALID]` and skipped rather than crashing the run.
3. **Sync planning** (`sync_engine.py`) — assigns each valid clip a beat (one clip per beat, in order) and lays chunks out back-to-back on the timeline so each clip's kill frame lands exactly on its beat. If a clip doesn't have enough pre-kill footage to fill the gap before its beat, the engine freezes the first frame instead ("Hold and Snap") rather than crashing.
4. **Velocity curves** (`curve_engine.py`) — seven mathematical speed profiles (see below), built with `scipy` PCHIP/cubic-spline interpolation, that map output time to input time. Each curve is split into a pre-kill "ramp" and a post-kill "tail", and each is broken into locally-linear pieces so it can be rendered with ordinary FFmpeg filters.
5. **Rendering** (`render_engine.py`) — renders each chunk piece-by-piece: an optional freeze-frame hold, the curve-paced ramp, a brief impact flash (5% zoom pump + blur) on the kill frame, and a curve-paced slow-motion tail using FFmpeg's `minterpolate` for genuine motion-compensated in-between frames (not frame duplication). Pieces are concatenated per chunk, chunks are concatenated into the full video, and the analyzed audio track is muxed in. Hardware encoding (NVENC / AMF / VideoToolbox) is auto-detected and used with a `libx264` fallback.
6. **Cleanup** (`cleanup.py`) — once the final MP4 is written, all intermediate chunks/pieces/frames in `temp_workspace/` are purged automatically.

## Velocity curves

| Curve | Use case |
|---|---|
| `kill_sync` (default) | Baseline for most kills — ramp up into the shot, instant drop to slow-mo on impact, ease back in. |
| `heartbeat` | Rhythmic double-pump for high-BPM trap/EDM edits. |
| `s_curve` | Ramp flattens (near-freeze) right on the kill, then rockets into the next clip. |
| `elastic` | Tail overshoots past its resting point and springs back — comedic/UI-style motion. |
| `suck_in` | Long, tense, near-frozen build-up followed by a near-vertical snap on the beat. |
| `linear_stutter` | No smoothing — hold, jump, hold, jump. Glitch/stop-motion feel. |
| `reverse_time` | Ramp briefly rewinds before snapping forward through the kill frame. |

Full curve shapes and rationale are documented in `CURVES.md`.

## Running it

### Setup

```
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

FFmpeg and ffprobe must be on `PATH` (this project was built against `Gyan.FFmpeg` via winget on Windows). A Python 3.12 environment is recommended — OpenCV/SciPy/librosa wheels are not always available for the very latest Python.

Drop your gameplay clips and exactly one music track into `media/raw_clips/`.

### Headless CLI

```
cd backend
python pipeline.py [output_path]
```

Runs the full pipeline against `media/raw_clips/` using the curve set in `config.py` (`CONFIG.selected_curve`) and writes the montage to `media/output/montage_output.mp4` (or the path you pass).

### Web dashboard

```
cd backend
uvicorn server:app --reload
```

Then open `http://127.0.0.1:8000`. The dashboard lets you:

- **See the clip queue** — every raw clip with duration/fps, plus the detected audio track.
- **Preview a clip** or scrub the **onset waveform**, with detected beat-drop markers overlaid.
- **Pick a velocity curve** from the 7 profiles, each shown as a live sparkline of its actual ramp/tail shape.
- **Render** the montage and watch live progress (stage, percent, log) as it works through detection, audio analysis, and per-chunk rendering.
- **Open the finished output** directly from the browser once the render completes.

The dashboard is a thin FastAPI layer (`server.py`) over the same `pipeline.py`/`render_engine.py` used by the CLI — it doesn't reimplement any pipeline logic, just adds progress callbacks and REST endpoints.

## Project structure

```
backend/
  config.py          Global settings: paths, resolution/fps, selected curve, hw encoder detection
  models.py           ClipMetadata dataclass
  ffmpeg_utils.py      ffprobe wrapper, hw-encoder-with-fallback ffmpeg runner, AV1->H.264 normalization for OpenCV
  audio_engine.py      Beat-drop detection (librosa)
  cv_engine.py         Kill-frame detection (OpenCV template matching)
  sync_engine.py        Beat assignment + chunk timeline planning
  curve_engine.py       Velocity curve profiles + frame-remap math
  render_engine.py      Per-chunk rendering (hold/ramp/impact/tail), concat, audio mux
  cleanup.py            Temp workspace purge
  pipeline.py            End-to-end CLI entrypoint, with optional progress callbacks
  server.py              FastAPI backend for the web dashboard
  assets/kill_template.png   Reference crop of the kill-confirm UI icon used for CV matching
frontend/              Dashboard UI (HTML/CSS/vanilla JS, no build step)
media/                  User media & working data (gitignored in full)
  raw_clips/              Input clips + audio track
  temp_workspace/          Intermediate render artifacts, auto-purged after a successful run
  output/                  Final rendered montages
```

## Known limitations

- `cv_engine.py`'s kill detector is tuned to one specific game's kill-confirm UI element (`assets/kill_template.png`); using it with different footage requires swapping the template and adjusting `REGION_FRACTIONS`.
- Multi-clip cascading timeline/beat-drift logic in `sync_engine.build_chunk_plan` is implemented but has only been exercised against a single test clip.
- `minterpolate`'s motion interpolation is CPU-bound and dominates render time (~100s per ~8s clip even with NVENC); expect render time to scale roughly linearly with clip count.
