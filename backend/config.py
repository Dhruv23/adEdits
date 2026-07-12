from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = PROJECT_ROOT / "media"


def detect_hw_encoder() -> str:
    """Pick the best available FFmpeg H.264 encoder for this machine."""
    if shutil.which("nvidia-smi"):
        return "h264_nvenc"

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
        encoders = result.stdout
        if "h264_amf" in encoders:
            return "h264_amf"
        if "h264_videotoolbox" in encoders:
            return "h264_videotoolbox"

    return "libx264"


@dataclass
class GlobalConfig:
    raw_clips_dir: Path = field(default_factory=lambda: MEDIA_DIR / "raw_clips")
    temp_workspace_dir: Path = field(default_factory=lambda: MEDIA_DIR / "temp_workspace")
    output_dir: Path = field(default_factory=lambda: MEDIA_DIR / "output")

    # Fallback only — pipeline.run() overwrites this with the highest-resolution
    # input clip's native size once clips are known, so output resolution
    # matches the source instead of forcing an up/downscale.
    target_resolution: tuple[int, int] = (1920, 1080)
    target_fps: float = 59.94

    selected_curve: str = "kill_sync"

    hw_encoder: str = field(default_factory=detect_hw_encoder)


CONFIG = GlobalConfig()
