"""Rule-based stochastic guest decision model (group level).

utility = preference_match + activity_interest + hunger_relief + fatigue_relief + heat_relief
          - travel_penalty - expected_wait_penalty - price_penalty - repeat_visit_penalty
          - schedule_conflict_penalty + time_pressure

heat_relief and time_pressure are documented additions (heat recovery for indoor/water/rest
options; time_pressure drives return / go-to-park / end-of-day options relative to the
group's own intended times). Benefits apply only to relevant options.
"""
import numpy as np

BENEFITS = ["preference_match", "activity_interest", "hunger_relief", "fatigue_relief", "heat_relief", "time_pressure"]
PENALTIES = ["travel_penalty", "expected_wait_penalty", "price_penalty", "repeat_visit_penalty",
             "schedule_conflict_penalty"]


def score_option(g, opt, t, w):
    """opt keys: kind, activity, travel_min, est_wait, price, end_t, eats, rests, heat_relief, time_pressure."""
    c = dict.fromkeys(BENEFITS + PENALTIES, 0.0)
    kind = opt["kind"]
    if kind == "attraction":
        c["preference_match"] = w["preference_match"] * g.pref.get(opt["activity"], 0.3)
        c["activity_interest"] = w["activity_interest"] * (1.0 - g.fatigue / 100.0) * opt.get("interest_scale", 1.0)
        c["repeat_visit_penalty"] = w["repeat_visit_penalty_per_visit"] * g.visits.get(opt["fid"], 0)
    if opt.get("eats"):
        c["hunger_relief"] = w["hunger_relief"] * (g.hunger / 100.0) ** 2
    if opt.get("rests"):
        c["fatigue_relief"] = w["fatigue_relief"] * (g.fatigue / 100.0) ** 2
    if opt.get("heat_relief"):
        c["heat_relief"] = w["heat_relief"] * opt["heat_relief"] * (g.heat / 100.0)
    c["time_pressure"] = opt.get("time_pressure", 0.0)
    c["travel_penalty"] = w["travel_penalty_per_30min"] * opt.get("travel_min", 0) / 30.0
    c["expected_wait_penalty"] = w["expected_wait_penalty_per_tolerance"] * opt.get("est_wait", 0) / g.queue_tolerance
    price = opt.get("price", 0.0)
    if price > 0:
        c["price_penalty"] = w["price_penalty"] * min(1.0, price / max(g.budget_left, 1.0))
    if opt.get("end_t") is not None and g.zone == "park" and opt["end_t"] > g.intended_return:
        c["schedule_conflict_penalty"] = (w["schedule_conflict_penalty_per_hour"]
                                          * (opt["end_t"] - g.intended_return) / 60.0)
    u = sum(c[k] for k in BENEFITS) - sum(c[k] for k in PENALTIES)
    return u, c


def softmax(utilities, temperature):
    z = np.asarray(utilities, dtype=float) / max(temperature, 1e-6)
    z -= z.max()  # numerically stable
    e = np.exp(z)
    return e / e.sum()


def choose(g, options, t, weights, temperature, rng):
    scored = [score_option(g, o, t, weights) for o in options]
    utils = [s[0] for s in scored]
    probs = softmax(utils, temperature)
    k = int(rng.choice(len(options), p=probs))
    log = [{"option": o["label"], "utility": round(u, 3), "prob": round(float(p), 3),
            **{kk: round(v, 3) for kk, v in comps.items() if v}}
           for o, (u, comps), p in zip(options, scored, probs)]
    log.sort(key=lambda r: -r["prob"])
    return k, log
