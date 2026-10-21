"""Build the static website (Vercel-ready) in site/.

  python run_simulation.py --compare --seeds 10   # comparison tab reads outputs/scenario_summary.csv
  python build_site.py
  cd site && vercel deploy --prod

site/index.html is a static dashboard (same views as the Streamlit app, without re-running);
site/map/<scenario>.html are the 3D maps; site/data/ holds precomputed JSON + CSV downloads.
"""
import base64
import json
import shutil

import numpy as np
import pandas as pd

from simulation.config import ROOT, fmt_min
from simulation.energy import END_USES
from simulation.inputs import reference_daily_profile, self_reported_monthly_grid_kwh
from simulation.metrics import (COMPARE_METRICS, groups_df, interval_df, itinerary_df, queue_stats_df, summary,
                                verify)
from simulation.scenarios import SCENARIOS, run_scenario
from simulation.viewer import build_payload, render_html

SITE = ROOT / "site"
DATA = SITE / "data"
SLUG = {"baseline": "baseline", "occupancy_hvac": "occupancy-hvac", "housekeeping": "housekeeping",
        "recommendations": "recommendations", "ride_dispatch": "ride-dispatch", "combined": "combined"}
SEED = 42
DEC_SHARD = 25
# 8 end-use groups (fixed order = fixed colour slot), detail kept in the bar chart / tables
GROUPS = [
    ("room_hvac", "Điều hòa phòng", ["room_hvac"]),
    ("room_lp", "Chiếu sáng & ổ cắm phòng", ["room_lighting_plug"]),
    ("hot_water", "Nước nóng phòng", ["hotel_hot_water"]),
    ("hotel_common", "Khu chung & tiện ích KS", ["hotel_common", "hotel_wellness"]),
    ("restaurants", "Nhà hàng (KS + công viên)", ["hotel_restaurant", "park_restaurant"]),
    ("laundry", "Giặt là", ["laundry"]),
    ("attractions", "Trò chơi & khu trong nhà", ["attraction_standby", "attraction_cycles", "indoor_attraction_cooling"]),
    ("park_other", "Khu chung công viên & giao thông", ["park_common", "transport"]),
]
KPI_KEYS = ["total_kwh", "hotel_kwh", "park_kwh", "peak_15min_kw", "emissions_kgco2e",
            "hotel_kwh_per_occupied_room_night", "park_entries", "park_visitor_hours",
            "completed_attraction_visits_persons", "attraction_mean_wait_min", "abandonments_groups",
            "meals_served_persons", "rooms_cleaned", "rooms_pending_cleaning", "housekeeping_interruptions",
            "laundry_demand_kg", "laundry_pending_kg", "laundry_completion_kwh_estimate",
            "occupied_room_hours_above_comfort", "occupied_degree_hours_above_comfort",
            "mean_walking_min_per_group", "mean_transport_min_per_group", "median_return_to_hotel",
            "recommendations_offered", "recommendations_accepted", "ride_cycles", "ride_cycle_utilization",
            "occupied_rooms", "guests", "guest_groups", "rooms", "emission_factor_mean", "transport_energy_included"]


def b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr, dtype=np.uint8).tobytes()).decode("ascii")


def clean(v):
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else round(float(v), 4)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def records(df):
    return [{k: clean(v) for k, v in r.items()} for r in df.to_dict("records")]


def room_arrays(sim):
    snaps, sold = sim.snapshots, sim.rooms.sold
    temp = np.stack([np.clip((s["room_temp"] - 15) * 10, 0, 255) for s in snaps]).round()
    sp = np.stack([np.clip((s["room_sp"] - 15) * 10, 0, 255) for s in snaps]).round()
    hv = np.stack([np.clip(s["room_hvac_kw"] * 200, 0, 255) for s in snaps]).round()
    li = np.stack([np.clip(s["room_light_kw"] * 400, 0, 255) for s in snaps]).round()
    states = []
    for s in snaps:
        code = np.zeros(sim.rooms.n, dtype=int)                 # 0 unsold
        code[sold & ~s["room_occ"] & ~s["room_control"]] = 1   # vacant, HVAC off
        code[sold & ~s["room_occ"] & s["room_control"]] = 2    # vacant, HVAC on
        code[s["room_occ"]] = 3                                # guests in room
        code[s["room_clean"]] = 4                              # being cleaned
        states.append("".join(map(str, code)))
    return dict(t=[s["t"] for s in snaps], tout=[round(s["t_out"], 2) for s in snaps],
                temp=b64(temp), sp=b64(sp), hvac=b64(hv), light=b64(li), state=states,
                group_of=sim.rooms.group_of.tolist(), bypass=sim.rooms.bypass.astype(int).tolist(),
                comfort=sim.cfg["rooms"]["comfort_threshold_c"])


def scenario_data(name, sim):
    S = summary(sim)
    iv = interval_df(sim)
    step = sim.cfg["reporting_interval_min"]
    kw = lambda cols: (iv[[f"{c}_kwh" for c in cols]].sum(axis=1) / (step / 60)).round(2).tolist()
    qs = queue_stats_df(sim)
    fac = list(sim.fac)
    gdf = groups_df(sim)
    groups = []
    for g, row in zip(sim.groups, records(gdf)):
        row["itinerary"] = [[a, b, act, node, w] for a, b, act, node, w in g.history]
        groups.append(row)
    return dict(
        meta=dict(scenario=name, label=SCENARIOS[name]["label_vi"], slug=SLUG[name], seed=SEED,
                  floors=sim.cfg["hotel"]["floors"], days=sim.days, runtime_s=round(sim.runtime_s, 2),
                  policies=sim.cfg["policies"], start_date=sim.cfg["start_date"]),
        kpi={k: clean(S[k]) for k in KPI_KEYS},
        energy=dict(t=[fmt_min(t)[3:] if sim.days == 1 else fmt_min(t) for t in iv["interval_start_min"]],
                    groups=[dict(key=k, label=l, kw=kw(cols)) for k, l, cols in GROUPS],
                    detail=[dict(key=k, label=END_USES[k][0], boundary=END_USES[k][1], kwh=round(S[f"kwh_{k}"], 1))
                            for k in END_USES],
                    total_kw=kw(list(END_USES)), emissions=iv["emissions_kgco2e"].round(2).tolist(),
                    tout=iv["outdoor_temp_c"].round(2).tolist(),
                    hotel_kwh_day=round(S["hotel_kwh"] / sim.days, 1),
                    shape=(iv.groupby(iv.interval_start_min % 1440)["total_kwh"].mean()
                           / iv["total_kwh"].mean()).round(4).tolist()),
        queues=dict(t=[s["t"] for s in sim.snapshots], fac=fac, labels=[sim.fac[f].label for f in fac],
                    q=[[s["queue"][f] for s in sim.snapshots] for f in fac]),
        queue_stats=records(qs),
        hk_daily=records(pd.DataFrame(sim.hk_daily)),
        laundry=records(pd.DataFrame(sim.laundry_batches)) if sim.laundry_batches else [],
        rooms=room_arrays(sim),
        groups=groups,
        checks=[{**c, "passed": bool(c["passed"])} for c in verify(sim)],
    )


def decisions(sim):
    out = {}
    for d in sim.decision_log:
        out.setdefault(str(d["gid"]), []).append(dict(t=d["t"], chosen=d["chosen"], hunger=d["hunger"],
                                                      fatigue=d["fatigue"], heat=d["heat"],
                                                      rec=d["recommendation"], options=d["options"][:5]))
    return out


def write_csvs(slug, sim):
    d = DATA / "csv"
    d.mkdir(parents=True, exist_ok=True)
    files = {"interval_electricity": interval_df(sim), "queue_stats": queue_stats_df(sim),
             "itineraries": itinerary_df(sim), "groups": groups_df(sim), "summary": pd.DataFrame([summary(sim)]),
             "housekeeping": pd.DataFrame(sim.hk_log), "laundry_batches": pd.DataFrame(sim.laundry_batches),
             "verification": pd.DataFrame(verify(sim))}
    for k, df in files.items():
        df.to_csv(d / f"{slug}_{k}.csv", index=False, encoding="utf-8-sig")
    return [f"{slug}_{k}.csv" for k in files]


def static_data():
    month = 6
    ref = self_reported_monthly_grid_kwh()
    days_in = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    try:
        prof = reference_daily_profile(month=month).round(4).tolist()
    except Exception:  # reference file missing: the tab shows the model only
        prof = None
    read = lambda f: records(pd.read_csv(ROOT / f).fillna("")) if (ROOT / f).exists() else []
    rules = read("rule_registry.csv")
    keep = ["rule_id", "sheet", "row", "original_text", "category", "interpretation", "status", "model_link"]
    return dict(
        assumptions=read("assumptions.csv"), verified_facts=read("verified_facts.csv"),
        rules=[{k: r.get(k, "") for k in keep} for r in rules],
        provenance=(ROOT / "data_provenance.md").read_text(encoding="utf-8"),
        reference=dict(month=month, excel_kwh_month=ref["kwh"][month - 1] if ref else None,
                       excel_kwh_day=round(ref["kwh"][month - 1] / days_in, 1) if ref else None,
                       excel_sheet=ref["sheet"] if ref else None, excel_row=ref["row"] if ref else None,
                       nrel_shape=prof))


def compare_data():
    s, p = ROOT / "outputs" / "scenario_summary.csv", ROOT / "outputs" / "per_seed_comparison.csv"
    if not s.exists():
        return None
    for f in (s, p):
        shutil.copy(f, DATA / "csv" / f.name)
    keep = ["scenario", "seed"] + [k for k, _ in COMPARE_METRICS]
    per = pd.read_csv(p)
    return dict(summary=records(pd.read_csv(s)), per_seed=records(per[[c for c in keep if c in per]]),
                metrics=[dict(key=k, label=l) for k, l in COMPARE_METRICS],
                scenarios=[dict(key=k, label=v["label_vi"]) for k, v in SCENARIOS.items()])


def main():
    SITE.mkdir(exist_ok=True)
    for p in SITE.iterdir():               # clear old build, keep the Vercel project link
        if p.name == ".vercel":
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    (SITE / "map").mkdir(parents=True)
    (DATA / "csv").mkdir(parents=True)
    index = []
    for name in SCENARIOS:
        sim = run_scenario(name, seed=SEED)
        slug = SLUG[name]
        (SITE / "map" / f"{slug}.html").write_text(render_html(build_payload(sim, SCENARIOS[name]["label_vi"])),
                                                   encoding="utf-8")
        dump = lambda obj, f: (DATA / f).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                                                    encoding="utf-8")
        dump(scenario_data(name, sim), f"{slug}.json")
        (DATA / "dec").mkdir(exist_ok=True)
        dec = decisions(sim)
        for k in range(0, max(1, len(sim.groups)), DEC_SHARD):     # shards of 25 groups, loaded on demand
            dump({g: dec[g] for g in map(str, range(k, k + DEC_SHARD)) if g in dec}, f"dec/{slug}_{k // DEC_SHARD}.json")
        csvs = write_csvs(slug, sim)
        index.append(dict(key=name, slug=slug, label=SCENARIOS[name]["label_vi"], csv=csvs))
        print(f"  {slug}: map + data ({sim.runtime_s:.1f}s sim)")
    st = static_data()
    st["scenarios"] = index
    st["compare"] = compare_data()
    (DATA / "static.json").write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(ROOT / "web" / "dashboard.html", SITE / "index.html")
    # old URLs from the first deployment → dashboard tabs
    redirects = [{"source": "/compare", "destination": "/?s=baseline#compare", "permanent": False}]
    redirects += [{"source": f"/{slug}", "destination": f"/?s={key}#map", "permanent": False}
                  for key, slug in SLUG.items() if key != "baseline"]
    (SITE / "vercel.json").write_text(json.dumps({"cleanUrls": True, "redirects": redirects}, indent=2))
    files = [f for f in SITE.rglob("*") if f.is_file()]
    print(f"site/ built: {len(files)} files, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
