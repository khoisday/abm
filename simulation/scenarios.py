"""Scenario definitions and paired multi-seed comparison."""
import copy

from .config import load_config
from .engine import Simulation

SCENARIOS = {
    "baseline": {"label_vi": "Cơ sở (baseline)", "policies": {}},
    "occupancy_hvac": {"label_vi": "Điều hòa theo hiện diện", "policies": {"occupancy_hvac": True}},
    "housekeeping": {"label_vi": "Phối hợp buồng phòng & tái sử dụng khăn",
                     "policies": {"housekeeping_coordination": True}},
    "recommendations": {"label_vi": "Gợi ý lộ trình trong công viên", "policies": {"park_recommendations": True}},
    "ride_dispatch": {"label_vi": "Điều phối chuyến trò chơi", "policies": {"ride_dispatch": True}},
    "combined": {"label_vi": "Kết hợp tất cả", "policies": {"occupancy_hvac": True, "housekeeping_coordination": True,
                                                            "park_recommendations": True, "ride_dispatch": True}},
}
ALL_OFF = {"occupancy_hvac": False, "housekeeping_coordination": False, "park_recommendations": False,
           "ride_dispatch": False}


def scenario_config(name, overrides=None, allow_room_override=False):
    ov = copy.deepcopy(overrides or {})
    ov["policies"] = {**ALL_OFF, **SCENARIOS[name]["policies"]}
    return load_config(ov, allow_room_override=allow_room_override)


def run_scenario(name, seed=None, overrides=None, allow_room_override=False, progress=None):
    ov = copy.deepcopy(overrides or {})
    if seed is not None:
        ov["seed"] = seed
    cfg = scenario_config(name, ov, allow_room_override)
    return Simulation(cfg).run(progress=progress)
