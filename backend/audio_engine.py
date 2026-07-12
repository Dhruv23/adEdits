from __future__ import annotations

import numpy as np
import librosa


def analyze_audio(
    path: str,
    energy_percentile: float = 90.0,
    min_gap: float = 0.5,
) -> list[float]:
    """Find the timestamps of heavy audio transients ("beat drops") in a track.

    Computes the onset envelope, then keeps only onsets whose local RMS
    energy is above `energy_percentile` so quiet hi-hats/ticks are filtered
    out and only high-impact hits remain. Onsets closer together than
    `min_gap` seconds are collapsed, keeping the highest-energy one, so a
    single hit doesn't register as several near-duplicate timestamps.
    """
    y, sr = librosa.load(path, sr=None, mono=True)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)

    rms = librosa.feature.rms(y=y)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)

    onset_rms = np.interp(onset_times, rms_times, rms)
    threshold = np.percentile(rms, energy_percentile)

    heavy_mask = onset_rms >= threshold
    heavy_times = onset_times[heavy_mask]
    heavy_energy = onset_rms[heavy_mask]

    order = np.argsort(heavy_times)
    heavy_times = heavy_times[order]
    heavy_energy = heavy_energy[order]

    beats: list[float] = []
    cluster_start = 0
    for i in range(1, len(heavy_times) + 1):
        if i == len(heavy_times) or heavy_times[i] - heavy_times[i - 1] > min_gap:
            cluster = slice(cluster_start, i)
            best = cluster_start + int(np.argmax(heavy_energy[cluster]))
            beats.append(round(float(heavy_times[best]), 3))
            cluster_start = i

    return beats


if __name__ == "__main__":
    import sys

    beats = analyze_audio(sys.argv[1])
    print(f"Detected {len(beats)} heavy beat drops:")
    print(beats)
