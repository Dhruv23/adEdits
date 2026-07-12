from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from audio_engine import analyze_audio
from cleanup import purge_temp_workspace
from config import CONFIG
from cv_engine import build_clip_metadata
from render_engine import build_montage
from sync_engine import assign_beats, build_chunk_plan

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}

# stage, message, done, total (done/total are only populated for stages that
# have a natural sub-progress: detect scans clips one at a time, render
# renders chunks one at a time).
ProgressFn = Callable[[str, str, int | None, int | None], None]


def run(
    raw_clips_dir: Path,
    output_path: Path,
    curve_name: str | None = None,
    on_progress: ProgressFn | None = None,
    clip_order: list[str] | None = None,
    beat_times: list[float] | None = None,
    metadata_cache: dict[str, ClipMetadata] | None = None,
) -> Path:
    """Run the full pipeline.

    By default, clips are taken in sorted filename order and assigned the
    first N chronological beats. Pass `clip_order` (filenames, in assignment
    order) and matching `beat_times` (strictly ascending seconds) to instead
    use an explicit user-chosen clip-to-beat mapping, e.g. from the web
    dashboard's Clip Order UI. `metadata_cache` lets a caller (e.g. the
    dashboard, which already ran detection for the timeline preview) hand in
    already-computed `ClipMetadata` so kill detection isn't redone here.
    """
    def report(stage: str, message: str, done: int | None = None, total: int | None = None) -> None:
        print(message)
        if on_progress:
            on_progress(stage, message, done, total)

    audio_files = sorted(p for p in raw_clips_dir.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    if not audio_files:
        raise FileNotFoundError(f"No audio track found in {raw_clips_dir}")
    audio_path = audio_files[0]

    if clip_order is not None:
        video_files = [raw_clips_dir / name for name in clip_order]
        missing = [p for p in video_files if not p.exists()]
        if missing:
            raise FileNotFoundError(f"Clip(s) not found: {', '.join(p.name for p in missing)}")
    else:
        video_files = sorted(p for p in raw_clips_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)
    if not video_files:
        raise FileNotFoundError(f"No raw clips found in {raw_clips_dir}")

    report("ingest", f"Found {len(video_files)} clip(s) and audio track {audio_path.name}")

    clips = []
    for i, video_path in enumerate(video_files, start=1):
        cached = metadata_cache.get(video_path.name) if metadata_cache else None
        if cached is not None:
            report("detect", f"Using cached detection for {video_path.name} ({i}/{len(video_files)})", i, len(video_files))
            metadata = cached
        else:
            report("detect", f"Scanning {video_path.name} for kill frame ({i}/{len(video_files)})", i, len(video_files))
            metadata = build_clip_metadata(video_path)
        if metadata is None:
            report("detect", f"[INVALID] Skipping {video_path.name} (no kill UI detected)", i, len(video_files))
            continue
        clips.append(metadata)

    if not clips:
        raise RuntimeError("No valid clips (with a detected kill) to build a montage from")

    # Match output resolution to the source instead of a hardcoded default —
    # use the highest-resolution clip so no clip is downscaled from its
    # native quality (smaller clips get upscaled+letterboxed by render_engine
    # instead, which only matters for mixed-resolution clip sets).
    CONFIG.target_resolution = max(((c.width, c.height) for c in clips), key=lambda wh: wh[0] * wh[1])
    report("detect", f"Output resolution set to {CONFIG.target_resolution[0]}x{CONFIG.target_resolution[1]} (from source)")

    if beat_times is not None:
        if len(beat_times) != len(video_files):
            raise ValueError(f"beat_times length ({len(beat_times)}) must match clip_order length ({len(video_files)})")
        # Keep only the beats for clips that survived detection, in the same order as `clips`.
        valid_names = {c.filepath for c in clips}
        assigned = [b for p, b in zip(video_files, beat_times) if str(p) in valid_names]
        beats_for_clips = assigned
    else:
        report("audio", f"Analyzing {audio_path.name} for beat drops")
        detected_beats = analyze_audio(str(audio_path))
        if len(detected_beats) < len(clips):
            raise RuntimeError(f"Not enough beats ({len(detected_beats)}) for {len(clips)} valid clips")
        beats_for_clips = detected_beats[: len(clips)]

    assign_beats(clips, beats_for_clips)
    chunks = build_chunk_plan(clips, curve_name=curve_name or CONFIG.selected_curve)

    def on_chunk(done: int, total: int) -> None:
        report("render", f"Rendering chunk {done}/{total}", done, total)

    report("render", f"Rendering {len(chunks)} chunk(s)", 0, len(chunks))
    result = build_montage(chunks, audio_path, output_path, on_chunk_progress=on_chunk)

    report("cleanup", "Purging temp workspace")
    purge_temp_workspace()
    report("done", f"Montage written to: {result}")
    return result


if __name__ == "__main__":
    out = run(CONFIG.raw_clips_dir, Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG.output_dir / "montage_output.mp4")
    print(f"Montage written to: {out}")
