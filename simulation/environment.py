"""Schematic destination network, facilities and synthetic weather.

The network is SCHEMATIC. Node coordinates are for drawing only and are not
geographic positions of any real property.
"""
import math
from collections import deque

import numpy as np

from .config import hhmm
from .rng import stream


class Network:
    """Walking graph (the promenade joins the island hotel and the park); other zones are linked by lines."""

    def __init__(self, cfg):
        net = cfg["network"]
        self.nodes = net["nodes"]
        self.ids = list(self.nodes)
        idx = {n: i for i, n in enumerate(self.ids)}
        n = len(self.ids)
        inf = float("inf")
        d = [[0 if i == j else inf for j in range(n)] for i in range(n)]
        nxt = [[j if i == j else None for j in range(n)] for i in range(n)]
        for a, b, w in net["walk_edges_min"]:
            i, j = idx[a], idx[b]
            d[i][j] = d[j][i] = w
            nxt[i][j], nxt[j][i] = j, i
        for k in range(n):  # Floyd-Warshall, cached once
            for i in range(n):
                for j in range(n):
                    if d[i][k] + d[k][j] < d[i][j]:
                        d[i][j] = d[i][k] + d[k][j]
                        nxt[i][j] = nxt[i][k]
        self._d, self._nxt, self._idx = d, nxt, idx

    def zone(self, node):
        return self.nodes[node]["zone"]

    def walk_min(self, a, b):
        w = self._d[self._idx[a]][self._idx[b]]
        if w == float("inf"):
            raise ValueError(f"No walking path {a}->{b} (different zones need transport)")
        return w

    def route(self, a, b):
        i, j = self._idx[a], self._idx[b]
        if self._nxt[i][j] is None:
            return []
        path = [a]
        while i != j:
            i = self._nxt[i][j]
            path.append(self.ids[i])
        return path


class Weather:
    """Synthetic smooth diurnal outdoor temperature (deg C) and solar factor (0-1)."""

    def __init__(self, cfg, seed, days):
        w = cfg["weather"]
        rng = stream(seed, "weather")
        self.offsets = rng.normal(0.0, w["day_offset_sd_c"], size=days + 1)
        minutes = np.arange((days + 1) * 1440)
        hour = (minutes % 1440) / 60.0
        day = minutes // 1440
        self.temp = (w["mean_c"] + w["amplitude_c"] * np.cos(2 * math.pi * (hour - w["peak_hour"]) / 24.0)
                     + self.offsets[day])
        s0, s1 = w["solar_start_hour"], w["solar_end_hour"]
        self.solar = np.where((hour > s0) & (hour < s1), np.sin(math.pi * (hour - s0) / (s1 - s0)), 0.0)


class Facility:
    """Attraction, restaurant, rest area or pool with a FIFO queue.

    Queue entries: [group, t_join, token]. Active: dict group_id -> (group, persons, end_t).
    """

    def __init__(self, fid, spec, closing_grace):
        self.id = fid
        self.spec = spec
        self.kind = spec["kind"]
        self.node = spec["node"]
        self.label = spec["label_vi"]
        self.activity = spec.get("activity", fid.lower())
        self.capacity = spec["capacity_persons"]
        self.windows = [(hhmm(a), hhmm(b)) for a, b in spec["windows"]]
        self.cycle = spec.get("cycle_min")
        self.closing_grace = closing_grace
        self.queue = deque()
        self.active = {}
        self.ready_at = 0          # batch rides: next possible dispatch minute
        self.check_pending = False
        self.show_times = [hhmm(s) for s in spec.get("show_times", [])]
        self.energy = spec.get("energy", {})
        # statistics
        self.stats = dict(arrivals=0, arrival_persons=0, admissions=0, admitted_persons=0,
                          completions=0, completed_persons=0, abandonments=0, abandoned_persons=0,
                          balks=0, rejected=0, rejected_persons=0, cycles=0, cycle_riders=0,
                          wait_sum_person_min=0.0, max_queue_persons=0)
        self.waits = []  # per admitted group (minutes)

    # ---- schedule helpers (t is absolute minute) ----
    def is_open(self, t):
        m = t % 1440
        return any(a <= m < b for a, b in self.windows)

    def in_grace(self, t):
        m = t % 1440
        return any(b <= m < b + self.closing_grace for a, b in self.windows)

    def minutes_to_close(self, t):
        m = t % 1440
        for a, b in self.windows:
            if a <= m < b:
                return b - m
        return 0

    def opens_within(self, t, horizon):
        m = t % 1440
        return any(m < a <= m + horizon for a, b in self.windows)

    def active_persons(self):
        return sum(p for _, p, _ in self.active.values())

    def queue_persons(self):
        return sum(e[0].size for e in self.queue)

    def throughput_per_min(self):
        if self.kind == "batch":
            return self.capacity / self.cycle
        dur = self.spec.get("duration_min_mean") or self.spec.get("duration_min", 30)
        return self.capacity / dur

    def posted_wait(self, t):
        """Wait estimate shown to guests (queue length / nominal throughput)."""
        if self.kind == "show":
            nxt = self.next_show(t)
            return 0.0 if nxt is None else max(0.0, nxt - t)
        q = self.queue_persons()
        if self.kind != "batch":
            free = self.capacity - self.active_persons()
            q = max(0, q - free)
        return q / self.throughput_per_min()

    def next_show(self, t):
        day0 = (t // 1440) * 1440
        for s in self.show_times:
            if day0 + s >= t:
                return day0 + s
        return None


def build_facilities(cfg):
    return {fid: Facility(fid, spec, cfg["closing_grace_min"]) for fid, spec in cfg["facilities"].items()}
