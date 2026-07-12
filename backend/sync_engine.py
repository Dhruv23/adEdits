from __future__ import annotations

from dataclasses import dataclass

from config import CONFIG
from curve_engine import DEFAULT_CURVE
from curve_engine import tail_output_frames as curve_tail_output_frames
from models import ClipMetadata


def assign_beats(clips: list[ClipMetadata], beats: list[float]) -> list[ClipMetadata]:
    """Assign each clip a target audio beat, one beat per clip in order."""
    if len(beats) < len(clips):
        raise ValueError(f"Not enough beats ({len(beats)}) for {len(clips)} clips")
    for prev, cur in zip(beats, beats[1:]):
        if cur <= prev:
            raise ValueError(
                f"Beat assignments must be strictly ascending (got {prev}s then {cur}s); "
                "build_chunk_plan lays clips back-to-back and can't schedule an earlier beat after a later one"
            )
    for clip, beat in zip(clips, beats):
        clip.target_audio_beat = beat
    return clips


@dataclass
class ChunkPlan:
    clip: ClipMetadata
    output_start: float        # when this chunk starts in the final timeline (s)
    hold_seconds: float        # freeze-first-frame duration used to pad a too-short clip
    trim_start_frame: int      # first source frame included, after any hold
    output_duration: float     # total duration this chunk contributes (s)
    curve_name: str            # velocity curve profile applied when rendering
    hold_output_frames: int    # output frame count of the literal frame-freeze (if any)
    ramp_output_frames: int    # output frame count for the curve-paced ramp (beat-locked)
    tail_output_frames: int    # output frame count for the post-kill slow-mo tail

    @property
    def kill_time_in_output(self) -> float:
        return self.output_start + self.hold_seconds + (
            (self.clip.kill_frame_index - self.trim_start_frame) / self.clip.fps
        )


def build_chunk_plan(clips: list[ClipMetadata], curve_name: str = DEFAULT_CURVE) -> list[ChunkPlan]:
    """Lay out clips back-to-back so each kill frame lands exactly on its beat.

    The pre-kill ramp always spans exactly enough output frames to reach the
    beat on time (preserving the hard sync lock); if a clip doesn't have
    enough pre-kill footage to reach its beat, the first frame is held
    (frozen) to fill the gap, per PLAN.md's "Hold and Snap" edge case. The
    post-kill tail's duration is derived from the chosen curve's slow-motion
    stretch factor rather than the source footage's natural length. If the
    previous clip's tail runs past the next beat, sync is allowed to drift
    late rather than crashing (PLAN.md's "don't crash the pipeline" ethos).
    """
    plan: list[ChunkPlan] = []
    timeline = 0.0

    for clip in clips:
        if clip.target_audio_beat is None:
            raise ValueError(f"{clip.filepath} has no assigned beat")

        needed = clip.target_audio_beat - timeline
        if needed < 0:
            print(
                f"[WARN] {clip.filepath}: beat {clip.target_audio_beat}s is earlier than "
                f"timeline position {timeline:.3f}s; sync will drift late by {-needed:.3f}s"
            )
            needed = 0.0

        available = clip.kill_frame_index / clip.fps

        if available >= needed:
            hold_seconds = 0.0
            trim_start_frame = clip.kill_frame_index - round(needed * clip.fps)
        else:
            hold_seconds = needed - available
            trim_start_frame = 0

        # The curve only paces the portion of dead space actually covered by
        # source footage; a literal hold (if any) is prepended separately so
        # "Hold and Snap" is a genuine freeze-frame, not just a slow curve.
        ramp_span_seconds = needed - hold_seconds
        hold_output_frames = max(round(hold_seconds * CONFIG.target_fps), 0)
        ramp_output_frames = max(round(ramp_span_seconds * CONFIG.target_fps), 1)

        tail_input_frames = clip.total_frames - clip.kill_frame_index
        n_tail = curve_tail_output_frames(curve_name, tail_input_frames)
        tail_seconds = n_tail / CONFIG.target_fps

        output_duration = needed + tail_seconds

        plan.append(ChunkPlan(
            clip=clip,
            output_start=timeline,
            hold_seconds=hold_seconds,
            trim_start_frame=trim_start_frame,
            output_duration=output_duration,
            curve_name=curve_name,
            hold_output_frames=hold_output_frames,
            ramp_output_frames=ramp_output_frames,
            tail_output_frames=n_tail,
        ))

        timeline += output_duration

    return plan
