"""Command-line entry point.

Examples:
  python run_simulation.py                              # 533-room baseline, seed 42, 1 day
  python run_simulation.py --scenario combined --seed 7
  python run_simulation.py --compare --seeds 10         # 6 scenarios x 10 paired seeds
  python run_simulation.py --verify-suite               # diagnostic / full / stress / zero-occupancy runs
"""
import argparse
import json
import platform
import sys
import time
from pathlib import Path

import pandas as pd

from simulation.config import ROOT
from simulation.metrics import export_run, paired_comparison, summary, verify
from simulation.scenarios import SCENARIOS, run_scenario
from simulation.viewer import write_viewer

OUT = ROOT / "outputs"


def overrides_from_args(a):
    ov = {"horizon_days": a.days, "hotel": {"occupancy": a.occupancy}}
    if a.emission_factor is not None:
        ov["emissions"] = {"factor_kg_per_kwh": a.emission_factor}
    return ov


def cmd_run(a):
    t0 = time.perf_counter()
    sim = run_scenario(a.scenario, seed=a.seed, overrides=overrides_from_args(a))
    name = f"{a.scenario}_seed{a.seed}"
    out = export_run(sim, OUT / name, name)
    viewer = write_viewer(sim, OUT / name / f"{name}_resort3d.html", SCENARIOS[a.scenario]["label_vi"])
    s = summary(sim)
    checks = verify(sim)
    print(f"Scenario {a.scenario} | seed {a.seed} | rooms {s['rooms']} | occupied {s['occupied_rooms']} | "
          f"groups {s['guest_groups']} | guests {s['guests']}")
    for k in ["total_kwh", "hotel_kwh", "park_kwh", "peak_15min_kw", "emissions_kgco2e",
              "hotel_kwh_per_occupied_room_night", "park_entries", "completed_attraction_visits_persons",
              "attraction_mean_wait_min", "abandonments_groups", "meals_served_persons", "rooms_pending_cleaning",
              "laundry_pending_kg", "occupied_room_hours_above_comfort", "median_return_to_hotel"]:
        v = s[k]
        print(f"  {k:40s} {v:,.1f}" if isinstance(v, float) else f"  {k:40s} {v}")
    failed = [c for c in checks if not c["passed"]]
    print(f"Verification: {len(checks) - len(failed)}/{len(checks)} checks passed")
    for c in failed:
        print("  FAILED:", c["check"], c["detail"])
    print(f"Simulation runtime {sim.runtime_s:.2f} s (total incl. export {time.perf_counter() - t0:.2f} s)")
    print(f"Outputs: {out}\n3D viewer: {viewer}")


def cmd_compare(a):
    rows = []
    seeds = list(range(a.seed, a.seed + a.seeds))
    scen = a.scenarios.split(",") if a.scenarios else list(SCENARIOS)
    t0 = time.perf_counter()
    for sc in scen:
        for sd in seeds:
            sim = run_scenario(sc, seed=sd, overrides=overrides_from_args(a))
            rows.append({"scenario": sc, "scenario_vi": SCENARIOS[sc]["label_vi"], **summary(sim)})
            print(f"  {sc:16s} seed {sd:3d}  total {rows[-1]['total_kwh']:9.1f} kWh  wait {rows[-1]['attraction_mean_wait_min']:5.2f} min"
                  f"  ({sim.runtime_s:.2f}s)", flush=True)
    per_seed = pd.DataFrame(rows)
    comp = paired_comparison(per_seed)
    OUT.mkdir(exist_ok=True)
    per_seed.to_csv(OUT / "per_seed_comparison.csv", index=False, encoding="utf-8-sig")
    comp.to_csv(OUT / "scenario_summary.csv", index=False, encoding="utf-8-sig")
    print(f"\n{len(rows)} runs in {time.perf_counter() - t0:.1f} s -> outputs/per_seed_comparison.csv, outputs/scenario_summary.csv")
    key = comp[comp.metric.isin(["total_kwh", "emissions_kgco2e", "attraction_mean_wait_min",
                                 "occupied_room_hours_above_comfort", "laundry_pending_kg"])]
    print(key[["scenario", "metric", "mean", "sd", "mean_paired_diff", "sd_paired_diff", "mean_pct_diff"]]
          .to_string(index=False, float_format=lambda v: f"{v:,.2f}"))


def cmd_verify_suite(a):
    runs = [
        ("diagnostic_60_rooms", dict(hotel={"rooms": 60, "floors": 3, "occupancy": 0.8},
                                     housekeeping={"workers": 4}), True),
        ("full_533_80pct", {}, False),
        ("stress_533_100pct", dict(hotel={"occupancy": 1.0}), False),
        ("boundary_533_0pct", dict(hotel={"occupancy": 0.0}), False),
    ]
    results = []
    for name, ov, diag in runs:
        sim = run_scenario("baseline", seed=a.seed, overrides=ov, allow_room_override=diag)
        s = summary(sim)
        checks = verify(sim)
        ok = sum(c["passed"] for c in checks)
        results.append(dict(run=name, rooms=s["rooms"], occupied_rooms=s["occupied_rooms"], guests=s["guests"],
                            total_kwh=round(s["total_kwh"], 1), runtime_s=round(sim.runtime_s, 2),
                            checks_passed=ok, checks_total=len(checks),
                            failed="; ".join(c["check"] for c in checks if not c["passed"])))
        print(results[-1])
    # zero emission factor and reproducibility / identical-policy pairing
    z = summary(run_scenario("baseline", seed=a.seed, overrides={"emissions": {"factor_kg_per_kwh": 0.0}}))
    r1 = summary(run_scenario("baseline", seed=a.seed))
    r2 = summary(run_scenario("baseline", seed=a.seed))
    same = all(r1[k] == r2[k] for k in r1 if k != "runtime_s")
    results.append(dict(run="zero_emission_factor", emissions_kgco2e=z["emissions_kgco2e"],
                        checks_passed=int(z["emissions_kgco2e"] == 0), checks_total=1))
    results.append(dict(run="reproducibility_same_seed_same_policy", identical=same,
                        checks_passed=int(same), checks_total=1))
    print(results[-2], results[-1], sep="\n")
    OUT.mkdir(exist_ok=True)
    pd.DataFrame(results).to_csv(OUT / "verification_suite.csv", index=False, encoding="utf-8-sig")
    (OUT / "machine.json").write_text(json.dumps(dict(python=sys.version, platform=platform.platform(),
                                                      processor=platform.processor()), indent=2))


def main():
    p = argparse.ArgumentParser(description="Virtual Hotel Monitoring: synthetic 533-room hotel + amusement park ABM")
    p.add_argument("--scenario", default="baseline", choices=list(SCENARIOS))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--occupancy", type=float, default=0.80)
    p.add_argument("--emission-factor", type=float, default=None, help="kg CO2e per kWh (illustrative default 0.60)")
    p.add_argument("--compare", action="store_true")
    p.add_argument("--seeds", type=int, default=10)
    p.add_argument("--scenarios", default=None, help="comma-separated subset for --compare")
    p.add_argument("--verify-suite", action="store_true")
    a = p.parse_args()
    if a.verify_suite:
        cmd_verify_suite(a)
    elif a.compare:
        cmd_compare(a)
    else:
        cmd_run(a)


if __name__ == "__main__":
    main()
