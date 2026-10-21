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
    rooms = [g.room for g in base.groups]
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
    assert all(t["persons"] <= base.cfg["transport"]["capacity_persons"] for t in base.trips)


def test_identity_preserved_across_hotel_and_park(base):
    in_park = {gid for _, gid, e, *_ in base.events if e == "enter_park"}
    back = {gid for _, gid, e, *_ in base.events if e == "return_hotel"}
    assert in_park and in_park <= back
    for gid in list(in_park)[:20]:
        assert base.groups[gid].gid == gid and base.groups[gid].state == "FINISHED"


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
    assert not s.groups
    assert summary(s)["total_kwh"] > 0          # base loads and vacant-room standby remain
    assert all(c["passed"] for c in verify(s))


def test_full_occupancy_stress():
    s = run_scenario("baseline", seed=42, overrides={"hotel": {"occupancy": 1.0}})
    assert len(s.groups) == 533
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
