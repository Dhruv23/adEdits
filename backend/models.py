from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClipMetadata:
    filepath: str
    fps: float
    total_frames: int
    width: int
    height: int
    kill_frame_index: int | None = None
    target_audio_beat: float | None = None
