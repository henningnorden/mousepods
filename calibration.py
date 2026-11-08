# SPDX-License-Identifier: Apache-2.0
"""Calibration of a game profile: the player looks at five points one after the other and holds
still for a moment at each; the head angles at the edge points become the profile's range.

Pure state machine: the controller feeds head angles (degrees, yaw + = right, pitch + = up) with
their time; nothing here reads a clock.
"""
from collections import deque

STEPS = ("center", "left", "right", "top", "bottom")
HOLD_S = 2.0            # hold still this long at each point
STILL_DEG = 1.5         # yaw and pitch each stay within this span while holding
MIN_DEG = 5.0           # an edge point at least this far from the centre, on its own side
NO_SIGNAL_S = 3.0       # no head data this long: the AirPods are gone


class Calibration:
    def __init__(self, on_center=None):
        self.on_center = on_center
        self.index = 0
        self.window = deque()            # (t, yaw, pitch) since the head became still
        self.hint = None
        self.edges = {}
        self.end = None                  # "done" | "cancelled" | "failed"
        self.last_seen = None
        self.now = 0.0

    @property
    def finished(self):
        return self.end is not None

    def feed(self, now, yaw, pitch):
        if self.finished:
            return
        self.now = self.last_seen = now
        w = self.window
        w.append((now, yaw, pitch))
        while w and (max(s[1] for s in w) - min(s[1] for s in w) >= STILL_DEG
                     or max(s[2] for s in w) - min(s[2] for s in w) >= STILL_DEG):
            w.popleft()                   # moved: the hold starts again from the newest samples
        if w and now - w[0][0] >= HOLD_S:
            n = len(w)
            self._capture(sum(s[1] for s in w) / n, sum(s[2] for s in w) / n)
            w.clear()

    def tick(self, now):
        if self.finished:
            return
        self.now = now
        if self.last_seen is None:
            self.last_seen = now
        elif now - self.last_seen > NO_SIGNAL_S:
            self.end, self.hint = "failed", "no_signal"

    def cancel(self):
        if not self.finished:
            self.end = "cancelled"

    def _capture(self, yaw, pitch):
        step = STEPS[self.index]
        if step == "center":
            if self.on_center:
                self.on_center()      # the tracker is recentred here: later angles are relative to it
        else:
            value = {"left": -yaw, "right": yaw, "top": pitch, "bottom": -pitch}[step]
            if value < 0 and abs(value) >= MIN_DEG:
                self.hint = "wrong_side"
                return
            if value < MIN_DEG:
                self.hint = "too_close"
                return
            self.edges[step] = value
        self.hint = None
        self.index += 1
        if self.index == len(STEPS):
            self.end = "done"

    def state(self):
        step = self.end or STEPS[self.index]
        w = self.window
        progress = 0.0 if self.finished or not w else min((self.now - w[0][0]) / HOLD_S, 1.0)
        result = None
        if self.end == "done":
            e = self.edges
            result = {"yaw": {"neg": e["left"], "pos": e["right"]}, "pitch": {"neg": e["bottom"], "pos": e["top"]}}
        return {"step": step, "progress": round(progress, 3), "hint": self.hint, "result": result}
