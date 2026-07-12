from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from models import ClipMetadata

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = PROJECT_ROOT / "assets" / "kill_template.png"

# The kill-confirm skull sits dead-center on the crosshair. Region is stored
# as fractions of frame width/height so detection works across resolutions.
REGION_FRACTIONS = (0.4635, 0.7407, 0.0729, 0.1296)  # x, y, w, h

# The kill-confirm skull animates in (scale/fade), so match confidence
# fluctuates for ~30 frames after it first appears before settling at a
# stable plateau. MATCH_THRESHOLD confirms the icon is clearly present
# (used to locate it during the coarse scan); the lower PRESENCE_THRESHOLD
# is used when walking backward through the animation so a mid-animation
# dip doesn't get mistaken for the icon not being there yet.
MATCH_THRESHOLD = 0.6
PRESENCE_THRESHOLD = 0.2
COARSE_STEP = 30


def _region_for_frame(frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x_frac, y_frac, w_frac, h_frac = REGION_FRACTIONS
    x = int(x_frac * frame_w)
    y = int(y_frac * frame_h)
    w = int(w_frac * frame_w)
    h = int(h_frac * frame_h)
    return x, y, w, h


def _match_score(frame: np.ndarray, template_gray: np.ndarray, region: tuple[int, int, int, int]) -> float:
    x, y, w, h = region
    crop = frame[y:y + h, x:x + w]
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    crop_gray = cv2.resize(crop_gray, (template_gray.shape[1], template_gray.shape[0]))
    result = cv2.matchTemplate(crop_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def find_kill_frame(clip_path: Path, template_path: Path = DEFAULT_TEMPLATE) -> int | None:
    """Find the exact frame index where the kill-confirm skull first appears.

    Scans backward from the end of the clip in large increments until the
    skull is found, then scans frame-by-frame within that window to pin
    down its exact first frame. Returns None if no kill UI is ever found.
    """
    template = cv2.imread(str(template_path))
    if template is None:
        raise FileNotFoundError(f"Template not found: {template_path}")
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    cap = cv2.VideoCapture(str(clip_path))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    region = _region_for_frame(frame_w, frame_h)

    def read_frame(idx: int) -> np.ndarray | None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        return frame if ok else None

    # Coarse backward scan.
    coarse_idx = total_frames - 1
    found_at = None
    while coarse_idx >= 0:
        frame = read_frame(coarse_idx)
        if frame is not None and _match_score(frame, template_gray, region) >= MATCH_THRESHOLD:
            found_at = coarse_idx
            break
        coarse_idx -= COARSE_STEP

    if found_at is None:
        cap.release()
        return None

    # Fine-grained backward scan to find the exact first frame the skull
    # starts appearing. Uses PRESENCE_THRESHOLD (not MATCH_THRESHOLD) so the
    # pop-in animation's confidence dips don't cut the scan short.
    first_match = found_at
    idx = found_at - 1
    while idx >= 0:
        frame = read_frame(idx)
        if frame is not None and _match_score(frame, template_gray, region) >= PRESENCE_THRESHOLD:
            first_match = idx
            idx -= 1
        else:
            break

    cap.release()
    return first_match


def build_clip_metadata(clip_path: Path) -> ClipMetadata | None:
    """Probe a clip and locate its kill frame; returns None if invalid (no kill found)."""
    from config import CONFIG
    from ffmpeg_utils import normalize_for_cv, probe_clip

    metadata = probe_clip(clip_path)

    cv_ready_path = normalize_for_cv(clip_path, CONFIG.temp_workspace_dir)
    kill_frame_index = find_kill_frame(cv_ready_path)
    if kill_frame_index is None:
        return None

    metadata.kill_frame_index = kill_frame_index
    return metadata


if __name__ == "__main__":
    import sys

    result = build_clip_metadata(Path(sys.argv[1]))
    if result is None:
        print("[INVALID] No kill UI detected in clip")
    else:
        print(result)
