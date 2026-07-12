from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from config import CONFIG
from curve_engine import piecewise_segments
from ffmpeg_utils import normalize_for_cv, run_ffmpeg_encode
from sync_engine import ChunkPlan

MINTERPOLATE_ARGS = "mi_mode=mci:mc_mode=aobmc:me_mode=bidir"
IMPACT_OUTPUT_FRAMES = 6  # ~0.1s pump/blur flash on the kill frame


def _fit_filter(width: int, height: int) -> str:
    """Scale into `width`x`height` with a high-quality resampler, preserving
    aspect ratio (letterboxed) instead of a naive stretch — a plain
    `scale=w:h` distorts any clip whose native aspect ratio doesn't exactly
    match the montage's output resolution."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def _extract_single_frame(cv_ready_path: Path, frame_index: int, fps: float, out_png: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(cv_ready_path),
            "-ss", str(frame_index / fps),
            "-frames:v", "1",
            "-update", "1",
            str(out_png),
        ],
        capture_output=True, text=True, check=True,
    )
    return out_png


def _render_hold(image_path: Path, out_frames: int, resolution: tuple[int, int], out_path: Path) -> Path:
    width, height = resolution
    return run_ffmpeg_encode(
        [
            "-loop", "1", "-i", str(image_path),
            "-t", str(out_frames / CONFIG.target_fps),
            "-r", str(CONFIG.target_fps),
            "-vf", _fit_filter(width, height),
            "-an",
        ],
        ["-frames:v", str(out_frames)],
        out_path,
    )


def _render_impact(cv_ready_path: Path, kill_frame_index: int, fps_in: float, resolution: tuple[int, int], out_path: Path) -> Path:
    """A brief freeze-frame pump (5% zoom) + directional blur right on the
    kill frame, per PLAN.md Phase 5's impact VFX."""
    width, height = resolution
    frame_png = out_path.parent / f"{out_path.stem}_src.png"
    _extract_single_frame(cv_ready_path, kill_frame_index, fps_in, frame_png)

    zoom_w = round(width * 1.05 / 2) * 2
    zoom_h = round(height * 1.05 / 2) * 2
    vf = (
        f"{_fit_filter(width, height)},"
        f"scale={zoom_w}:{zoom_h}:flags=lanczos,"
        f"crop={width}:{height},"
        "tmix=frames=3:weights='1 2 1'"
    )
    run_ffmpeg_encode(
        [
            "-loop", "1", "-i", str(frame_png),
            "-t", str(IMPACT_OUTPUT_FRAMES / CONFIG.target_fps),
            "-r", str(CONFIG.target_fps),
            "-vf", vf,
            "-an",
        ],
        ["-frames:v", str(IMPACT_OUTPUT_FRAMES)],
        out_path,
    )
    frame_png.unlink(missing_ok=True)
    return out_path


def _render_piece(
    cv_ready_path: Path,
    in_start_frame: int,
    in_count: int,
    out_count: int,
    fps_in: float,
    resolution: tuple[int, int],
    out_path: Path,
) -> Path:
    """Render one locally-linear piece of a ramp/tail. Slowdowns (out_count
    > in_count) go through minterpolate to synthesize real in-between motion
    instead of duplicating frames; speedups use a plain fps conversion."""
    width, height = resolution
    target_fps = CONFIG.target_fps

    if in_count <= 1:
        frame_png = out_path.parent / f"{out_path.stem}_src.png"
        _extract_single_frame(cv_ready_path, in_start_frame, fps_in, frame_png)
        result = _render_hold(frame_png, out_count, resolution, out_path)
        frame_png.unlink(missing_ok=True)
        return result

    ratio = (out_count / target_fps) / (in_count / fps_in)
    filters = [f"setpts=PTS-STARTPTS", f"setpts={ratio}*PTS"]
    if out_count > in_count:
        filters.append(f"minterpolate=fps={target_fps}:{MINTERPOLATE_ARGS}")
    else:
        filters.append(f"fps={target_fps}")
    filters.append(_fit_filter(width, height))

    # -ss as an INPUT option (before -i): seeks before decoding, so the
    # PTS-rewriting setpts filter operates on a clean 0-based timeline
    # instead of colliding with an output-side trim on a rescaled timeline.
    # Deliberately no -t bound: minterpolate needs to read a little past the
    # nominal segment to have lookahead frames to interpolate against near
    # the end (bounding the read tightly starved it and truncated output
    # short of the requested frame count); -frames:v below caps the actual
    # output length precisely regardless of how far it reads ahead.
    return run_ffmpeg_encode(
        ["-ss", str(in_start_frame / fps_in), "-i", str(cv_ready_path), "-vf", ",".join(filters), "-an"],
        ["-frames:v", str(out_count)],
        out_path,
    )


def render_chunk(chunk: ChunkPlan, out_path: Path) -> Path:
    """Render one chunk: an optional literal hold, a curve-paced ramp up to
    the kill frame, a brief impact flash on the kill frame, and a
    curve-paced, minterpolate-smoothed slow-motion tail. Pieces are rendered
    independently then concatenated."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clip = chunk.clip
    cv_ready_path = normalize_for_cv(Path(clip.filepath), CONFIG.temp_workspace_dir)
    resolution = CONFIG.target_resolution

    pieces_dir = out_path.parent / f"{out_path.stem}_pieces"
    pieces_dir.mkdir(parents=True, exist_ok=True)
    piece_paths: list[Path] = []

    if chunk.hold_output_frames > 0:
        hold_png = pieces_dir / "hold_src.png"
        _extract_single_frame(cv_ready_path, chunk.trim_start_frame, clip.fps, hold_png)
        hold_path = pieces_dir / "piece_hold.mp4"
        _render_hold(hold_png, chunk.hold_output_frames, resolution, hold_path)
        hold_png.unlink(missing_ok=True)
        piece_paths.append(hold_path)

    ramp_input_span = clip.kill_frame_index - chunk.trim_start_frame
    ramp_pieces = piecewise_segments(
        chunk.curve_name, is_tail=False,
        input_start_frame=chunk.trim_start_frame, input_span=ramp_input_span,
        output_frames=chunk.ramp_output_frames,
    )
    for i, (out_count, in_start, in_count) in enumerate(ramp_pieces):
        piece_path = pieces_dir / f"piece_ramp_{i:02d}.mp4"
        _render_piece(cv_ready_path, in_start, in_count, out_count, clip.fps, resolution, piece_path)
        piece_paths.append(piece_path)

    impact_path = pieces_dir / "piece_impact.mp4"
    _render_impact(cv_ready_path, clip.kill_frame_index, clip.fps, resolution, impact_path)
    piece_paths.append(impact_path)

    tail_input_span = clip.total_frames - clip.kill_frame_index
    tail_pieces = piecewise_segments(
        chunk.curve_name, is_tail=True,
        input_start_frame=clip.kill_frame_index, input_span=tail_input_span,
        output_frames=chunk.tail_output_frames,
    )
    for i, (out_count, in_start, in_count) in enumerate(tail_pieces):
        piece_path = pieces_dir / f"piece_tail_{i:02d}.mp4"
        _render_piece(cv_ready_path, in_start, in_count, out_count, clip.fps, resolution, piece_path)
        piece_paths.append(piece_path)

    concat_chunks(piece_paths, out_path)

    for p in piece_paths:
        p.unlink(missing_ok=True)
    (out_path.parent / "concat_list.txt").unlink(missing_ok=True)
    pieces_dir.rmdir()

    return out_path


def concat_chunks(chunk_paths: list[Path], out_path: Path) -> Path:
    """Stitch pre-rendered chunks together in order (same codec/res/fps, so
    a stream copy is enough — no re-encoding)."""
    concat_list = out_path.parent / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in chunk_paths),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(out_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return out_path


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> Path:
    """Overlay the analyzed audio track onto the combined video, trimmed to
    the video's duration."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(out_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return out_path


def build_montage(
    chunks: list[ChunkPlan],
    audio_path: Path,
    output_path: Path,
    on_chunk_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Render each chunk, concatenate them, and mux in the audio track."""
    workspace = CONFIG.temp_workspace_dir
    chunk_paths = []
    for i, chunk in enumerate(chunks):
        chunk_path = workspace / f"chunk_{i:03d}.mp4"
        render_chunk(chunk, chunk_path)
        chunk_paths.append(chunk_path)
        if on_chunk_progress:
            on_chunk_progress(i + 1, len(chunks))

    combined_path = workspace / "combined_video.mp4"
    concat_chunks(chunk_paths, combined_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mux_audio(combined_path, audio_path, output_path)
    return output_path
