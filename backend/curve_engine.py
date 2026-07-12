from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator

# Each curve is built from two independent value-curve segments:
#   ramp: maps the pre-kill "dead space" (u=0 at segment start, u=1 at the
#         kill frame) to the fraction of available input footage consumed.
#   tail: maps the post-kill "follow-through" (u=0 at the kill frame, u=1 at
#         the end of the tail) similarly.
# In both cases u is normalized OUTPUT progress and v is normalized INPUT
# progress; local slope dv/du is the playback speed multiplier at that point.
# This lets the "instant drop to 20%" moments in CURVES.md fall out naturally
# from the ramp ending fast and the tail starting slow, without needing a
# single discontinuous curve.

Array = np.ndarray


def _pchip(points: list[tuple[float, float]]) -> Callable[[Array], Array]:
    xs, ys = zip(*points)
    interpolator = PchipInterpolator(xs, ys)
    return lambda u: np.clip(interpolator(u), 0.0, 1.0)


def _cubic(points: list[tuple[float, float]], clip: bool = True) -> Callable[[Array], Array]:
    xs, ys = zip(*points)
    interpolator = CubicSpline(xs, ys)
    if clip:
        return lambda u: np.clip(interpolator(u), 0.0, 1.0)
    return lambda u: interpolator(u)


def _stutter(steps: int = 6) -> Callable[[Array], Array]:
    """Bypasses smooth interpolation entirely: holds a frame, then jumps."""
    def fn(u: Array) -> Array:
        step_idx = np.minimum(np.floor(u * steps), steps - 1)
        return step_idx / (steps - 1)
    return fn


@dataclass
class CurveProfile:
    ramp_fn: Callable[[Array], Array]
    tail_fn: Callable[[Array], Array]
    tail_stretch_factor: float  # how many times longer the tail plays vs. its natural (1x) duration


CURVE_PROFILES: dict[str, CurveProfile] = {
    # Aggressive ramp into the kill (speeds up approaching the shot), then an
    # instant drop to ~20% speed with a slow-motion tail that eases back in.
    "kill_sync": CurveProfile(
        ramp_fn=_pchip([(0, 0), (0.3, 0.10), (0.6, 0.35), (0.85, 0.70), (1.0, 1.0)]),
        tail_fn=_pchip([(0, 0), (0.15, 0.03), (0.5, 0.15), (0.8, 0.55), (1.0, 1.0)]),
        tail_stretch_factor=3.0,
    ),
    # Two speed peaks in the dead-space ramp (double pump) before settling
    # into a smooth slow-motion tail.
    "heartbeat": CurveProfile(
        ramp_fn=_pchip([(0, 0), (0.2, 0.18), (0.35, 0.22), (0.55, 0.45), (0.7, 0.5), (1.0, 1.0)]),
        tail_fn=_pchip([(0, 0), (0.2, 0.05), (0.55, 0.2), (0.85, 0.6), (1.0, 1.0)]),
        tail_stretch_factor=2.5,
    ),
    # Ramp decelerates approaching the kill (flattens instead of speeding
    # up), then the tail rockets sharply upward at the end.
    "s_curve": CurveProfile(
        ramp_fn=_pchip([(0, 0), (0.3, 0.35), (0.6, 0.68), (0.85, 0.94), (1.0, 1.0)]),
        tail_fn=_pchip([(0, 0), (0.15, 0.03), (0.3, 0.07), (0.5, 0.18), (0.75, 0.45), (1.0, 1.0)]),
        tail_stretch_factor=2.0,
    ),
    # Tail springs past its resting value and settles back (values above 1
    # are clamped, giving a hold-rebound-settle feel rather than fabricating
    # frames beyond the end of the source footage).
    "elastic": CurveProfile(
        ramp_fn=_pchip([(0, 0), (0.4, 0.3), (0.7, 0.65), (1.0, 1.0)]),
        tail_fn=_cubic([(0, 0), (0.4, 0.85), (0.6, 1.08), (0.8, 0.92), (1.0, 1.0)]),
        tail_stretch_factor=1.5,
    ),
    # Crawls forward incredibly slowly (anticipation), then snaps almost
    # vertically right at the kill frame.
    "suck_in": CurveProfile(
        ramp_fn=_pchip([(0, 0), (0.7, 0.08), (0.85, 0.15), (0.97, 0.6), (1.0, 1.0)]),
        tail_fn=_pchip([(0, 0), (0.15, 0.03), (0.5, 0.15), (0.8, 0.55), (1.0, 1.0)]),
        tail_stretch_factor=2.5,
    ),
    # No smooth interpolation: holds a frame, jumps forward, holds again.
    "linear_stutter": CurveProfile(
        ramp_fn=_stutter(steps=6),
        tail_fn=_stutter(steps=6),
        tail_stretch_factor=1.0,
    ),
    # Rewind: the ramp dips backward (revisiting earlier input frames) before
    # snapping forward through the kill frame.
    "reverse_time": CurveProfile(
        ramp_fn=_cubic([(0, 0.3), (0.25, 0.05), (0.5, 0.15), (0.75, 0.6), (1.0, 1.0)]),
        tail_fn=_pchip([(0, 0), (0.2, 0.05), (0.55, 0.2), (0.85, 0.6), (1.0, 1.0)]),
        tail_stretch_factor=1.5,
    ),
}

DEFAULT_CURVE = "kill_sync"


def tail_output_frames(curve_name: str, tail_input_frames: int) -> int:
    profile = CURVE_PROFILES[curve_name]
    return max(round(tail_input_frames * profile.tail_stretch_factor), 1)


def generate_remap_array(
    curve_name: str,
    trim_start_frame: int,
    kill_frame_index: int,
    clip_total_frames: int,
    ramp_output_frames: int,
) -> Array:
    """Build the RemapArray for one chunk: for every output frame, which
    (possibly fractional) input frame to sample. The ramp portion always has
    exactly `ramp_output_frames` frames (preserving the beat-lock established
    by sync_engine); the tail portion's length is derived from the curve's
    own stretch factor to produce a genuine slow-motion tail.
    """
    profile = CURVE_PROFILES[curve_name]

    ramp_input_span = kill_frame_index - trim_start_frame
    ramp_output_frames = max(ramp_output_frames, 1)
    ramp_u = np.linspace(0, 1, ramp_output_frames, endpoint=False)
    ramp_v = profile.ramp_fn(ramp_u)
    ramp_frames = trim_start_frame + ramp_v * ramp_input_span

    tail_input_span = clip_total_frames - kill_frame_index
    n_tail = tail_output_frames(curve_name, tail_input_span)
    tail_u = np.linspace(0, 1, n_tail, endpoint=True)
    tail_v = profile.tail_fn(tail_u)
    tail_frames = kill_frame_index + tail_v * tail_input_span

    return np.concatenate([ramp_frames, tail_frames])


def piecewise_segments(
    curve_name: str,
    is_tail: bool,
    input_start_frame: float,
    input_span: float,
    output_frames: int,
    num_pieces: int = 6,
) -> list[tuple[int, int, int]]:
    """Approximate a ramp or tail's non-linear curve as `num_pieces` locally
    -linear pieces, each renderable with a single (constant-ratio) ffmpeg
    setpts/minterpolate pass. Returns a list of
    (output_frame_count, input_frame_start, input_frame_count) tuples.
    """
    profile = CURVE_PROFILES[curve_name]
    fn = profile.tail_fn if is_tail else profile.ramp_fn

    num_pieces = max(1, min(num_pieces, output_frames, max(round(input_span), 1)))
    u_bounds = np.linspace(0, 1, num_pieces + 1)
    v_bounds = fn(u_bounds)

    pieces = []
    prev_out = 0
    prev_in = input_start_frame
    for i in range(num_pieces):
        out_end = output_frames if i == num_pieces - 1 else round((i + 1) / num_pieces * output_frames)
        out_count = max(out_end - prev_out, 1)

        in_end = input_start_frame + v_bounds[i + 1] * input_span
        in_count = max(round(in_end - prev_in), 1)

        pieces.append((out_count, round(prev_in), in_count))
        prev_out = out_end
        prev_in = in_end

    return pieces
