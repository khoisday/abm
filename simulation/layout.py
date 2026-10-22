"""Room-to-floor layout of a hotel (display, floor statistics and the inspector).

A hotel config lists room floors as blocks: {"floors": [first, last], "rooms": per floor, "class": label}.
Rooms are numbered floor by floor; on each floor the first half faces the sea (row 0) and the rest the
other side (row 1). Room number = floor * 100 + position + 1. Diagnostic configs whose plan does not add up
to the room count fall back to an even split over `floors` room floors starting at floor 3.
"""
import math


def floor_plan(hcfg):
    n = int(hcfg["rooms"])
    blocks = hcfg.get("floor_plan") or []
    floors = [(f, int(b["rooms"]), b.get("class", "")) for b in blocks for f in range(b["floors"][0], b["floors"][1] + 1)]
    if sum(c for _, c, _ in floors) != n:
        k = max(1, int(hcfg.get("floors", 1)))
        per = math.ceil(n / k) if n else 0
        floors, left = [], n
        for i in range(k):
            c = min(per, left)
            floors.append((3 + i, c, ""))
            left -= c
    return [dict(n=f, count=c, cls=cl) for f, c, cl in floors if c > 0]


def room_layout(hcfg):
    """Per room: floor number, position on the floor, row (0 = sea side) and room number."""
    floor_of, slot_of, row_of, number, cls = [], [], [], [], []
    for fl in floor_plan(hcfg):
        half = math.ceil(fl["count"] / 2)
        for i in range(fl["count"]):
            floor_of.append(fl["n"])
            slot_of.append(i)
            row_of.append(0 if i < half else 1)
            number.append(fl["n"] * 100 + i + 1)
            cls.append(fl["cls"])
    return dict(floor=floor_of, slot=slot_of, row=row_of, number=number, cls=cls)
