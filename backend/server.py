from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from audio_engine import analyze_audio
from config import CONFIG
from curve_engine import CURVE_PROFILES, DEFAULT_CURVE
from cv_engine import build_clip_metadata
from ffmpeg_utils import probe_clip
from models import ClipMetadata
from pipeline import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from pipeline import run as run_pipeline
from sync_engine import assign_beats, build_chunk_plan

app = FastAPI(title="adEdits Montage Suite")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Kill-frame detection is a real cost (OpenCV backward scan + AV1->H.264
# transcode), so cache each clip's ClipMetadata by filename, invalidated on
# mtime change. Shared between /api/plan (timeline preview) and the actual
# render job so detection never runs twice for the same clip.
_metadata_lock = threading.Lock()
_metadata_cache: dict[str, tuple[float, ClipMetadata | None]] = {}


def _get_or_detect(name: str) -> ClipMetadata | None:
    path = CONFIG.raw_clips_dir / name
    mtime = path.stat().st_mtime
    with _metadata_lock:
        cached = _metadata_cache.get(name)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    metadata = build_clip_metadata(path)
    with _metadata_lock:
        _metadata_cache[name] = (mtime, metadata)
    return metadata

# Coarse percent budget per pipeline stage; stages with (done, total) progress
# (detect, render) interpolate across their span, the rest just jump to base.
STAGE_BASE = {"ingest": 0, "detect": 5, "audio": 25, "render": 35, "cleanup": 95, "done": 100}
STAGE_SPAN = {"detect": 20, "render": 60}

_state_lock = threading.Lock()
job_state: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "stage": None,
    "message": None,
    "percent": 0,
    "curve": None,
    "output_path": None,
    "error": None,
    "log": [],
    "started_at": None,
    "finished_at": None,
}


class Assignment(BaseModel):
    clip: str
    beat: float


class RenderRequest(BaseModel):
    curve: str | None = None
    assignments: list[Assignment] | None = None


class PlanRequest(BaseModel):
    curve: str | None = None
    assignments: list[Assignment]


def _run_job(curve_name: str, assignments: list[Assignment] | None) -> None:
    with _state_lock:
        job_state.update(
            status="running", stage="ingest", message="Starting…", percent=0,
            curve=curve_name, error=None, output_path=None, log=[], started_at=time.time(), finished_at=None,
        )

    def on_progress(stage: str, message: str, done: int | None, total: int | None) -> None:
        base = STAGE_BASE.get(stage, 0)
        span = STAGE_SPAN.get(stage, 0)
        percent = base + span * (done / total) if done is not None and total else base
        with _state_lock:
            job_state.update(stage=stage, message=message, percent=round(min(percent, 100), 1))
            job_state["log"].append(message)
            job_state["log"] = job_state["log"][-50:]

    clip_order = [a.clip for a in assignments] if assignments else None
    beat_times = [a.beat for a in assignments] if assignments else None
    metadata_cache = {name: meta for name, (_, meta) in _metadata_cache.items()} if assignments else None

    try:
        output_path = CONFIG.output_dir / f"montage_{int(time.time())}.mp4"
        result = run_pipeline(
            CONFIG.raw_clips_dir, output_path, curve_name=curve_name, on_progress=on_progress,
            clip_order=clip_order, beat_times=beat_times, metadata_cache=metadata_cache,
        )
        with _state_lock:
            job_state.update(status="done", percent=100, output_path=str(result), finished_at=time.time())
    except Exception as exc:
        with _state_lock:
            job_state.update(status="error", error=str(exc), finished_at=time.time())


@app.get("/api/config")
def get_config() -> dict:
    return {
        "resolution": list(CONFIG.target_resolution),
        "fps": CONFIG.target_fps,
        "encoder": CONFIG.hw_encoder,
        "selected_curve": CONFIG.selected_curve,
        "raw_clips_dir": str(CONFIG.raw_clips_dir),
        "output_dir": str(CONFIG.output_dir),
    }


@app.get("/api/clips")
def list_clips() -> dict:
    videos = []
    audio = []
    if CONFIG.raw_clips_dir.exists():
        for p in sorted(CONFIG.raw_clips_dir.iterdir()):
            suffix = p.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                try:
                    meta = probe_clip(p)
                    videos.append({
                        "name": p.name,
                        "fps": round(meta.fps, 2),
                        "total_frames": meta.total_frames,
                        "duration": round(meta.total_frames / meta.fps, 2),
                        "url": f"/media/raw/{p.name}",
                    })
                except Exception:
                    videos.append({"name": p.name, "error": "probe failed", "url": f"/media/raw/{p.name}"})
            elif suffix in AUDIO_EXTENSIONS:
                audio.append({"name": p.name, "url": f"/media/raw/{p.name}"})
    return {"videos": videos, "audio": audio}


@app.get("/api/curves")
def list_curves() -> dict:
    u = np.linspace(0, 1, 40)
    curves = []
    for name, profile in CURVE_PROFILES.items():
        curves.append({
            "name": name,
            "label": name.replace("_", " ").title(),
            "ramp": np.round(profile.ramp_fn(u), 4).tolist(),
            "tail": np.round(profile.tail_fn(u), 4).tolist(),
        })
    return {"curves": curves, "default": DEFAULT_CURVE}


@app.get("/api/waveform")
def waveform(file: str) -> dict:
    path = CONFIG.raw_clips_dir / file
    if not path.exists() or path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(404, "audio file not found")

    y, sr = librosa.load(str(path), sr=None, mono=True)
    hop = max(len(y) // 400, 1)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    peak = float(rms.max()) or 1.0
    beats = analyze_audio(str(path))

    return {
        "peaks": np.round(rms / peak, 4).tolist(),
        "beats": beats,
        "duration": round(len(y) / sr, 3),
    }


@app.post("/api/plan")
def get_plan(payload: PlanRequest) -> dict:
    curve_name = payload.curve or CONFIG.selected_curve
    if curve_name not in CURVE_PROFILES:
        raise HTTPException(400, f"Unknown curve '{curve_name}'")
    if not payload.assignments:
        return {"chunks": [], "total_duration": 0}

    clips: list[ClipMetadata] = []
    beats: list[float] = []
    for a in payload.assignments:
        metadata = _get_or_detect(a.clip)
        if metadata is None:
            raise HTTPException(400, f"No kill detected in '{a.clip}'; it can't be placed on the timeline")
        clips.append(metadata)
        beats.append(a.beat)

    try:
        assign_beats(clips, beats)
        chunks = build_chunk_plan(clips, curve_name=curve_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "chunks": [
            {
                "clip": Path(c.clip.filepath).name,
                "output_start": round(c.output_start, 3),
                "hold_seconds": round(c.hold_seconds, 3),
                "ramp_output_frames": c.ramp_output_frames,
                "tail_output_frames": c.tail_output_frames,
                "output_duration": round(c.output_duration, 3),
                "kill_time_in_output": round(c.kill_time_in_output, 3),
                "assigned_beat": c.clip.target_audio_beat,
            }
            for c in chunks
        ],
        "total_duration": round(chunks[-1].output_start + chunks[-1].output_duration, 3) if chunks else 0,
    }


@app.post("/api/render")
def start_render(payload: RenderRequest) -> dict:
    with _state_lock:
        if job_state["status"] == "running":
            raise HTTPException(409, "A render is already in progress")

    curve_name = payload.curve or CONFIG.selected_curve
    if curve_name not in CURVE_PROFILES:
        raise HTTPException(400, f"Unknown curve '{curve_name}'")

    thread = threading.Thread(target=_run_job, args=(curve_name, payload.assignments), daemon=True)
    thread.start()
    return {"status": "started", "curve": curve_name}


@app.get("/api/render/status")
def render_status() -> dict:
    with _state_lock:
        return dict(job_state)


@app.get("/api/output")
def list_output() -> dict:
    files = []
    if CONFIG.output_dir.exists():
        for p in sorted(CONFIG.output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
            files.append({"name": p.name, "url": f"/media/output/{p.name}", "mtime": p.stat().st_mtime})
    return {"files": files}


if CONFIG.raw_clips_dir.exists():
    app.mount("/media/raw", StaticFiles(directory=str(CONFIG.raw_clips_dir)), name="raw")
if CONFIG.output_dir.exists():
    app.mount("/media/output", StaticFiles(directory=str(CONFIG.output_dir)), name="output")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
