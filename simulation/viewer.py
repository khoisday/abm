"""Builds the data payload and standalone HTML for the 3D pixel resort map.

Everything the map animates comes from simulation output: 5-minute snapshots
(counts, queues, room states), exact walk segments and shuttle trips.
"""
import json
from pathlib import Path

import numpy as np

from .config import ROOT, fmt_min
from .energy import END_USES

TEMPLATE = ROOT / "web" / "resort3d_template.html"


def room_state_string(s, sold, thr):
    occ, asleep, clean, ctrl = s["room_occ"], s["room_asleep"], s["room_clean"], s["room_control"]
    hot = occ & (s["room_temp"] > thr)
    code = np.full(len(sold), "0")
    code[sold & ~occ & ~ctrl] = "1"
    code[sold & ~occ & ctrl] = "2"
    code[occ & ~asleep] = "3"
    code[occ & asleep] = "4"
    code[hot] = "6"
    code[clean] = "5"
    return "".join(code)


def build_payload(sim, scenario_label="Cơ sở (baseline)"):
    cfg = sim.cfg
    nodes = list(cfg["network"]["nodes"])
    nidx = {n: i for i, n in enumerate(nodes)}
    fac_ids = list(sim.fac)
    minute_kw = sum(v[:sim.T] for v in sim.meter.kwh.values()) * 60.0
    cum_kwh = np.cumsum(minute_kw / 60.0)
    ef = cfg["emissions"]["factor_kg_per_kwh"]
    thr = cfg["rooms"]["comfort_threshold_c"]
    sold = sim.rooms.sold
    frames = []
    for s in sim.snapshots:
        t = s["t"]
        occ = s["room_occ"] & sold
        frames.append(dict(
            t=t,
            n=[s["node_persons"].get(n, 0) for n in nodes],
            q=[s["queue"][f] for f in fac_ids],
            a=[s["active"][f] for f in fac_ids],
            sq=[s["stop_queue"]["HOTEL_STOP"], s["stop_queue"]["PARK_STOP"]],
            iv=s["in_vehicle"], ip=s["in_park"],
            st=s["state_persons"],
            rs=room_state_string(s, sold, thr),
            occ=int(occ.sum()), hv=int((s["room_hvac"] & sold).sum()), cl=int(s["room_clean"].sum()),
            tm=round(float(s["room_temp"][occ].mean()), 2) if occ.any() else None,
            kw=round(float(minute_kw[t]), 1), ck=round(float(cum_kwh[t]), 1),
            co2=round(float(cum_kwh[t] * ef), 1), to=round(s["t_out"], 1)))
    routes = {}
    for a in nodes:
        for b in nodes:
            if a != b and sim.net.zone(a) == sim.net.zone(b):
                routes[f"{nidx[a]}>{nidx[b]}"] = [nidx[x] for x in sim.net.route(a, b)]
    walks = [[gid, nidx[a], nidx[b], s, e, p, r] for gid, a, b, s, e, p, r in sim.walk_segments if e > s]
    trips = [[tr["depart_t"], tr["arrive_t"], 0 if tr["origin"] == "HOTEL_STOP" else 1, tr["persons"]]
             for tr in sim.trips]
    step = cfg["reporting_interval_min"]
    iv = sim.meter.intervals(step)
    power15 = (sum(iv.values()) / (step / 60.0)).round(1).tolist()
    groups = []
    for g in sim.groups:
        groups.append(dict(id=g.gid, room=int(g.room), size=g.size, cat=g.category,
                           it=[[a, b, act, w] for a, b, act, node, w in g.history]))
    totals = {k: round(float(v[:sim.T].sum()), 1) for k, v in sim.meter.kwh.items()}
    return dict(
        meta=dict(seed=cfg["seed"], days=sim.days, T=sim.T, rooms=int(sim.rooms.n), floors=cfg["hotel"]["floors"],
                  occupied=int(sold.sum()), guests=int(sum(g.size for g in sim.groups)), groups=len(sim.groups),
                  scenario=scenario_label, policies=cfg["policies"], ef=ef, snapshot_min=5,
                  transport_included=cfg["transport"]["include_transport_energy"],
                  ride_min=cfg["transport"]["ride_min"], start_date=cfg["start_date"],
                  machines=cfg["laundry"]["machines"]),
        laundry=[[b["start"], b["end"], b["machine"], b["load_kg"]] for b in sim.laundry_batches],
        nodes=[dict(id=n, label=cfg["network"]["nodes"][n]["label_vi"], zone=cfg["network"]["nodes"][n]["zone"])
               for n in nodes],
        facilities=[dict(id=f, label=sim.fac[f].label, node=nidx[sim.fac[f].node], kind=sim.fac[f].kind,
                         cap=sim.fac[f].capacity, cycle=sim.fac[f].cycle) for f in fac_ids],
        edges=[[nidx[a], nidx[b]] for a, b, _ in cfg["network"]["walk_edges_min"]],
        frames=frames, routes=routes, walks=walks, trips=trips, power15=power15, step=step,
        end_uses={k: [END_USES[k][0], END_USES[k][1], totals[k]] for k in END_USES},
        groups=groups,
    )


def render_html(payload):
    html = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return html.replace("/*__SIM_DATA__*/null", data)


def write_viewer(sim, path, scenario_label="Cơ sở (baseline)"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(build_payload(sim, scenario_label)), encoding="utf-8")
    return path


def fmt(t):
    return fmt_min(t)
