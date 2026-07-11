# `curves.md`: Velocity & Time-Remapping Profiles

This document defines the mathematical shapes and flow profiles used by the Time-Remapping Engine.

## Velocity Curves (Speed Graphs)

These curves represent the speed of the clip over time. A flat line at the top means fast-forward; a flat line near the bottom means slow-motion.

### 1. The Standard "Kill Sync"

**Use Case:** The baseline curve for 90% of routine montage kills.
**Flow:** Aggressive speed ramp through dead space -> Maximum velocity right before the shot -> Instant drop to 20% on the exact frame of the kill -> Slow-motion tail.

```text
Speed
  ^       /\
  |      /  \  <- Rapid speed ramp into the action
  |     /    \
  |    /      \_______________________  <- Slow-mo screen pump/flow tail (25% speed)
  +-------------------------------------> Time
             ^ Kill Frame

```

### 2. The "Heartbeat" / Double Pump

**Use Case:** Rhythmic edits synced to high-BPM trap or EDM music with double bass kicks.
**Flow:** Speeds up for Beat 1 -> dips slightly -> instantly speeds up for Beat 2 -> drops into a smooth slow-motion tail.

```text
Speed
  ^   /\    /\
  |  /  \  /  \
  | /    \/    \
  |/            \_____________ <- Slow-mo tail
  +-------------------------------------> Time
      ^      ^
     Beat 1 Beat 2

```

## Value Curves (Time/Position Graphs)

These curves map output time to input time. A steep line means hyper-speed (covering lots of input frames in few output frames); a flat horizontal line means frozen time.

### 3. The "Fast-Slow-Fast" (S-Curve)

**Use Case:** Hyper-smooth transitions where the action practically pauses on the kill, then rockets into the next clip.
**Flow:** Upward curve (ramp up) -> Flattens out completely on the kill frame (frozen/super slow) -> Curves sharply upward again.

```text
Value
  ^           __--/
  |         / 
  |       /   <- Rockets to next clip
  |     _--   <- Flattens on Kill Frame
  |   /
  | /
  +-------------------------------------> Time

```

### 4. The "Elastic" / Overshoot

**Use Case:** Comedic edits or UI animation montages. The motion springs past its target and settles back.
**Flow:** The time mapping goes slightly past the intended frame, reverses a few frames, and settles.

```text
Value
  ^          _.-'\_
  |       .-'      `-.
  |     .'            `-...___ <- Settles at final value
  |   .'
  | .'
  +-------------------------------------> Time

```

### 5. The "Suck-In" / Anticipation

**Use Case:** Massive beat drops. Creates a feeling of holding your breath.
**Flow:** The clip crawls forward incredibly slowly (building tension), then snaps instantly in a near-vertical line on the beat.

```text
Value
  ^                   /|
  |                  / |
  |                 /  |
  |                /   |
  | ____________.-'    | <- Sudden vertical snap
  +-------------------------------------> Time
    ^ Slow, tense build-up

```

### 6. The "Linear Stutter" / Stepped

**Use Case:** Glitch edits, cyborg movements, or edgy stop-motion styles.
**Flow:** Bypasses smooth interpolation entirely. Holds a frame -> jumps forward -> holds -> jumps forward.

```text
Value
  ^           ____
  |       ____|
  |   ____|
  |___|
  +-------------------------------------> Time

```

### 7. The "Reverse Time" / Rebound

**Use Case:** Showing a missed shot or cool animation, rewinding it slightly, and playing it forward for the actual hit.
**Flow:** Forward motion (up) -> Rewind (down) -> Forward motion (up).

```text
Time Value
  ^             /|
  |            / |
  |   /\      /  |
  |  /  \    /   |
  | /    \  /    |
  |/      \/     |
  +-------------------------------------> Time
       ^ Rewind segment

```