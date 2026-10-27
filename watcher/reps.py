"""Count reps from pose landmarks: a joint angle going down past one threshold and back up past
another is one rep. The gap between the thresholds stops a wobble at the bottom counting twice.

Pure Python (no mediapipe import), so it's unit-tested with the coach's tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# MediaPipe pose landmark indices.
L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 23, 24, 25, 26, 27, 28

# exercise: (the two joint triples to measure, angle that counts as "down", angle that's "up")
JOINTS = {
    "pushups": (((L_SHOULDER, L_ELBOW, L_WRIST), (R_SHOULDER, R_ELBOW, R_WRIST)), 100, 150),
    "squats": (((L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE)), 115, 155),
    "lunges": (((L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE)), 115, 155),
}
MIN_VISIBILITY = 0.5


def angle(a, b, c) -> float:
    """Angle at b, in degrees, from three (x, y) points."""
    ab = (a[0] - b[0], a[1] - b[1])
    cb = (c[0] - b[0], c[1] - b[1])
    norm = math.hypot(*ab) * math.hypot(*cb)
    if norm == 0:
        return 180.0
    cos = max(-1.0, min(1.0, (ab[0] * cb[0] + ab[1] * cb[1]) / norm))
    return math.degrees(math.acos(cos))


@dataclass
class RepCounter:
    exercise: str
    count: int = 0
    down: bool = False
    last_angle: float | None = None

    @property
    def countable(self) -> bool:
        return self.exercise in JOINTS

    def update(self, landmarks) -> bool:
        """Feed one frame's landmarks (objects with .x, .y, .visibility). True if a rep just finished."""
        if not self.countable or not landmarks:
            return False
        triples, down_at, up_at = JOINTS[self.exercise]
        angles = []
        for triple in triples:
            points = [landmarks[i] for i in triple]
            if all(getattr(p, "visibility", 1.0) >= MIN_VISIBILITY for p in points):
                angles.append(angle(*[(p.x, p.y) for p in points]))
        if not angles:
            return False
        # The more-bent side: the camera usually sees one side better, and both bend together.
        a = min(angles)
        self.last_angle = a
        if not self.down and a < down_at:
            self.down = True
        elif self.down and a > up_at:
            self.down = False
            self.count += 1
            return True
        return False
