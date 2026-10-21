"""Guest-group agents, housekeeping workers and synthetic population generation."""
from dataclasses import dataclass, field

import numpy as np

from .config import hhmm
from .rng import stream

STATES_VI = {
    "IN_ROOM": "Trong phòng",
    "EATING": "Đang ăn",
    "TRAVELING": "Đang di chuyển",
    "QUEUING": "Đang xếp hàng",
    "VISITING": "Đang tham quan",
    "RESTING": "Đang nghỉ",
    "WAITING_TRANSPORT": "Chờ phương tiện",
    "RETURNING": "Đang về khách sạn",
    "FINISHED": "Kết thúc ngày",
}
ACTIVITY_TYPES = ["thrill", "family", "indoor", "water", "show", "pool", "spa", "gym", "kids"]


@dataclass
class GuestGroup:
    gid: int
    room: int
    size: int
    adults: int
    seniors: int
    children: int
    young_children: int
    category: str
    pref: dict
    budget_vnd: float
    walk_speed: float
    queue_tolerance: float
    conservation_pref: float
    rec_acceptance: float
    guest_setpoint: float
    # daily plan (re-drawn each day from the group_day stream)
    park_intent: bool = False
    wake_t: int = 0
    intended_departure: int = 0
    intended_return: int = 0
    bedtime: int = 0
    # dynamic state
    state: str = "IN_ROOM"
    node: str = "ROOMS"
    zone: str = "hotel"
    asleep: bool = True
    hunger: float = 40.0
    fatigue: float = 10.0
    heat: float = 0.0
    last_update: int = 0
    budget_left: float = 0.0
    destination: str = None
    route: list = field(default_factory=list)
    walk_from: str = None
    walk_start: int = 0
    walk_end: int = 0
    pending: tuple = None
    visits: dict = field(default_factory=dict)
    excluded_until: dict = field(default_factory=dict)
    in_park_today: bool = False
    park_entry_t: int = None
    returned_t: int = None
    meals_today: int = 0
    walking_min: float = 0.0
    transport_min: float = 0.0
    queue_token: int = 0
    returning: bool = False
    final: tuple = None
    wait_start: int = 0
    env: str = "in"
    service_start: int = 0
    service_wait: int = 0
    skip_breakfast: bool = False
    history: list = field(default_factory=list)

    @property
    def has_young_child(self):
        return self.young_children > 0


def _draw_composition(rng, size, pcfg):
    seniors_share = pcfg["senior_share_of_adults"]
    if size == 1:
        category, adults, children = "solo", 1, 0
    elif size == 2:
        if rng.random() < pcfg["couple_share_of_size2"]:
            category, adults, children = "couple", 2, 0
        else:
            category, adults, children = "family", 1, 1
    else:
        if rng.random() < pcfg["family_share_of_size3plus"]:
            category = "family"
            adults = 2 if size <= 4 else int(rng.choice([2, 3]))
            children = size - adults
        else:
            category, adults, children = "friends", size, 0
    seniors = int(rng.binomial(adults, seniors_share))
    young = int(rng.binomial(children, pcfg["young_child_share_of_children"])) if children else 0
    assert adults >= 1 and adults + children == size
    return category, adults, seniors, children, young


def generate_population(cfg, seed):
    """Create rooms->groups assignment and heterogeneous guest groups.

    Returns (groups, sold_rooms_mask). One group per occupied room.
    """
    n_rooms = cfg["hotel"]["rooms"]
    n_occ = int(round(n_rooms * cfg["hotel"]["occupancy"]))
    pcfg = cfg["population"]
    rcfg = cfg["rooms"]
    rng = stream(seed, "population")
    sold = np.sort(rng.choice(n_rooms, size=n_occ, replace=False)) if n_occ else np.array([], dtype=int)
    sizes = rng.choice(pcfg["group_sizes"], size=n_occ, p=pcfg["group_size_probs"])
    groups = []
    for gid, (room, size) in enumerate(zip(sold, sizes)):
        size = int(size)
        category, adults, seniors, children, young = _draw_composition(rng, size, pcfg)
        means = pcfg["preference_means"][category]
        pref = {a: float(np.clip(means[a] + rng.normal(0, pcfg["preference_noise_sd"]), 0.02, 1.0))
                for a in ACTIVITY_TYPES}
        budget = size * pcfg["budget_vnd_per_person_median"] * float(np.exp(rng.normal(0, pcfg["budget_lognormal_sigma"])))
        speed = float(np.clip(rng.normal(1.0, 0.1), 0.7, 1.3))
        if young or seniors:
            speed *= 0.85
        tol = pcfg["queue_tolerance_min_median"] * float(np.exp(rng.normal(0, pcfg["queue_tolerance_lognormal_sigma"])))
        if young:
            tol *= 0.8
        cons = float(rng.beta(2, 3))
        rec = float(np.clip((0.15 + 0.5 * cons + rng.normal(0, 0.1)) * pcfg["recommendation_acceptance_scale"], 0.02, 0.95))
        sp = float(rng.choice(rcfg["guest_setpoints_c"], p=rcfg["guest_setpoint_probs"]))
        groups.append(GuestGroup(gid=gid, room=int(room), size=size, adults=adults, seniors=seniors,
                                 children=children, young_children=young, category=category, pref=pref,
                                 budget_vnd=budget, walk_speed=speed, queue_tolerance=float(np.clip(tol, 8, 120)),
                                 conservation_pref=cons, rec_acceptance=rec, guest_setpoint=sp,
                                 budget_left=budget))
    sold_mask = np.zeros(n_rooms, dtype=bool)
    sold_mask[sold] = True
    return groups, sold_mask


def draw_daily_plan(g, cfg, seed, day):
    """Per-day intentions from a stream keyed by (group, day)."""
    p = cfg["population"]
    rng = stream(seed, "group_day", g.gid, day)
    base = day * 1440

    def clipped(mean_key, sd_key, lo, hi):
        return int(np.clip(rng.normal(hhmm(p[mean_key]), p[sd_key]), hhmm(lo), hhmm(hi)))

    g.park_intent = bool(rng.random() < p["park_intention"])
    g.wake_t = base + clipped("wake_mean_hhmm", "wake_sd_min", "05:30", "10:00")
    g.intended_departure = base + clipped("park_departure_mean_hhmm", "park_departure_sd_min", "08:30", "13:00")
    g.intended_return = base + clipped("park_return_mean_hhmm", "park_return_sd_min", "13:00", "21:30")
    g.bedtime = base + clipped("bedtime_mean_hhmm", "bedtime_sd_min", "20:30", "23:59")
    g.skip_breakfast = bool(rng.random() > p["breakfast_probability"])


@dataclass
class Housekeeper:
    wid: int
    section: list
    busy_until: int = -1
    room: int = None
    task_start: int = None
    task_history: list = field(default_factory=list)

    @property
    def available(self):
        return self.room is None
