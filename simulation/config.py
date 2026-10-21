"""Configuration loading, merging and time helpers."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = ROOT / "config" / "defaults.json"
PRODUCTION_ROOMS = 533


def hhmm(s):
    """'07:30' -> minutes after midnight."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_min(t):
    """Absolute simulation minute -> 'Dd HH:MM'."""
    day, rem = divmod(int(t), 1440)
    return f"N{day + 1} {rem // 60:02d}:{rem % 60:02d}"


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(overrides=None, allow_room_override=False):
    """Load defaults and apply overrides.

    The production room inventory is fixed at 533. Diagnostic runs may pass
    allow_room_override=True; the result is flagged as a diagnostic config.
    """
    with open(DEFAULTS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = deep_merge(cfg, overrides)
    if cfg["hotel"]["rooms"] != PRODUCTION_ROOMS:
        if not allow_room_override:
            raise ValueError("Production room inventory is fixed at 533; "
                             "use allow_room_override=True for diagnostics only.")
        cfg["diagnostic_config"] = True
    days = int(cfg["horizon_days"])
    if not 1 <= days <= cfg["max_horizon_days"]:
        raise ValueError(f"horizon_days must be 1..{cfg['max_horizon_days']}")
    occ = float(cfg["hotel"]["occupancy"])
    if not 0.0 <= occ <= 1.0:
        raise ValueError("occupancy must be within [0, 1]")
    return cfg
