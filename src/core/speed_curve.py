"""Pure-Python speed interpolation — no Qt dependency.

Used by both the UI (AutomationLane delegates to this) and the export
pipeline (ffmpeg_runner calls this from worker threads). Separating the
math from the QWidget avoids creating GUI objects on background threads.
"""


class SpeedCurve:
    """Linear interpolation over speed keyframes."""

    def __init__(self, keyframes: list[tuple[int, float]], base_speed: float = 1.0):
        self._keyframes = sorted(keyframes, key=lambda k: k[0])
        self._base_speed = base_speed

    def get_speed_at(self, time_ms: int) -> float:
        if not self._keyframes:
            return self._base_speed

        first_t, first_s = self._keyframes[0]
        if time_ms <= first_t:
            if first_t == 0:
                return first_s
            frac = time_ms / first_t
            return self._base_speed + (first_s - self._base_speed) * frac

        for i in range(len(self._keyframes) - 1):
            t1, s1 = self._keyframes[i]
            t2, s2 = self._keyframes[i + 1]
            if t1 <= time_ms <= t2:
                span = t2 - t1
                if span == 0:
                    return s2
                frac = (time_ms - t1) / span
                return s1 + (s2 - s1) * frac

        return self._keyframes[-1][1]
