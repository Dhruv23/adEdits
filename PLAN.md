# `plan.md`: Automated Montage & Time-Remapping Suite

## 1. System Architecture Overview

This software is a Python-based pipeline that automates beat detection, visual kill identification, complex time-remapping, and final rendering.

Borrowing from best practices in automated Python rendering, the system is designed to be highly modular. It processes the video in **isolated chunks** (one kill per chunk) rather than attempting to generate a massive, failure-prone FFmpeg command for the entire montage at once.

## 2. Core Pipeline Modules

### Phase 1: Global Configuration & File Handling

**Goal:** Establish a single "control center" to manage folders, settings, and file organization.

* **The Setup:** A centralized `config.py` file dictates resolution, target FPS, file paths, and the selected velocity curve.
* **Workspace Routing:** Raw media is ingested from a `/raw_clips` folder. Temporary rendering artifacts (like individual mapped frames or audio snippets) are routed to a `/temp_workspace` folder.

### Phase 2: Audio Analysis Engine (Beat Detection)

**Goal:** Map the precise milliseconds of high-impact audio transients.

* **Library:** `librosa` (Python).
* **Process:**
1. Parse the audio file to compute the onset envelope (sudden bursts in volume/energy).
2. Filter these onsets against RMS energy to isolate the "heavy" drops rather than just background hi-hats.
3. **Output:** An array of target timestamps (e.g., `[12.450, 18.200, 24.100]`) where the exact "kill frame" must land.



### Phase 3: Computer Vision Engine (Action Detection)

**Goal:** Identify the exact frame of the kill notification in raw gameplay clips.

* **Library:** `OpenCV` (cv2).
* **Process:**
1. Scan the bottom center of the screen (or top right for the kill feed) using `cv2.matchTemplate()` to search for the specific red skull UI element.
2. To optimize memory and processing speed, scan backward from the end of the clip in larger increments until the UI element is found, then scan frame-by-frame to find the *exact* first frame it appears.
3. **Output:** A dataset pairing each clip with its specific kill frame index.



### Phase 4: Sync Logic & The Time-Remapping Engine

**Goal:** Align the visuals to the audio and mathematically apply the chosen velocity curve.

* **The Duration Checker:** Utilize `ffprobe` (via `subprocess`) to measure exact clip and audio durations down to the millisecond.
* **Curve Generation:**
* Load preset mathematical profiles for your curves (e.g., The "Standard Kill Sync" or the "S-Curve").
* Use `scipy.interpolate` to generate a 1D mapping array. This array dictates which input frame corresponds to which output frame (e.g., output frame 60 pulls input frame 85).


* **The Anchor Point:** The entire mapping array is shifted dynamically so the lowest velocity point (the bottom of the curve) locks perfectly onto the exact timestamp of the audio beat drop.

### Phase 5: Chunk-Based Render & VFX Pipeline

**Goal:** Render the video clip-by-clip, hallucinate missing frames for smooth slow-motion, and compile the final video.

* **Libraries:** `FFmpeg` (via `subprocess` or `ffmpeg-python`), `asyncio`.
* **Process:**
1. **Chunk Rendering:** The system processes one "kill" at a time. It uses the mapping array to extract and sequence frames.
2. **Optical Flow:** Apply FFmpeg’s `minterpolate` filter to generate artificial frames for the ultra-slow-motion tails of the velocity curves, ensuring buttery smooth playback without stuttering.
3. **Impact VFX:** On the exact frame of the kill, apply a sudden 5% scale increase (screen pump) and a minor directional blur.
4. **Stitching:** Concatenate all the individual processed chunks together sequentially.
5. **Muxing:** Overlay the original analyzed audio track onto the combined video file.



### Phase 6: Auto-Cleanup

**Goal:** Prevent hard drive bloat.

* Once the final MP4 is rendered successfully, the `os` module automatically purges the `/temp_workspace` folder, deleting all intermediate chunks and frame arrays.

---

## 3. Development Sequence

1. **Establish the Scaffolding:** Config and CLI.
Build `config.py` to define file paths and video settings. Set up the foundational script to read a clip, measure it with `ffprobe`, cut it, and output a test file.


2. **Build Detection Engines:**
Develop the `librosa` audio analyzer to accurately print beat timestamps. Develop the OpenCV template matcher to consistently identify the exact frame the kill UI appears in raw clips.


3. **Implement Linear Sync (MVP):**
Write logic to match one clip's kill frame exactly to one audio beat timestamp using hard cuts (no speed ramps yet). Establish the chunk-based rendering loop.


4. **Develop the Math Mapping Engine:**
Create the Python class that utilizes `scipy.interpolate` to translate a chosen Bezier curve profile into a concrete frame-mapping array. Apply this mapping during chunk generation.


5. **Integrate Interpolation and VFX:**
Route the mapped chunks through FFmpeg's `minterpolate` to smooth the slow-motion segments. Add the screen pump scale logic at the precise kill frame index.


6. **Finalize Muxing and Cleanup:**
Write the final concatenation block to stitch chunks, merge the master audio track, and trigger the auto-cleanup sequence to delete temporary files.


# `plan.md`: Advanced Implementation & Architecture

## 4. Advanced Data Structures & State Management

To keep the pipeline from collapsing when handling dozens of video files, we need rigid data schemas. Python's `dataclasses` will act as the single source of truth for each chunk.

### The `ClipMetadata` Object

Every video file ingested gets parsed into a Python object before any processing begins.

* `filepath`: String path to the raw video.
* `fps`: Float (e.g., 59.94). Extracted via `ffprobe`.
* `total_frames`: Integer.
* `kill_frame_index`: Integer. Populated by the OpenCV Engine.
* `target_audio_beat`: Float (timestamp in seconds). Populated by the Sync Logic.

### The `RemapArray`

Instead of passing complex mathematical formulas to FFmpeg, the Python Time-Remapping engine will generate a 1D Numpy array for each chunk.

* **Format:** `[1, 2, 4, 7, 11, 11.5, 11.8, 12, 12.1...]`
* **Meaning:** "For Output Frame 1, grab Input Frame 1. For Output Frame 6, grab Input Frame 11.5 (which FFmpeg will interpolate)."

## 5. Performance Optimization Strategy

Video processing is highly CPU/GPU intensive. The naive approach (processing everything in one massive loop) will take hours. We will optimize this using asynchronous processing and hardware acceleration.

### Chunk Parallelism

Since the video is broken into isolated chunks (e.g., Beat 1 to Beat 2 is Chunk A, Beat 2 to Beat 3 is Chunk B), they do not depend on each other during the render phase.

* Use Python's `concurrent.futures.ProcessPoolExecutor` to render multiple video chunks simultaneously across different CPU cores.

### Hardware-Accelerated FFmpeg

Standard FFmpeg relies on the CPU. To make rendering fast, the configuration file must dynamically detect the user's hardware and inject the correct hardware-encoding flags into the FFmpeg subprocess commands.

* **NVIDIA GPUs:** Inject `-c:v h264_nvenc` for lightning-fast H.264 rendering.
* **AMD GPUs:** Inject `-c:v h264_amf`.
* **Apple Silicon (Mac):** Inject `-c:v h264_videotoolbox`.

## 6. Edge Case Handling (The "What Ifs")

* **No Kill Detected:** If OpenCV scans a clip and finds zero red skull UI elements, the system flags the clip as `[INVALID]`, skips it, and pulls the next raw clip from the folder so the pipeline doesn't crash.
* **Clip is Too Short:** If a beat drop requires 4 seconds of buildup, but the raw clip only has 2 seconds of footage before the kill, the Time-Remapping engine will automatically default to a "Hold and Snap" curve (freezing the first frame until the action needs to start) rather than crashing with an `IndexError`.
* **Beat Drought:** If `librosa` detects a 20-second gap between major beats, the Sync Logic will automatically inject b-roll or allow the current clip to play out at normal 100% linear speed to bridge the gap.

---
