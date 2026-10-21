"""Metrics, verification checks and CSV export derived only from simulation output."""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT, fmt_min
from .energy import END_USES

PARK_ATTRACTIONS = ["THRILL", "FAMILY", "INDOOR", "WATER", "SHOW"]


def emission_factors(sim, n_intervals, step):
    ec = sim.cfg["emissions"]
    path = ec.get("time_varying_factor_csv")
    if path:
        df = pd.read_csv(ROOT / path)
        vals = df["factor_kg_per_kwh"].to_numpy(dtype=float)
        per_day = 1440 // step
        if len(vals) != per_day:
            raise ValueError(f"time-varying factor file needs {per_day} rows (one per interval of a day)")
        return np.tile(vals, n_intervals // per_day)
    return np.full(n_intervals, float(ec["factor_kg_per_kwh"]))


def interval_df(sim):
    step = sim.cfg["reporting_interval_min"]
    iv = sim.meter.intervals(step)
    n = len(next(iter(iv.values())))
    df = pd.DataFrame({"interval_start_min": np.arange(n) * step})
    df["thoi_gian"] = [fmt_min(t) for t in df["interval_start_min"]]
    for k in END_USES:
        df[f"{k}_kwh"] = iv[k]
    cols = [f"{k}_kwh" for k in END_USES]
    df["total_kwh"] = df[cols].sum(axis=1)
    df["hotel_kwh"] = df[[f"{k}_kwh" for k, v in END_USES.items() if v[1] == "hotel"]].sum(axis=1)
    df["park_kwh"] = df[[f"{k}_kwh" for k, v in END_USES.items() if v[1] == "park"]].sum(axis=1)
    df["avg_power_kw"] = df["total_kwh"] / (step / 60.0)
    df["emission_factor_kg_per_kwh"] = emission_factors(sim, n, step)
    df["emissions_kgco2e"] = df["total_kwh"] * df["emission_factor_kg_per_kwh"]
    df["outdoor_temp_c"] = sim.weather.temp[df["interval_start_min"].to_numpy()]
    return df


def events_df(sim):
    df = pd.DataFrame(sim.events, columns=["t", "group_id", "event", "where", "detail", "state"])
    df.insert(1, "thoi_gian", [fmt_min(t) for t in df["t"]])
    return df


def itinerary_df(sim):
    rows = []
    for g in sim.groups:
        for i, (a, b, act, node, wait) in enumerate(g.history):
            rows.append(dict(group_id=g.gid, room=g.room, step=i, start_min=a, end_min=b, start=fmt_min(a),
                             end=fmt_min(b), activity=act, node=node, wait_min=wait))
    return pd.DataFrame(rows)


def groups_df(sim):
    rows = []
    for g in sim.groups:
        rows.append(dict(group_id=g.gid, room=g.room, size=g.size, adults=g.adults, seniors=g.seniors,
                         children=g.children, young_children=g.young_children, category=g.category,
                         **{f"pref_{k}": round(v, 3) for k, v in g.pref.items()},
                         budget_vnd=round(g.budget_vnd), budget_left_vnd=round(g.budget_left),
                         walk_speed=round(g.walk_speed, 3), queue_tolerance_min=round(g.queue_tolerance, 1),
                         conservation_pref=round(g.conservation_pref, 3), rec_acceptance=round(g.rec_acceptance, 3),
                         guest_setpoint_c=g.guest_setpoint, park_intent_last_day=g.park_intent,
                         walking_min=round(g.walking_min, 1), transport_min=round(g.transport_min, 1),
                         final_state=g.state))
    return pd.DataFrame(rows)


def queue_stats_df(sim):
    rows = []
    for f in sim.fac.values():
        s = f.stats
        w = np.array(f.waits) if f.waits else np.array([0.0])
        cap_cycles = s["cycles"] * f.capacity
        rows.append(dict(facility=f.id, label_vi=f.label, kind=f.kind, capacity_persons=f.capacity,
                         queue_arrivals_groups=s["arrivals"], queue_arrival_persons=s["arrival_persons"],
                         admissions_groups=s["admissions"], admitted_persons=s["admitted_persons"],
                         completed_groups=s["completions"], completed_persons=s["completed_persons"],
                         abandonments_groups=s["abandonments"], abandoned_persons=s["abandoned_persons"],
                         balks_groups=s["balks"], rejected_groups=s["rejected"], rejected_persons=s["rejected_persons"],
                         mean_wait_min=round(s["wait_sum_person_min"] / max(1, s["admitted_persons"]), 2),
                         p90_wait_min=round(float(np.percentile(w, 90)), 1), max_wait_min=int(w.max()),
                         max_queue_persons=s["max_queue_persons"], cycles_or_shows=s["cycles"],
                         cycle_utilization=round(s["cycle_riders"] / cap_cycles, 3) if cap_cycles else None,
                         still_queued_at_horizon=len(f.queue), active_at_horizon=f.active_persons()))
    return pd.DataFrame(rows)


def park_visitor_hours(sim):
    ev = sim.events
    entry, hours = {}, 0.0
    for t, gid, e, where, *_ in ev:
        if e == "enter_park":
            entry[gid] = t
        elif e == "board" and where == "PARK_STOP" and gid in entry:
            hours += (t - entry.pop(gid)) * sim.groups[gid].size / 60.0
    for gid, t0 in entry.items():   # still in park at horizon
        hours += (sim.T - t0) * sim.groups[gid].size / 60.0
    return hours


def summary(sim):
    iv = interval_df(sim)
    cfg = sim.cfg
    step_h = cfg["reporting_interval_min"] / 60.0
    by_use = {k: float(iv[f"{k}_kwh"].sum()) for k in END_USES}
    total = float(iv["total_kwh"].sum())
    hotel = float(iv["hotel_kwh"].sum())
    park = float(iv["park_kwh"].sum())
    sold = int(sim.rooms.sold.sum())
    room_nights = sold * sim.days       # nightly census: every sold room is occupied each night (no check-in/out)
    q = queue_stats_df(sim).set_index("facility")
    hk_cleaned = sum(d["cleaned"] for d in sim.hk_daily)
    hk_pending = sum(d["pending"] for d in sim.hk_daily)
    delays = [h["delay_min"] for h in sim.hk_log if h["outcome"] == "done"]
    lc = cfg["laundry"]
    washed = sum(b["load_kg"] for b in sim.laundry_batches if b["end"] <= sim.T)
    in_machine = sum(b["load_kg"] for b in sim.laundry_batches if b["end"] > sim.T)
    pending_kg = sim.laundry_bin
    full_batches = math.floor(pending_kg / lc["capacity_kg"])
    rem = pending_kg - full_batches * lc["capacity_kg"]
    completion_kwh = full_batches * lc["kwh_per_full_batch"] + (
        lc["kwh_per_full_batch"] * (lc["fixed_energy_fraction"] + (1 - lc["fixed_energy_fraction"]) * rem / lc["capacity_kg"])
        if rem > 1e-9 else 0.0)
    park_groups = [g for g in sim.groups if any(e for e in g.history)]
    returns = [t for t, gid, e, *_ in sim.events if e == "return_hotel"]
    meals = int(q.loc[["HOTEL_RESTAURANT", "PARK_RESTAURANT"], "completed_persons"].sum())
    attr = q.loc[PARK_ATTRACTIONS]
    batch = q[q["kind"] == "batch"]
    persons = sum(g.size for g in sim.groups)
    ef = iv["emission_factor_kg_per_kwh"]
    out = dict(
        scenario_policies=json.dumps(cfg["policies"]), seed=cfg["seed"], horizon_days=sim.days,
        rooms=sim.rooms.n, occupied_rooms=sold, guest_groups=len(sim.groups), guests=persons,
        total_kwh=total, hotel_kwh=hotel, park_kwh=park, transport_kwh=by_use["transport"],
        transport_energy_included=cfg["transport"]["include_transport_energy"],
        **{f"kwh_{k}": v for k, v in by_use.items()},
        peak_15min_kw=float(iv["avg_power_kw"].max()),
        emissions_kgco2e=float(iv["emissions_kgco2e"].sum()),
        emission_factor_note="constant" if not cfg["emissions"].get("time_varying_factor_csv") else "time-varying",
        emission_factor_mean=float(ef.mean()),
        occupied_room_nights=room_nights,
        hotel_kwh_per_occupied_room_night=hotel / room_nights if room_nights else None,
        park_entries=sim.park_entries, park_visitor_hours=park_visitor_hours(sim),
        park_kwh_per_visitor_hour=(park / park_visitor_hours(sim)) if park_visitor_hours(sim) > 0 else None,
        completed_attraction_visits_persons=int(attr["completed_persons"].sum()),
        attraction_mean_wait_min=float((attr["mean_wait_min"] * attr["admitted_persons"]).sum()
                                       / max(1, attr["admitted_persons"].sum())),
        abandonments_groups=int(q["abandonments_groups"].sum()), balks_groups=int(q["balks_groups"].sum()),
        rejected_groups=int(q["rejected_groups"].sum()),
        ride_cycles=int(batch["cycles_or_shows"].sum()),
        ride_cycle_utilization=float((batch["completed_persons"].sum() + 0) /
                                     max(1, (batch["cycles_or_shows"] * batch["capacity_persons"]).sum())),
        meals_served_persons=meals,
        mean_walking_min_per_group=float(np.mean([g.walking_min for g in sim.groups])) if sim.groups else 0.0,
        mean_transport_min_per_group=float(np.mean([g.transport_min for g in sim.groups])) if sim.groups else 0.0,
        median_return_to_hotel=fmt_min(int(np.median(returns))) if returns else "",
        rooms_cleaned=hk_cleaned, rooms_pending_cleaning=hk_pending,
        housekeeping_interruptions=sim.hk_interruptions,
        mean_housekeeping_delay_min=float(np.mean(delays)) if delays else 0.0,
        laundry_demand_kg=sim.laundry_demand_kg, laundry_washed_kg=washed, laundry_in_machine_at_horizon_kg=in_machine,
        laundry_pending_kg=pending_kg, laundry_batches=len(sim.laundry_batches),
        laundry_full_batch_share=(sum(b["full"] for b in sim.laundry_batches) / len(sim.laundry_batches))
        if sim.laundry_batches else None,
        laundry_completion_kwh_estimate=completion_kwh,
        occupied_room_hours_above_comfort=float(sim.room_hot_minutes.sum() / 60.0),
        occupied_degree_hours_above_comfort=float(sim.room_degree_minutes.sum() / 60.0),
        recommendations_offered=len(sim.rec_log),
        recommendations_accepted=sum(r["accepted"] for r in sim.rec_log),
        unfinished_demand_events=len(sim.unfinished_demand),
        spillover_kwh_after_horizon=sum(sim.meter.spillover_kwh().values()),
        runtime_s=getattr(sim, "runtime_s", None),
    )
    return out


def verify(sim):
    """Model-level verification checks. Returns list of dicts (check, passed, detail)."""
    checks = []

    def add(name, ok, detail=""):
        checks.append(dict(check=name, passed=bool(ok), detail=str(detail)))

    R = sim.rooms
    add("Đúng 533 phòng sản xuất, mã phòng duy nhất", R.n == 533 or sim.cfg.get("diagnostic_config"), R.n)
    exp = int(round(R.n * sim.cfg["hotel"]["occupancy"]))
    add("Số phòng có khách khớp công suất", int(R.sold.sum()) == exp, f"{int(R.sold.sum())} / {exp}")
    rooms = [g.room for g in sim.groups]
    add("Mỗi nhóm có đúng một phòng hợp lệ", len(set(rooms)) == len(rooms) and all(R.sold[r] for r in rooms))
    add("Thành phần nhóm hợp lệ (≥1 người lớn, tổng = quy mô)",
        all(g.adults >= 1 and g.adults + g.children == g.size and g.young_children <= g.children for g in sim.groups))
    persons = sum(g.size for g in sim.groups)
    cons = all(sum(s["state_persons"].values()) == persons for s in sim.snapshots)
    add("Bảo toàn số khách qua mọi ảnh chụp trạng thái", cons, persons)
    walks = [e for e in sim.events if e[2] == "walk"]
    zero = [e for e in walks if e[4] == "0 phút" and e[3].split("->")[0] != e[3].split("->")[1]]
    add("Di chuyển giữa hai nút khác nhau luôn tốn thời gian", not zero, len(zero))
    over = []
    for s in sim.snapshots:
        for fid, a in s["active"].items():
            if a > sim.fac[fid].capacity:
                over.append((s["t"], fid, a))
    add("Không vượt sức chứa", not over, over[:3])
    trips_over = [t for t in sim.trips if t["persons"] > sim.cfg["transport"]["capacity_persons"]]
    add("Phương tiện không vượt sức chứa", not trips_over)
    closed_adm = [e for e in sim.events if e[2] == "admit" and not (sim.fac[e[3]].is_open(e[0]) or sim.fac[e[3]].in_grace(e[0])
                                                                     or sim.fac[e[3]].kind == "show")]
    add("Điểm đã đóng cửa không nhận khách mới", not closed_adm, len(closed_adm))
    stuck = [g.gid for g in sim.groups if g.state in ("WAITING_TRANSPORT", "QUEUING")]
    add("Không nhóm nào bị kẹt ở hàng chờ/bến cuối kỳ", not stuck, stuck[:5])
    overlap = False
    for w in sim.workers:
        h = sorted(w.task_history)
        overlap |= any(a[1] > b[0] for a, b in zip(h, h[1:]))
    add("Nhân viên buồng phòng không làm hai việc cùng lúc", not overlap)
    iv = interval_df(sim)
    vac_kw = float(sim.snapshots[-1]["room_light_kw"][~R.sold].sum()) if (~R.sold).any() else 0.0
    add("Phòng trống vẫn được tính điện chờ", (~R.sold).sum() == 0 or vac_kw > 0, f"{vac_kw:.2f} kW")
    cols = [f"{k}_kwh" for k in END_USES]
    add("Tổng theo mục đích sử dụng = tổng điện", np.allclose(iv[cols].sum(axis=1), iv["total_kwh"]))
    integ = float((iv["avg_power_kw"] * sim.cfg["reporting_interval_min"] / 60).sum())
    add("Điện năng = công suất trung bình × thời gian", math.isclose(integ, iv["total_kwh"].sum(), rel_tol=1e-9),
        f"{integ:.3f}")
    minute_total = sum(float(v[:sim.T].sum()) for v in sim.meter.kwh.values())
    add("Tổng theo khoảng 15 phút = tổng theo phút", math.isclose(minute_total, iv["total_kwh"].sum(), rel_tol=1e-9))
    add("Phát thải = điện × hệ số", np.allclose(iv["emissions_kgco2e"], iv["total_kwh"] * iv["emission_factor_kg_per_kwh"]))
    add("Công việc tồn đọng được báo cáo", len(sim.hk_daily) == sim.days, sim.hk_daily[-1] if sim.hk_daily else "")
    return checks


def export_run(sim, outdir, name="run"):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    iv = interval_df(sim)
    iv.to_csv(out / f"{name}_interval_electricity.csv", index=False, encoding="utf-8-sig")
    events_df(sim).to_csv(out / f"{name}_events.csv", index=False, encoding="utf-8-sig")
    itinerary_df(sim).to_csv(out / f"{name}_itineraries.csv", index=False, encoding="utf-8-sig")
    groups_df(sim).to_csv(out / f"{name}_groups.csv", index=False, encoding="utf-8-sig")
    queue_stats_df(sim).to_csv(out / f"{name}_queue_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(sim.hk_log).to_csv(out / f"{name}_housekeeping.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(sim.laundry_batches).to_csv(out / f"{name}_laundry_batches.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(sim.trips).to_csv(out / f"{name}_transport_trips.csv", index=False, encoding="utf-8-sig")
    dec = pd.DataFrame(sim.decision_log)
    if not dec.empty:
        dec["options"] = dec["options"].apply(lambda o: json.dumps(o, ensure_ascii=False))
        dec.insert(1, "thoi_gian", [fmt_min(t) for t in dec["t"]])
    dec.to_csv(out / f"{name}_decisions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(sim.unfinished_demand).to_csv(out / f"{name}_unfinished_demand.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([summary(sim)]).to_csv(out / f"{name}_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(verify(sim)).to_csv(out / f"{name}_verification.csv", index=False, encoding="utf-8-sig")
    return out


COMPARE_METRICS = [
    ("total_kwh", "Tổng điện (kWh)"), ("hotel_kwh", "Điện khách sạn (kWh)"), ("park_kwh", "Điện công viên (kWh)"),
    ("kwh_room_hvac", "Điều hòa phòng (kWh)"), ("kwh_laundry", "Giặt là (kWh)"),
    ("kwh_attraction_cycles", "Chu kỳ trò chơi (kWh)"), ("peak_15min_kw", "Đỉnh công suất 15 phút (kW)"),
    ("emissions_kgco2e", "Phát thải liên quan điện (kg CO2e)"),
    ("hotel_kwh_per_occupied_room_night", "Điện KS / đêm phòng (kWh)"),
    ("attraction_mean_wait_min", "Chờ TB trò chơi (phút)"), ("completed_attraction_visits_persons", "Lượt chơi hoàn thành"),
    ("abandonments_groups", "Bỏ hàng (nhóm)"), ("ride_cycle_utilization", "Hệ số tải chuyến"),
    ("occupied_room_hours_above_comfort", "Giờ-phòng có khách quá nóng"),
    ("occupied_degree_hours_above_comfort", "Độ-giờ vượt ngưỡng (phòng có khách)"),
    ("rooms_pending_cleaning", "Phòng chưa dọn"), ("housekeeping_interruptions", "Dọn phòng bị gián đoạn"),
    ("laundry_pending_kg", "Đồ giặt tồn (kg)"), ("laundry_completion_kwh_estimate", "Điện để giặt nốt phần tồn (kWh)"),
    ("park_visitor_hours", "Giờ-khách trong công viên"),
]


def paired_comparison(per_seed):
    """per_seed: DataFrame with columns scenario, seed and metric columns. Paired by seed vs baseline."""
    rows = []
    base = per_seed[per_seed["scenario"] == "baseline"].set_index("seed")
    for sc, df in per_seed.groupby("scenario", sort=False):
        d = df.set_index("seed")
        for key, label in COMPARE_METRICS:
            if key not in d:
                continue
            x = d[key].astype(float)
            b = base.loc[x.index, key].astype(float)
            diff = x - b
            pct = np.where(b.abs() > 1e-12, diff / b.abs() * 100, np.nan)
            rows.append(dict(scenario=sc, metric=key, metric_vi=label, n_seeds=len(x), mean=x.mean(), sd=x.std(ddof=1),
                             baseline_mean=b.mean(), mean_paired_diff=diff.mean(), sd_paired_diff=diff.std(ddof=1),
                             min_paired_diff=diff.min(), max_paired_diff=diff.max(),
                             mean_pct_diff=float(np.nanmean(pct)) if np.isfinite(pct).any() else None))
    return pd.DataFrame(rows)
