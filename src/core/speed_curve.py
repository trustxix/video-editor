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
        # Resumable segment index. The export prerender calls get_speed_at once
        # per output sample with monotonically increasing time_ms, so a cursor
        # makes that an O(1)-amortized walk instead of an O(keyframes) scan per
        # call. Self-healing: any out-of-order query just restarts the scan, so
        # random access stays correct. Instances are never shared across
        # threads (each export worker builds its own), so the mutable cursor is
        # safe.
        self._cursor = 0

    def get_speed_at(self, time_ms: int) -> float:
        kf = self._keyframes
        if not kf:
            return self._base_speed

        first_t, first_s = kf[0]
        if time_ms <= first_t:
            self._cursor = 0
            if first_t == 0:
                return first_s
            frac = time_ms / first_t
            return self._base_speed + (first_s - self._base_speed) * frac

        n = len(kf)
        if time_ms >= kf[-1][0]:
            self._cursor = n - 1
            return kf[-1][1]

        # time_ms lies strictly inside the keyframe span. Resume from the
        # cursor for monotonic queries; rewind to 0 if it overshot the target.
        i = self._cursor
        if i >= n - 1 or kf[i][0] > time_ms:
            i = 0
        while i < n - 1:
            t1, s1 = kf[i]
            t2, s2 = kf[i + 1]
            if t1 <= time_ms <= t2:
                self._cursor = i
                span = t2 - t1
                if span == 0:
                    return s2
                frac = (time_ms - t1) / span
                return s1 + (s2 - s1) * frac
            i += 1

        return kf[-1][1]
