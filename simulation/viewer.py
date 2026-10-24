"""Builds the data payload and standalone HTML for the 3D resort map.

Everything the map animates comes from simulation output: 5-minute snapshots
(counts, queues, room states of both hotels), 15-minute room temperatures, exact
walk segments and the shuttle, cable-car and boat trips. The map plays one day on a
loop, so a multi-day run is cut to its first day here.
"""
import base64
import json
import re
from pathlib import Path

import numpy as np

from .config import ROOT, fmt_min, hhmm
from .energy import END_USES
from .layout import floor_plan

LINES = ["shuttle", "cable", "boat"]
HOTEL_CODE = {"island": 0, "mainland": 1, "day": 2}

TEMPLATE = ROOT / "web" / "resort3d_template.html"
I18N_JS = ROOT / "web" / "i18n.js"
I18N_DICT = ROOT / "web" / "i18n_dict.json"


def room_state_string(s, sold, thr, prefix="room_"):
    occ, asleep, ctrl = s[prefix + "occ"], s[prefix + "asleep"], s[prefix + "control"]
    clean = s.get(prefix + "clean", np.zeros(len(sold), dtype=bool))
    hot = occ & (s[prefix + "temp"] > thr)
    code = np.full(len(sold), "0")
    code[sold & ~occ & ~ctrl] = "1"
    code[sold & ~occ & ctrl] = "2"
    code[occ & ~asleep] = "3"
    code[occ & asleep] = "4"
    code[hot] = "6"
    code[clean] = "5"
    return "".join(code)


def temps_b64(snaps, key, every):
    """Room temperatures every `every` snapshots as uint8 ((degC - 15) * 10), base64 per row."""
    out = []
    for s in snaps[::every]:
        v = np.clip(np.round((s[key] - 15.0) * 10.0), 0, 255).astype(np.uint8)
        out.append(base64.b64encode(v.tobytes()).decode("ascii"))
    return out


def hotel_meta(hcfg, sold, n_groups, guests):
    return dict(label=hcfg["label_vi"], rooms=int(hcfg["rooms"]), occupied=int(sold), groups=n_groups, guests=guests,
                floors=floor_plan(hcfg), amen=[dict(n=a["floor"], title=a["title_vi"], fac=a["facilities"])
                                              for a in hcfg.get("amenity_floors", [])],
                rows=hcfg.get("row_labels_vi", ["", ""]))


def build_payload(sim, scenario_label="Cơ sở (baseline)"):
    cfg = sim.cfg
    T = min(sim.T, 1440)   # first day only
    snaps = [s for s in sim.snapshots if s["t"] < T]
    sim_groups = [g for g in sim.groups if not g.history or g.history[0][0] < T]
    nodes = list(cfg["network"]["nodes"])
    nidx = {n: i for i, n in enumerate(nodes)}
    fac_ids = list(sim.fac)
    minute_kw = sum(v[:T] for v in sim.meter.kwh.values()) * 60.0
    cum_kwh = np.cumsum(minute_kw / 60.0)
    ef = cfg["emissions"]["factor_kg_per_kwh"]
    thr = cfg["rooms"]["comfort_threshold_c"]
    sold = sim.rooms.sold
    M = sim.mrooms
    stops = [st for lid in LINES for st in sim.lines[lid]["stops"]]
    frames = []
    for s in snaps:
        t = s["t"]
        occ = s["room_occ"] & sold
        frames.append(dict(
            t=t,
            n=[s["node_persons"].get(n, 0) for n in nodes],
            q=[s["queue"][f] for f in fac_ids],
            a=[s["active"][f] for f in fac_ids],
            sq=[s["stop_queue"][st] for st in stops],
            iv=s["in_vehicle"], ivl=[s["in_vehicle_by_line"][lid] for lid in LINES], ip=s["in_park"],
            st=s["state_persons"],
            rs=room_state_string(s, sold, thr),
            mrs=room_state_string(s, M.sold, thr, "m_room_") if M is not None else "",
            mhv=int((s["m_room_hvac"] & M.sold).sum()) if M is not None else 0,
            mocc=int((s["m_room_occ"] & M.sold).sum()) if M is not None else 0,
            occ=int(occ.sum()), hv=int((s["room_hvac"] & sold).sum()), cl=int(s["room_clean"].sum()),
            tm=round(float(s["room_temp"][occ].mean()), 2) if occ.any() else None,
            kw=round(float(minute_kw[t]), 1), ck=round(float(cum_kwh[t]), 1),
            co2=round(float(cum_kwh[t] * ef), 1), to=round(s["t_out"], 1)))
    routes = {}
    for a in nodes:
        for b in nodes:
            if a != b and sim.net.zone(a) == sim.net.zone(b):
                routes[f"{nidx[a]}>{nidx[b]}"] = [nidx[x] for x in sim.net.route(a, b)]
    walks = [[gid, nidx[a], nidx[b], s, e, p, r] for gid, a, b, s, e, p, r in sim.walk_segments if e > s and s < T]
    trips = [[tr["depart_t"], tr["arrive_t"], 0 if tr["origin"] == sim.lines[tr["line"]]["stops"][0] else 1,
              tr["persons"], LINES.index(tr["line"])] for tr in sim.trips if tr["depart_t"] < T]
    boat_fuel_l = sum(1 for tr in sim.trips if tr["line"] == "boat" and tr["depart_t"] < T) * cfg["boats"]["fuel_l_per_trip"]
    step = cfg["reporting_interval_min"]
    iv = sim.meter.intervals(step)
    power15 = (sum(iv.values()) / (step / 60.0)).round(1).tolist()[:T // step]
    groups = []
    for g in sim_groups:
        groups.append(dict(id=g.gid, h=HOTEL_CODE[g.hotel], room=int(g.room), size=g.size, cat=g.category,
                           ad=g.adults, ch=g.children, sp=g.guest_setpoint,
                           it=[[a, min(b, T), act, w] for a, b, act, node, w in g.history if a < T]))
    totals = {k: round(float(v[:T].sum()), 1) for k, v in sim.meter.kwh.items()}
    return dict(
        meta=dict(seed=cfg["seed"], days=1, run_days=sim.days, T=T, rooms=int(sim.rooms.n), floors=cfg["hotel"]["floors"],
                  occupied=int(sold.sum()), guests=int(sum(g.size for g in sim_groups)), groups=len(sim_groups),
                  scenario=scenario_label, policies=cfg["policies"], ef=ef, snapshot_min=5,
                  transport_included=cfg["transport"]["include_transport_energy"],
                  ride_min=cfg["transport"]["ride_min"], start_date=cfg["start_date"],
                  machines=cfg["laundry"]["machines"],
                  occupancy=int(round(cfg["hotel"]["occupancy"] * 100)),
                  park_open=hhmm(cfg["park_common"]["park_open_hhmm"]),
                  park_close=hhmm(cfg["park_common"]["park_close_hhmm"]),
                  shuttle_interval=cfg["transport"]["interval_min"],
                  shuttle_first=hhmm(cfg["transport"]["first_departure_hhmm"]),
                  shuttle_last=hhmm(cfg["transport"]["last_departure_hhmm"]),
                  comfort=cfg["rooms"]["comfort_threshold_c"],
                  stops=stops, line_ids=LINES,
                  lines={lid: dict(stops=list(L["stops"]), ride=L["ride"], interval=L["interval"], cap=L["cap"],
                                   vehicles=L["vehicles"], first=min(L["first"].values()), last=max(L["last"].values()))
                         for lid, L in sim.lines.items()},
                  hotels=[hotel_meta(cfg["hotel"], int(sold.sum()), *_count(sim_groups, "island")),
                          hotel_meta(cfg["mainland_hotel"], int(M.sold.sum()) if M is not None else 0,
                                     *_count(sim_groups, "mainland"))],
                  day_visitors=sum(g.size for g in sim_groups if g.hotel == "day"),
                  boat_fuel_l=round(boat_fuel_l, 1),
                  boat_fuel_kgco2e=round(boat_fuel_l * cfg["boats"]["fuel_kgco2e_per_l"], 1),
                  temp_every=3 * 5),
        laundry=[[b["start"], b["end"], b["machine"], b["load_kg"]] for b in sim.laundry_batches if b["start"] < T],
        nodes=[dict(id=n, label=cfg["network"]["nodes"][n]["label_vi"], zone=cfg["network"]["nodes"][n]["zone"])
               for n in nodes],
        facilities=[dict(id=f, label=sim.fac[f].label, node=nidx[sim.fac[f].node], kind=sim.fac[f].kind,
                         cap=sim.fac[f].capacity, cycle=sim.fac[f].cycle, win=[list(w) for w in sim.fac[f].windows],
                         site=sim.fac[f].spec.get("site", "island"), amenity=bool(sim.fac[f].spec.get("hotel_amenity")))
                    for f in fac_ids],
        edges=[[nidx[a], nidx[b]] for a, b, _ in cfg["network"]["walk_edges_min"]],
        frames=frames, routes=routes, walks=walks, trips=trips, power15=power15, step=step,
        end_uses={k: [END_USES[k][0], END_USES[k][1], totals[k]] for k in END_USES},
        groups=groups,
        temps=dict(island=temps_b64(snaps, "room_temp", 3),
                   mainland=temps_b64(snaps, "m_room_temp", 3) if M is not None else []),
    )


def _count(groups, kind):
    gs = [g for g in groups if g.hotel == kind]
    return len(gs), sum(g.size for g in gs)


def i18n_script():
    """The VI/EN/RU switch (web/i18n.js) with its translation table inlined, as one <script> element."""
    table = json.loads(I18N_DICT.read_text(encoding="utf-8")) if I18N_DICT.exists() else {}
    table = json.dumps(table, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return "<script>\n" + I18N_JS.read_text(encoding="utf-8").replace("/*__I18N_DICT__*/{}", table, 1) + "</script>"


def with_i18n(html):
    return html.replace("<!--__I18N__-->", i18n_script(), 1)


def render_html(payload):
    html = with_i18n(TEMPLATE.read_text(encoding="utf-8"))
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # tolerant of formatters that put a space before "null"
    return re.sub(r"/\*__SIM_DATA__\*/\s*null", lambda m: data, html, count=1)


def write_viewer(sim, path, scenario_label="Cơ sở (baseline)"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(build_payload(sim, scenario_label)), encoding="utf-8")
    return path


def fmt(t):
    return fmt_min(t)
