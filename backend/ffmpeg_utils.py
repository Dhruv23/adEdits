from __future__ import annotations

import json
import subprocess
from pathlib import Path

from config import CONFIG
from models import ClipMetadata


def _parse_r_frame_rate(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den)
    return float(rate)


def probe_clip(path: Path) -> ClipMetadata:
    """Run ffprobe on a clip and return its ClipMetadata."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)

    video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    fps = _parse_r_frame_rate(video_stream["r_frame_rate"])
    duration = float(data["format"]["duration"])
    total_frames = round(duration * fps)

    return ClipMetadata(
        filepath=str(path), fps=fps, total_frames=total_frames,
        width=int(video_stream["width"]), height=int(video_stream["height"]),
    )


# Every encode in this pipeline (the CV-normalize pass AND every rendered
# piece) should be visually indistinguishable from the source — render time
# is not a constraint here, so each encoder is pushed to its highest-quality,
# effectively-unbounded-bitrate mode rather than ffmpeg's default (which
# targets a modest bitrate/CRF and would compound across the multiple
# re-encode generations every chunk goes through).
_QUALITY_ARGS: dict[str, list[str]] = {
    "libx264": ["-preset", "veryslow", "-crf", "14"],
    "h264_nvenc": ["-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "14", "-b:v", "0"],
    "h264_amf": ["-quality", "quality", "-rc", "cqp", "-qp_i", "14", "-qp_p", "14", "-qp_b", "14"],
    "h264_videotoolbox": ["-q:v", "20"],
}


def run_ffmpeg_encode(input_args: list[str], output_args: list[str], out_path: Path) -> Path:
    """Run ffmpeg with the configured hardware encoder (config.py's
    CONFIG.hw_encoder) at that encoder's highest-quality settings, falling
    back to libx264 if the hardware encoder fails at runtime — e.g. a
    driver/session issue even though it was detected as available — so a
    flaky GPU never crashes the pipeline.
    """
    encoders = [CONFIG.hw_encoder] if CONFIG.hw_encoder == "libx264" else [CONFIG.hw_encoder, "libx264"]

    last_stderr = ""
    for encoder in encoders:
        quality_args = _QUALITY_ARGS.get(encoder, [])
        cmd = ["ffmpeg", "-y", *input_args, "-c:v", encoder, *quality_args, "-pix_fmt", "yuv420p", *output_args, str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return out_path
        last_stderr = result.stderr
        print(f"[WARN] ffmpeg encoder '{encoder}' failed for {out_path.name}, falling back if possible")

    raise RuntimeError(f"ffmpeg encoding failed for all encoders {encoders}: {last_stderr[-500:]}")


def normalize_for_cv(path: Path, temp_workspace_dir: Path) -> Path:
    """Transcode a clip to H.264 for reliable OpenCV frame-by-frame decoding.

    Some capture tools record AV1 profiles that OpenCV's bundled decoder
    (libaom) can't decode even though system ffmpeg (via libdav1d) handles
    them fine. Re-encoding through ffmpeg first sidesteps that gap.
    """
    temp_workspace_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_workspace_dir / f"cv_{path.stem}.mp4"
    if out_path.exists():
        return out_path
    return run_ffmpeg_encode(["-i", str(path)], ["-an"], out_path)


def cut_clip(path: Path, start: float, duration: float, out_path: Path) -> Path:
    """Cut a short test segment from a clip using ffmpeg."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return run_ffmpeg_encode(
        ["-ss", str(start), "-i", str(path), "-t", str(duration)],
        ["-c:a", "aac"],
        out_path,
    )
