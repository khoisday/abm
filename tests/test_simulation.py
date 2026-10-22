import math

import numpy as np
import pytest

from simulation.config import load_config
from simulation.engine import Simulation
from simulation.metrics import interval_df, summary, verify
from simulation.scenarios import run_scenario


@pytest.fixture(scope="module")
def base():
    return run_scenario("baseline", seed=42)


def test_533_rooms_and_occupancy(base):
    assert base.rooms.n == 533
    assert int(base.rooms.sold.sum()) == round(533 * 0.8) == 426
    rooms = [g.room for g in base.groups if g.hotel == "island"]
    assert len(rooms) == len(set(rooms)) == 426
    assert all(base.rooms.sold[r] for r in rooms)


def test_room_count_cannot_be_silently_reduced():
    with pytest.raises(ValueError):
        load_config({"hotel": {"rooms": 100}})


def test_group_composition(base):
    for g in base.groups:
        assert g.adults >= 1
        assert g.adults + g.children == g.size
        assert 1 <= g.size <= 5
    assert len({g.category for g in base.groups}) >= 3


def test_all_verification_checks_pass(base):
    failed = [c for c in verify(base) if not c["passed"]]
    assert not failed, failed


def test_visitor_conservation(base):
    persons = sum(g.size for g in base.groups)
    for s in base.snapshots:
        assert sum(s["state_persons"].values()) == persons


def test_travel_takes_time(base):
    for gid, a, b, start, end, *_ in base.walk_segments:
        if a != b:
            assert end > start
    for tr in base.trips:
        assert tr["arrive_t"] > tr["depart_t"]


def test_capacities_respected(base):
    for s in base.snapshots:
        for fid, active in s["active"].items():
            assert active <= base.fac[fid].capacity
    assert all(t["persons"] <= base.lines[t["line"]]["cap"] for t in base.trips)


def test_identity_preserved_across_hotel_and_park(base):
    in_park = {gid for _, gid, e, *_ in base.events if e == "enter_park"}
    back = {gid for _, gid, e, *_ in base.events if e in ("return_hotel", "leave_site")}
    assert in_park and in_park <= back
    for gid in in_park:
        g = base.groups[gid]
        assert g.gid == gid and g.state == ("OFFSITE" if g.hotel == "day" else "FINISHED")


def test_workers_no_simultaneous_tasks(base):
    for w in base.workers:
        h = sorted(w.task_history)
        assert all(a[1] <= b[0] for a, b in zip(h, h[1:]))


def test_electricity_accounting(base):
    iv = interval_df(base)
    from simulation.energy import END_USES
    end_uses = [f"{k}_kwh" for k in END_USES]
    assert np.allclose(iv[end_uses].sum(axis=1), iv["total_kwh"])
    assert math.isclose((iv["avg_power_kw"] * 0.25).sum(), iv["total_kwh"].sum(), rel_tol=1e-9)
    assert np.allclose(iv["emissions_kgco2e"], iv["total_kwh"] * 0.60)
    assert iv["room_hvac_kwh"].sum() > 0


def test_vacant_rooms_in_accounting(base):
    vacant = ~base.rooms.sold
    assert vacant.sum() == 107
    assert float(base.snapshots[-1]["room_light_kw"][vacant].sum()) > 0


def test_zero_emission_factor():
    s = summary(run_scenario("baseline", seed=1, overrides={"emissions": {"factor_kg_per_kwh": 0.0}}))
    assert s["emissions_kgco2e"] == 0


def test_reproducible_and_identical_policy_pairing():
    a = summary(run_scenario("baseline", seed=7))
    b = summary(run_scenario("baseline", seed=7))
    a.pop("runtime_s"), b.pop("runtime_s")
    assert a == b


def test_exogenous_inputs_matched_across_policies():
    a = Simulation(load_config({"seed": 3}))
    b = Simulation(load_config({"seed": 3, "policies": {"occupancy_hvac": True, "park_recommendations": True,
                                                        "housekeeping_coordination": True, "ride_dispatch": True}}))
    assert [(g.room, g.size, g.pref) for g in a.groups] == [(g.room, g.size, g.pref) for g in b.groups]
    assert np.array_equal(a.weather.temp, b.weather.temp)
    assert np.array_equal(a.rooms.temp, b.rooms.temp)


def test_zero_occupancy_boundary():
    s = run_scenario("baseline", seed=42, overrides={"hotel": {"occupancy": 0.0}})
    assert not [g for g in s.groups if g.hotel == "island"]
    assert summary(s)["total_kwh"] > 0          # base loads and vacant-room standby remain
    assert all(c["passed"] for c in verify(s))


def test_full_occupancy_stress():
    s = run_scenario("baseline", seed=42, overrides={"hotel": {"occupancy": 1.0}})
    assert len([g for g in s.groups if g.hotel == "island"]) == 533
    assert all(c["passed"] for c in verify(s))


def test_closed_attractions_admit_nobody(base):
    for t, gid, e, where, *_ in base.events:
        if e == "admit":
            f = base.fac[where]
            assert f.is_open(t) or f.in_grace(t) or f.kind == "show"


def test_outstanding_work_reported(base):
    s = summary(base)
    assert "rooms_pending_cleaning" in s and "laundry_pending_kg" in s and "laundry_completion_kwh_estimate" in s


def test_multi_day_resets_cleaning():
    s = run_scenario("baseline", seed=5, overrides={"horizon_days": 2})
    assert len(s.hk_daily) == 2
    assert s.hk_daily[1]["cleaned"] > 0
    assert all(c["passed"] for c in verify(s))


def test_island_floor_plan_has_533_rooms():
    from simulation.config import load_config
    from simulation.layout import floor_plan, room_layout
    cfg = load_config()
    plan = floor_plan(cfg["hotel"])
    assert sum(f["count"] for f in plan) == 533 and [f["n"] for f in plan] == list(range(3, 21))
    lay = room_layout(cfg["hotel"])
    assert len(set(lay["number"])) == 533
    assert sum(f["count"] for f in floor_plan(cfg["mainland_hotel"])) == cfg["mainland_hotel"]["rooms"]


def test_mainland_hotel_and_day_visitors(base):
    mc = base.cfg["mainland_hotel"]
    assert base.mrooms.n == mc["rooms"] and int(base.mrooms.sold.sum()) == round(mc["rooms"] * mc["occupancy"])
    mainland = [g for g in base.groups if g.hotel == "mainland"]
    assert len({g.room for g in mainland}) == len(mainland) and all(base.mrooms.sold[g.room] for g in mainland)
    day = [g for g in base.groups if g.hotel == "day"]
    assert sum(g.size for g in day) == base.cfg["population"]["external_day_visitors"]
    assert all(g.room == -1 and g.state == "OFFSITE" for g in day)
    left = {gid for _, gid, e, *_ in base.events if e == "leave_site"}
    arrived = {gid for _, gid, e, *_ in base.events if e == "arrive_site"}
    assert arrived and left == arrived


def test_cable_car_and_boats_operate(base):
    cc, bc = base.cfg["cable_car"], base.cfg["boats"]
    from simulation.config import hhmm
    cable = [t for t in base.trips if t["line"] == "cable"]
    boats = [t for t in base.trips if t["line"] == "boat"]
    assert cable and boats
    assert all(0 < t["persons"] <= cc["cabin_capacity_persons"] for t in cable)
    assert all(0 < t["persons"] <= bc["capacity_persons"] for t in boats)
    assert all(t["depart_t"] % 1440 >= hhmm(cc["first_departure_hhmm"]) for t in cable)
    assert all(t["arrive_t"] - t["depart_t"] == cc["ride_min"] for t in cable)
    riders = {gid for _, gid, e, where, *_ in base.events if e == "board" and where in cc["stops"]}
    assert {base.groups[g].hotel for g in riders} >= {"mainland", "day"}
    s = summary(base)
    assert s["cable_car_kwh"] > 0 and math.isclose(s["boat_fuel_l"], len(boats) * bc["fuel_l_per_trip"])


def test_island_guests_walk_and_mainland_guests_ride_the_shuttle(base):
    stops = set(base.cfg["transport"]["stops"])
    shuttle = {gid for _, gid, e, where, *_ in base.events if e == "board" and where in stops}
    assert shuttle and {base.groups[g].hotel for g in shuttle} == {"mainland"}
    walked_in = {gid for _, gid, e, where, *_ in base.events if e == "walk" and where.endswith("->PARK_STOP")
                 and base.groups[gid].hotel == "island"}
    entered = {gid for _, gid, e, *_ in base.events if e == "enter_park" and base.groups[gid].hotel == "island"}
    assert entered and entered <= walked_in | {gid for _, gid, e, where, *_ in base.events if e == "alight" and where == "PIER_ISLAND"}
    left = {gid for _, gid, e, *_ in base.events if e == "leave_park"}
    assert entered <= left | {g.gid for g in base.groups if g.zone == "park"}


def test_boat_fuel_kept_out_of_electricity_emissions(base):
    iv = interval_df(base)
    s = summary(base)
    assert s["boat_fuel_kgco2e"] > 0
    assert math.isclose(s["emissions_kgco2e"], float((iv["total_kwh"] * iv["emission_factor_kg_per_kwh"]).sum()))
