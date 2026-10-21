"""Discrete-event simulation on a one-minute clock.

Events (arrivals, admissions, ride dispatches, service completions, housekeeping,
laundry, shuttle departures, guest decisions) sit in a heap; rooms, base loads and
meters are updated every minute in vectorized form. Decisions are only evaluated
for a group at its own decision points.
"""
import heapq
import math
import time as _time
from collections import deque

import numpy as np

from .agents import Housekeeper, draw_daily_plan, generate_population
from .config import hhmm
from .decisions import choose
from .energy import Meter, RoomBank
from .environment import Network, Weather, build_facilities
from .rng import stream

SNAPSHOT_MIN = 5
PARK_FACILITIES = ["THRILL", "FAMILY", "INDOOR", "WATER", "SHOW"]
OTHER_ZONE_STOP = {"hotel": "HOTEL_STOP", "park": "PARK_STOP"}


class Simulation:
    def __init__(self, cfg):
        self.cfg = cfg
        self.seed = int(cfg["seed"])
        self.days = int(cfg["horizon_days"])
        self.T = self.days * 1440
        self.policies = cfg["policies"]
        self.pp = cfg["policy_params"]
        self.w = cfg["decision"]["weights"]
        self.net = Network(cfg)
        self.weather = Weather(cfg, self.seed, self.days)
        self.fac = build_facilities(cfg)
        self.hotel_amenities = [f for f in self.fac.values() if f.spec.get("hotel_amenity")]
        self.groups, sold = generate_population(cfg, self.seed)
        self.rooms = RoomBank(cfg, sold, self.groups, stream(self.seed, "rooms_init"))
        self.meter = Meter(self.T)
        self.heap, self._seq = [], 0
        self.stop_queue = {"HOTEL_STOP": deque(), "PARK_STOP": deque()}
        self.trips, self.events, self.decision_log, self.snapshots, self.rec_log = [], [], [], [], []
        self.walk_segments = []   # (gid, from, to, start, end, persons, returning) for replay
        self.rngs = {}
        self.park_entries = 0
        self.unfinished_demand = []
        self._init_housekeeping()
        self._init_laundry()
        self._schedule_static_events()
        for g in self.groups:
            draw_daily_plan(g, cfg, self.seed, 0)
            g.last_update = 0
            g.hunger, g.fatigue = 25.0, cfg["needs"]["overnight_fatigue_after_sleep"]
            g.state, g.env = "FINISHED", "in"   # asleep in room at 00:00
            self.rooms.persons[g.room] = g.size
            self.push(g.wake_t, "wake", g.gid)

    # ------------------------------------------------------------------ utils
    def push(self, t, kind, *payload):
        self._seq += 1
        heapq.heappush(self.heap, (int(t), self._seq, kind, payload))

    def log(self, t, g, event, where="", detail=""):
        self.events.append((t, g.gid if g is not None else -1, event, where, detail,
                            g.state if g is not None else ""))

    def grng(self, g, name="decision"):
        day = self.t // 1440
        key = (name, g.gid, day)
        if key not in self.rngs:
            self.rngs[key] = stream(self.seed, name, g.gid, day)
        return self.rngs[key]

    def tday(self, t):
        return t % 1440

    # ------------------------------------------------------------ scheduling
    def _schedule_static_events(self):
        tc, lc, hk = self.cfg["transport"], self.cfg["laundry"], self.cfg["housekeeping"]
        for d in range(self.days):
            base = d * 1440
            if d > 0:
                self.push(base, "day_start", d)
            t = base + hhmm(tc["hotel_first_departure_hhmm"])
            while t <= base + 1439:
                self.push(t, "shuttle", "HOTEL_STOP", t <= base + hhmm(tc["hotel_last_departure_hhmm"]))
                t += tc["interval_min"]
            t = base + hhmm(tc["park_first_departure_hhmm"])
            while t <= base + 1439:
                self.push(t, "shuttle", "PARK_STOP", t <= base + hhmm(tc["park_last_departure_hhmm"]))
                t += tc["interval_min"]
            for f in self.fac.values():
                for a, b in f.windows:
                    self.push(base + b + f.closing_grace, "close", f.id)
                for s in f.show_times:
                    self.push(base + s, "show_start", f.id)
            for t in range(base + hhmm(hk["shift_start_hhmm"]), base + hhmm(hk["shift_end_hhmm"]), 5):
                self.push(t, "hk_tick")
            for t in range(base + hhmm(lc["open_hhmm"]), base + hhmm(lc["last_start_hhmm"]) + 1, 5):
                self.push(t, "laundry_tick")
            self.push(base + 1439, "day_end", d)

    # ------------------------------------------------------------ needs/state
    def update_needs(self, g, t):
        dt = t - g.last_update
        if dt <= 0:
            return
        n = self.cfg["needs"]
        g.hunger += n["hunger_rate_per_min"] * dt * (0.3 if g.asleep else 1.0)
        g.fatigue += n["fatigue_rates_per_min"].get(g.state, 0.0) * dt
        if g.env == "out":
            tout = float(self.weather.temp[g.last_update:t].mean())
            g.heat += n["heat_gain_per_min_per_degC_above_26"] * max(0.0, tout - 26.0) * dt
        elif g.env == "shade":
            g.heat -= n["heat_recovery_shaded_per_min"] * dt
        else:
            g.heat -= n["heat_recovery_indoor_per_min"] * dt
        g.hunger = min(100.0, max(0.0, g.hunger))
        g.fatigue = min(100.0, max(0.0, g.fatigue))
        g.heat = min(100.0, max(0.0, g.heat))
        g.last_update = t

    def set_state(self, g, state, t, env):
        self.update_needs(g, t)
        if g.returning and state == "TRAVELING":
            state = "RETURNING"
        g.state, g.env = state, env

    # ------------------------------------------------------------ movement
    def travel(self, g, dest, t, on_arrive):
        if g.node == "ROOMS" and dest != "ROOMS":
            self.leave_room(g, t, on_arrive)
        here_zone = self.net.zone(g.node)
        if here_zone != self.net.zone(dest):
            g.final = (dest, on_arrive)
            self.walk(g, OTHER_ZONE_STOP[here_zone], t, ("board_wait",))
        else:
            self.walk(g, dest, t, on_arrive)

    def walk(self, g, dest, t, on_arrive):
        w = self.net.walk_min(g.node, dest)
        dur = int(math.ceil(w / g.walk_speed)) if w > 0 else 0
        self.set_state(g, "TRAVELING", t, "out" if self.cfg["network"]["outdoor_walking"] else "in")
        g.walk_from, g.destination, g.walk_start, g.walk_end = g.node, dest, t, t + dur
        g.node, g.pending = "WALK", on_arrive
        g.walking_min += dur
        self.walk_segments.append((g.gid, g.walk_from, dest, t, t + dur, g.size, 1 if g.returning else 0))
        self.log(t, g, "walk", f"{g.walk_from}->{dest}", f"{dur} phút")
        self.push(t + dur, "arrive", g.gid)

    def on_arrive(self, g, t):
        g.node = g.destination
        action = g.pending
        g.pending = None
        kind = action[0]
        if kind == "join":
            self.join_queue(g, self.fac[action[1]], t)
        elif kind == "room":
            self.enter_room(g, t, action[1])
        elif kind == "board_wait":
            self.set_state(g, "WAITING_TRANSPORT", t, "out")
            g.wait_start = t
            self.stop_queue[g.node].append(g)
            self.log(t, g, "wait_transport", g.node)
        elif kind == "enter_park":
            g.zone, g.in_park_today, g.park_entry_t = "park", True, t
            self.park_entries += 1
            self.log(t, g, "enter_park", g.node)
            self.decide(g, t)
        elif kind == "arrive_hotel":
            g.zone, g.returning, g.returned_t = "hotel", False, t
            self.log(t, g, "return_hotel", g.node)
            self.decide(g, t)

    def shuttle(self, t, stop, scheduled):
        tc = self.cfg["transport"]
        q = self.stop_queue[stop]
        if not scheduled and not q:
            return
        cap, boarded, remaining = tc["capacity_persons"], [], deque()
        while q:
            g = q.popleft()
            if g.size <= cap:          # FIFO with fill-in by smaller groups (documented)
                cap -= g.size
                boarded.append(g)
            else:
                remaining.append(g)
        self.stop_queue[stop] = remaining
        dest = "PARK_STOP" if stop == "HOTEL_STOP" else "HOTEL_STOP"
        persons = sum(g.size for g in boarded)
        self.trips.append(dict(depart_t=t, arrive_t=t + tc["ride_min"], origin=stop, dest=dest,
                               persons=persons, groups=len(boarded), scheduled=scheduled))
        if tc["include_transport_energy"]:
            self.meter.add_energy("transport", t, tc["kwh_per_trip"], tc["ride_min"])
        for g in boarded:
            self.set_state(g, "TRAVELING", t, "in")
            g.transport_min += (t - g.wait_start) + tc["ride_min"]
            g.node = "VEHICLE"
            g.walk_from, g.destination, g.walk_start, g.walk_end = stop, dest, t, t + tc["ride_min"]
            self.log(t, g, "board", stop, f"{g.size} người")
            self.push(t + tc["ride_min"], "alight", g.gid)

    def alight(self, g, t):
        g.node = g.destination
        self.log(t, g, "alight", g.node)
        dest, action = g.final
        g.final = None
        if dest == g.node:
            g.pending, g.destination = action, dest
            self.on_arrive(g, t)
        else:
            self.walk(g, dest, t, action)

    # ------------------------------------------------------------ rooms
    def leave_room(self, g, t, on_arrive):
        r = g.room
        self.rooms.occupied[r] = False
        self.rooms.asleep[r] = False
        self.log(t, g, "room_leave", "ROOMS")
        if self.policies.get("occupancy_hvac"):
            p = self.pp["occupancy_hvac"]
            err = self.grng(g, "prediction").normal(0, p["prediction_error_sd_min"])
            if on_arrive and on_arrive[0] == "enter_park":
                pred = g.intended_return + 2 * self.cfg["transport"]["ride_min"] + err
            else:
                pred = t + p["hotel_trip_default_min"] + err
            self.rooms.precool_from[r] = int(pred - p["precool_lead_min"])

    def enter_room(self, g, t, mode):
        r = g.room
        g.node, g.zone = "ROOMS", "hotel"
        self.rooms.occupied[r] = True
        self.rooms.persons[r] = g.size
        self.rooms.precool_from[r] = 10 ** 9
        if self.rooms.cleaning[r]:
            self.interrupt_cleaning(r, t)
        if mode == "end":
            self.set_state(g, "FINISHED", t, "in")
            g.asleep = True
            self.rooms.asleep[r] = True
            self.meter.add_energy("hotel_hot_water", t, g.size * self.cfg["rooms"]["hot_water_kwh_per_person_shower"], 15)
            self.log(t, g, "end_day", "ROOMS")
            g.history.append((t, t, "END", "ROOMS", 0))
            next_day = (t - 360) // 1440 + 1
            if next_day < self.days:
                draw_daily_plan(g, self.cfg, self.seed, next_day)
                self.push(max(g.wake_t, t + 360), "wake", g.gid)
        else:
            self.set_state(g, "IN_ROOM", t, "in")
            dur = int(self.grng(g).uniform(45, 90))
            force_end = (t // 1440) * 1440 + hhmm(self.cfg["decision"]["force_end_day_hhmm"])
            limit = min(force_end, g.bedtime) if t < g.bedtime else force_end
            if t < limit:
                dur = min(dur, max(5, limit - t))   # re-decide at the group's own bedtime (or the forced end time)
            g.history.append((t, t + dur, "ROOM_REST", "ROOMS", 0))
            self.log(t, g, "room_rest", "ROOMS", f"{dur} phút")
            self.push(t + dur, "decide", g.gid)

    def wake(self, g, t):
        self.set_state(g, "IN_ROOM", t, "in")
        g.asleep = False
        self.rooms.asleep[g.room] = False
        g.hunger = self.cfg["needs"]["initial_hunger_at_wake"]
        g.fatigue = min(g.fatigue, self.cfg["needs"]["overnight_fatigue_after_sleep"])
        g.heat = 0.0
        g.visits, g.excluded_until, g.in_park_today, g.meals_today = {}, {}, False, 0
        g.park_entry_t = g.returned_t = None
        self.meter.add_energy("hotel_hot_water", t, g.size * self.cfg["rooms"]["hot_water_kwh_per_person_shower"], 15)
        self.log(t, g, "wake", "ROOMS")
        rest = self.fac["HOTEL_RESTAURANT"]
        arrive = t + self.net.walk_min("ROOMS", rest.node)
        if not g.skip_breakfast and rest.is_open(arrive) and rest.minutes_to_close(arrive) > 20:
            self.log(t, g, "decision", rest.id, "Thói quen: ăn sáng")
            self.travel(g, rest.node, t, ("join", rest.id))
        else:
            self.decide(g, t)

    # ------------------------------------------------------------ decisions
    def _est_wait(self, g, f, t):
        sigma = self.cfg["decision"]["posted_wait_noise_sigma"]
        return f.posted_wait(t) * float(np.exp(self.grng(g).normal(0, sigma)))

    def _walk_time(self, g, dest):
        return int(math.ceil(self.net.walk_min(g.node, dest) / g.walk_speed))

    def park_options(self, g, t):
        opts, w = [], self.w
        park = self.cfg["park_common"]
        if self.tday(t) < hhmm(park["park_close_hhmm"]):
            for fid in PARK_FACILITIES:
                f = self.fac[fid]
                if g.excluded_until.get(fid, -1) > t:
                    continue
                if f.spec.get("no_young_children") and g.has_young_child:
                    continue  # member ineligibility; groups stay together
                if g.size > f.capacity:
                    continue
                tr = self._walk_time(g, f.node)
                arr = t + tr
                if f.kind == "show":
                    nxt = f.next_show(arr)
                    if nxt is None or nxt - arr > f.spec["doors_open_before_min"] + 15:
                        continue
                    est, end = nxt - arr, nxt + f.spec["duration_min"]
                else:
                    if not f.is_open(arr) or f.minutes_to_close(arr) <= 5:
                        continue
                    est = self._est_wait(g, f, t)
                    dur = f.cycle if f.kind == "batch" else f.spec["duration_min_mean"]
                    end = arr + est + dur
                opts.append(dict(kind="attraction", fid=fid, activity=f.activity, label=f.label, travel_min=tr,
                                 est_wait=est, end_t=end,
                                 heat_relief=1.0 if (f.spec.get("indoor") or f.spec.get("water_relief")) else 0.0))
            for fid in ["PARK_RESTAURANT", "REST_A", "REST_B"]:
                f = self.fac[fid]
                if g.excluded_until.get(fid, -1) > t:
                    continue
                tr = self._walk_time(g, f.node)
                arr = t + tr
                if not f.is_open(arr) or f.minutes_to_close(arr) <= 20:
                    continue
                o = dict(kind=f.kind, fid=fid, label=f.label, travel_min=tr, est_wait=self._est_wait(g, f, t),
                         end_t=arr + f.spec["duration_min_mean"],
                         heat_relief=1.0 if f.spec.get("indoor") else (0.5 if f.spec.get("shaded") else 0.0))
                if f.kind == "restaurant":
                    price = f.spec["price_vnd_per_person"] * g.size
                    if price > g.budget_left:
                        continue  # insufficient budget
                    o.update(eats=True, price=price)
                else:
                    o.update(rests=True)
                opts.append(o)
        tp = w["time_pressure_per_hour"] * float(np.clip((t - g.intended_return) / 60.0, -2, 3))
        if self.tday(t) >= hhmm(park["park_close_hhmm"]) - 15:
            tp += 5.0
        opts.append(dict(kind="return", label="Về khách sạn", travel_min=self._walk_time(g, "PARK_STOP"),
                         rests=True, time_pressure=tp))
        return opts

    def hotel_options(self, g, t):
        opts, w, dc = [], self.w, self.cfg["decision"]
        m = self.tday(t)
        end_tp = w["time_pressure_per_hour"] * (t - g.bedtime) / 60.0 + 0.3
        if m >= hhmm(dc["force_end_day_hhmm"]) or m < 300:
            return [dict(kind="end_day", label="Kết thúc ngày (về phòng ngủ)", rests=True, time_pressure=5.0,
                         travel_min=self._walk_time(g, "ROOMS"))]
        rest = self.fac["HOTEL_RESTAURANT"]
        tr = self._walk_time(g, rest.node)
        arr = t + tr
        if rest.is_open(arr) and rest.minutes_to_close(arr) > 20:
            breakfast = self.tday(arr) < hhmm("11:00")
            price = (rest.spec["breakfast_price_vnd_per_person"] if breakfast else rest.spec["price_vnd_per_person"]) * g.size
            if price <= g.budget_left:
                opts.append(dict(kind="restaurant", fid=rest.id, label=rest.label, travel_min=tr,
                                 est_wait=self._est_wait(g, rest, t), eats=True, price=price, heat_relief=1.0))
        pool = self.fac["POOL"]
        tr = self._walk_time(g, pool.node)
        if pool.is_open(t + tr) and pool.minutes_to_close(t + tr) > 20:
            opts.append(dict(kind="attraction", fid="POOL", activity="pool", label=pool.label, travel_min=tr,
                             est_wait=self._est_wait(g, pool, t), heat_relief=0.6))
        for f in self.hotel_amenities:            # spa, gym, kids club inside the hotel
            if g.excluded_until.get(f.id, -1) > t or (f.spec.get("requires_children") and not g.children):
                continue
            if f.spec.get("once_per_day") and g.visits.get(f.id):
                continue                          # at most one spa / gym / kids-club visit per group per day
            tr = self._walk_time(g, f.node)
            if not f.is_open(t + tr) or f.minutes_to_close(t + tr) <= 20:
                continue
            price = f.spec.get("price_vnd_per_person", 0) * g.size
            if price > g.budget_left:
                continue
            opts.append(dict(kind="attraction", fid=f.id, activity=f.activity, label=f.label, travel_min=tr,
                             est_wait=self._est_wait(g, f, t), price=price, heat_relief=f.spec.get("heat_relief", 1.0),
                             rests=bool(f.spec.get("rests")), interest_scale=f.spec.get("interest_scale", 1.0)))
        if t < g.bedtime:                      # after the planned bedtime, going to the room means ending the day
            opts.append(dict(kind="room_rest", label="Nghỉ trong phòng", travel_min=self._walk_time(g, "ROOMS"),
                             rests=True, heat_relief=1.0, time_pressure=w["room_rest_base"]))
        if (g.park_intent and not g.in_park_today and m < hhmm(dc["latest_park_departure_hhmm"])):
            if t >= g.intended_departure:
                tp = w["go_park_base"] + 0.8
            else:
                tp = w["go_park_base"] - w["time_pressure_per_hour"] * 2 * (g.intended_departure - t) / 60.0
            opts.append(dict(kind="go_park", label="Đi công viên", travel_min=self._walk_time(g, "HOTEL_STOP"),
                             time_pressure=tp))
        if t >= g.bedtime - 90:
            opts.append(dict(kind="end_day", label="Kết thúc ngày (về phòng ngủ)", rests=True,
                             travel_min=self._walk_time(g, "ROOMS"), time_pressure=end_tp))
        return opts

    def decide(self, g, t):
        self.update_needs(g, t)
        if g.node in ("WALK", "VEHICLE"):
            return
        opts = self.park_options(g, t) if g.zone == "park" else self.hotel_options(g, t)
        k, log = choose(g, opts, t, self.w, self.cfg["decision"]["temperature"], self.grng(g))
        opt = opts[k]
        rec_note = ""
        if (self.policies.get("park_recommendations") and g.zone == "park" and opt["kind"] == "attraction"):
            opt, rec_note = self.recommend(g, opt, opts, t)
        self.decision_log.append(dict(t=t, gid=g.gid, chosen=opt["label"], hunger=round(g.hunger, 1),
                                      fatigue=round(g.fatigue, 1), heat=round(g.heat, 1),
                                      recommendation=rec_note, options=log[:6]))
        self.log(t, g, "decision", opt.get("fid", opt["kind"]), opt["label"])
        self.execute(g, opt, t)

    def recommend(self, g, opt, opts, t):
        p = self.pp["park_recommendations"]
        f = self.fac[opt["fid"]]
        posted = f.posted_wait(t)
        if f.kind == "show" or posted < p["trigger_wait_min"]:
            return opt, ""
        best, best_score = None, -1e9
        for o in opts:
            if o["kind"] != "attraction" or o["fid"] == opt["fid"] or self.fac[o["fid"]].kind == "show":
                continue
            alt_wait = self.fac[o["fid"]].posted_wait(t)
            if posted - alt_wait < p["min_improvement_min"]:
                continue
            score = g.pref.get(o["activity"], 0.3) - (alt_wait + o["travel_min"]) / 60.0
            if score > best_score:
                best, best_score = o, score
        if best is None:
            return opt, ""
        u = stream(self.seed, "policy", g.gid, t).random()
        accepted = u < g.rec_acceptance
        self.rec_log.append(dict(t=t, gid=g.gid, original=opt["fid"], suggested=best["fid"],
                                 posted_wait=round(posted, 1), alt_wait=round(self.fac[best["fid"]].posted_wait(t), 1),
                                 accepted=accepted))
        note = f"Gợi ý {best['label']} thay cho {opt['label']} – {'chấp nhận' if accepted else 'từ chối'}"
        self.log(t, g, "recommendation", best["fid"], note)
        return (best if accepted else opt), note

    def execute(self, g, opt, t):
        kind = opt["kind"]
        if kind in ("attraction", "restaurant", "rest"):
            self.travel(g, self.fac[opt["fid"]].node, t, ("join", opt["fid"]))
        elif kind == "room_rest":
            self.travel(g, "ROOMS", t, ("room", "rest"))
        elif kind == "end_day":
            self.travel(g, "ROOMS", t, ("room", "end"))
        elif kind == "go_park":
            self.travel(g, "PARK_STOP", t, ("enter_park",))
        elif kind == "return":
            g.returning = True
            self.travel(g, "HOTEL_STOP", t, ("arrive_hotel",))

    # ------------------------------------------------------------ queues & service
    def join_queue(self, g, f, t):
        self.update_needs(g, t)
        if f.kind == "show":
            nxt = f.next_show(t)
            ok = nxt is not None and nxt - t <= f.spec["doors_open_before_min"] + 15
        else:
            ok = f.is_open(t)
        if not ok:
            g.excluded_until[f.id] = t + 30
            self.log(t, g, "closed_on_arrival", f.id)
            self.unfinished_demand.append(dict(t=t, gid=g.gid, fid=f.id, persons=g.size, reason="đóng cửa khi đến"))
            return self.decide(g, t)
        if f.kind not in ("show",):
            observed = f.posted_wait(t)
            if observed > g.queue_tolerance * self.cfg["decision"]["balk_factor"]:
                f.stats["balks"] += 1
                g.excluded_until[f.id] = t + 30
                self.log(t, g, "balk", f.id, f"thấy chờ ~{observed:.0f} phút")
                return self.decide(g, t)
        g.queue_token += 1
        f.queue.append([g, t, g.queue_token])
        f.stats["arrivals"] += 1
        f.stats["arrival_persons"] += g.size
        f.stats["max_queue_persons"] = max(f.stats["max_queue_persons"], f.queue_persons())
        env = "in" if f.spec.get("indoor") else ("shade" if f.spec.get("shaded") else "out")
        self.set_state(g, "QUEUING", t, env)
        self.log(t, g, "join_queue", f.id, f"{f.queue_persons()} người trong hàng")
        if f.kind != "show":
            self.push(t + int(math.ceil(g.queue_tolerance)), "abandon", f.id, g.gid, g.queue_token)
        if f.kind == "batch":
            self.ride_check(f, t)
        elif f.kind != "show":
            self.admit(f, t)

    def _select_fifo(self, f, t, free):
        """FIFO with fill-in: smaller groups behind may use leftover capacity unless a group
        that has waited >= anti-starvation minutes is blocked at the front."""
        chosen, blocked = [], False
        lim = self.cfg["fill_in_anti_starvation_min"]
        for e in f.queue:
            g = e[0]
            if g.size <= free:
                chosen.append(e)
                free -= g.size
            elif t - e[1] >= lim:
                blocked = True
            if blocked or free <= 0:
                break
        return chosen

    def _start_service(self, f, e, t, dur, state):
        g = e[0]
        f.queue.remove(e)
        wait = t - e[1]
        f.stats["admissions"] += 1
        f.stats["admitted_persons"] += g.size
        f.stats["wait_sum_person_min"] += wait * g.size
        f.waits.append(wait)
        env = "in" if f.spec.get("indoor") else ("shade" if f.spec.get("shaded") else "out")
        self.set_state(g, state, t, env)
        f.active[g.gid] = (g, g.size, t + dur)
        g.service_start, g.service_wait = t, wait
        self.log(t, g, "admit", f.id, f"chờ {wait} phút")
        self.push(t + dur, "service_end", f.id, g.gid)

    def admit(self, f, t):
        if not (f.is_open(t) or f.in_grace(t)) or not f.queue:
            return
        free = f.capacity - f.active_persons()
        for e in self._select_fifo(f, t, free):
            g = e[0]
            sd = f.spec.get("duration_min_sd", 0)
            if f.kind == "restaurant" and self.tday(t) < hhmm("11:00") and "breakfast_duration_min_mean" in f.spec:
                mean = f.spec["breakfast_duration_min_mean"]
            else:
                mean = f.spec["duration_min_mean"]
            dur = max(10, int(self.grng(g, "service").normal(mean, sd)))
            state = {"restaurant": "EATING", "rest": "RESTING"}.get(f.kind, "VISITING")
            if f.kind == "restaurant":
                breakfast = self.tday(t) < hhmm("11:00") and "breakfast_price_vnd_per_person" in f.spec
                pr = f.spec["breakfast_price_vnd_per_person"] if breakfast else f.spec["price_vnd_per_person"]
                g.budget_left -= pr * g.size
                eu = "hotel_restaurant" if f.id == "HOTEL_RESTAURANT" else "park_restaurant"
                self.meter.add_energy(eu, t, f.energy["kwh_per_cover"] * g.size, dur)
            elif f.spec.get("price_vnd_per_person"):   # paid amenities (spa)
                g.budget_left -= f.spec["price_vnd_per_person"] * g.size
            self._start_service(f, e, t, dur, state)

    def ride_check(self, f, t):
        if not (f.is_open(t) or f.in_grace(t)) or not f.queue:
            return
        if t < f.ready_at:
            self.schedule_ride_check(f, f.ready_at)
            return
        chosen = self._select_fifo(f, t, f.capacity)
        load = sum(e[0].size for e in chosen)
        if self.policies.get("ride_dispatch"):
            p = self.pp["ride_dispatch"]
            oldest = t - f.queue[0][1]
            closing = f.in_grace(t) or f.minutes_to_close(t) <= f.cycle
            if load < p["min_load_factor"] * f.capacity and oldest < p["max_hold_min"] and not closing:
                self.schedule_ride_check(f, t + 1)
                return
        for e in chosen:
            self._start_service(f, e, t, f.cycle, "VISITING")
        f.stats["cycles"] += 1
        f.stats["cycle_riders"] += load
        en = f.energy
        self.meter.add_energy("attraction_cycles", t, en["kwh_per_cycle"] + en["kwh_per_rider"] * load, f.cycle)
        f.ready_at = t + f.cycle
        self.schedule_ride_check(f, f.ready_at)

    def schedule_ride_check(self, f, when):
        if f.next_check is not None and f.next_check <= when and f.next_check >= self.t:
            return
        f.next_check = when
        self.push(when, "ride_check", f.id)

    def show_start(self, f, t):
        free = f.capacity
        admitted = 0
        for e in list(f.queue):
            g = e[0]
            if g.size <= free:
                free -= g.size
                admitted += g.size
                self._start_service(f, e, t, f.spec["duration_min"], "VISITING")
        self.meter.add_power("attraction_cycles", t, f.energy["performance_kw"], f.spec["duration_min"])
        f.stats["cycles"] += 1
        f.stats["cycle_riders"] += admitted
        for e in list(f.queue):
            g = e[0]
            f.queue.remove(e)
            f.stats["rejected"] += 1
            f.stats["rejected_persons"] += g.size
            self.unfinished_demand.append(dict(t=t, gid=g.gid, fid=f.id, persons=g.size, reason="hết chỗ xem"))
            self.log(t, g, "show_full", f.id)
            g.excluded_until[f.id] = t + 60
            self.decide(g, t)

    def service_end(self, f, g, t):
        if g.gid not in f.active:
            return
        del f.active[g.gid]
        f.stats["completions"] += 1
        f.stats["completed_persons"] += g.size
        g.visits[f.id] = g.visits.get(f.id, 0) + 1
        self.update_needs(g, t)
        if f.kind == "restaurant":
            g.hunger = max(0.0, g.hunger - self.cfg["needs"]["meal_hunger_reduction"])
            g.meals_today += 1
        if f.spec.get("water_relief"):
            g.heat = max(0.0, g.heat - self.cfg["needs"]["water_ride_heat_relief"])
        g.history.append((g.service_start, t, f.id, f.node, g.service_wait))
        self.log(t, g, "service_end", f.id)
        if f.kind not in ("batch", "show"):
            self.admit(f, t)
        self.decide(g, t)

    def abandon(self, f, g, token, t):
        if g.state != "QUEUING" or g.queue_token != token:
            return
        entry = next((e for e in f.queue if e[0] is g and e[2] == token), None)
        if entry is None:
            return
        if self.grng(g).random() < self.cfg["decision"]["abandon_probability"]:
            f.queue.remove(entry)
            f.stats["abandonments"] += 1
            f.stats["abandoned_persons"] += g.size
            g.excluded_until[f.id] = t + self.cfg["decision"]["excluded_after_abandon_min"]
            self.log(t, g, "abandon", f.id, f"bỏ hàng sau {t - entry[1]} phút")
            self.decide(g, t)
        else:
            self.push(t + max(5, int(g.queue_tolerance / 2)), "abandon", f.id, g.gid, token)

    def close(self, f, t):
        """End of closing grace: remaining queue is rejected and recorded."""
        for e in list(f.queue):
            g = e[0]
            f.queue.remove(e)
            f.stats["rejected"] += 1
            f.stats["rejected_persons"] += g.size
            self.unfinished_demand.append(dict(t=t, gid=g.gid, fid=f.id, persons=g.size, reason="đóng cửa"))
            self.log(t, g, "rejected_at_close", f.id)
            self.decide(g, t)

    # ------------------------------------------------------------ housekeeping
    def _init_housekeeping(self):
        hk = self.cfg["housekeeping"]
        n_rooms = self.rooms.n
        chunks = np.array_split(np.arange(n_rooms), hk["workers"])
        self.workers = [Housekeeper(i, list(c)) for i, c in enumerate(chunks)]
        self.hk_remaining = np.zeros(n_rooms)
        self.hk_first_eligible = np.full(n_rooms, -1)
        self.hk_log, self.hk_daily = [], []
        self.hk_interruptions = 0
        self._hk_new_day(0)

    def _hk_new_day(self, day):
        rng = stream(self.seed, "housekeeping", day)
        n = self.rooms.n
        self.cleaned_today = np.zeros(n, dtype=bool)
        self.dnd = rng.random(n) < self.cfg["housekeeping"]["dnd_probability"]
        self.hk_remaining[:] = 0
        self.hk_first_eligible[:] = -1
        self.hk_day = day

    def _predicted_absence_ok(self, r, t):
        gid = self.rooms.group_of[r]
        g = self.groups[gid]
        if g.zone == "park" or g.node == "VEHICLE" or (g.node == "WALK" and g.destination == "HOTEL_STOP"
                                                       and not g.returning):
            return True, 2
        return (self.rooms.precool_from[r] - t) >= self.pp["housekeeping_coordination"]["min_predicted_absence_min"], 0

    def hk_tick(self, t):
        R = self.rooms
        eligible = R.sold & ~self.cleaned_today & ~self.dnd & ~R.occupied & ~R.cleaning
        newly = eligible & (self.hk_first_eligible < 0)
        self.hk_first_eligible[newly] = t
        coord = self.policies.get("housekeeping_coordination")
        free = [w for w in self.workers if w.available]
        if not free or not eligible.any():
            return
        if coord:
            cands = []
            for r in np.flatnonzero(eligible):
                g = self.groups[R.group_of[r]]
                in_park = g.zone == "park" or g.node == "VEHICLE"
                cands.append((0 if in_park else 1, int(r)))
            cands.sort()
            order = [r for _, r in cands]
        else:
            order = list(np.flatnonzero(eligible))
        taken = set()
        for w in free:
            own = [r for r in order if r not in taken and w.section[0] <= r <= w.section[-1]]
            pool = own + [r for r in order if r not in taken and r not in own]
            if coord:
                pool = [r for r in order if r not in taken]   # global priority list
            if not pool:
                break
            r = pool[0]
            taken.add(r)
            self.start_cleaning(w, r, t)

    def start_cleaning(self, w, r, t):
        hk = self.cfg["housekeeping"]
        if self.hk_remaining[r] > 0:
            dur = int(self.hk_remaining[r])
        else:
            rng = stream(self.seed, "housekeeping", self.hk_day, r + 1)
            dur = max(15, int(rng.normal(hk["stayover_clean_min_mean"], hk["stayover_clean_min_sd"])))
            dur += hk["between_rooms_min"]
        self.rooms.cleaning[r] = True
        w.room, w.task_start, w.busy_until = r, t, t + dur
        w.token = getattr(w, "token", 0) + 1
        self.push(t + dur, "hk_done", w.wid, w.token)

    def hk_done(self, w, token, t):
        if w.room is None or w.token != token:
            return
        r = w.room
        hk = self.cfg["housekeeping"]
        self.rooms.cleaning[r] = False
        self.cleaned_today[r] = True
        self.hk_remaining[r] = 0
        g = self.groups[self.rooms.group_of[r]]
        u = stream(self.seed, "towels", self.hk_day, r).random(3)
        p_towel = hk["towel_request_base_prob"] * (1 - hk["conservation_effect_on_request"] * g.conservation_pref)
        nudged = False
        if self.policies.get("housekeeping_coordination") and u[1] < g.rec_acceptance:
            p_towel *= (1 - self.pp["housekeeping_coordination"]["towel_nudge_reduction"])
            nudged = True
        towels = u[0] < p_towel
        linen = u[2] < hk["linen_request_base_prob"]
        kg = (g.size * hk["towel_kg_per_person"] if towels else 0.0) + (hk["linen_kg_per_room"] if linen else 0.0)
        self.laundry_bin += kg
        self.laundry_demand_kg += kg
        delay = t - self.hk_first_eligible[r] if self.hk_first_eligible[r] >= 0 else 0
        self.hk_log.append(dict(day=self.hk_day, worker=w.wid, room=int(r), start=w.task_start, end=t,
                                outcome="done", towels_replaced=bool(towels), linen_replaced=bool(linen),
                                towel_nudge_accepted=nudged, laundry_kg=kg, delay_min=int(delay)))
        w.task_history.append((w.task_start, t, int(r), "done"))
        w.room = None

    def interrupt_cleaning(self, r, t):
        for w in self.workers:
            if w.room == r:
                remaining = max(1, w.busy_until - t)
                self.hk_remaining[r] = remaining
                self.rooms.cleaning[r] = False
                self.hk_interruptions += 1
                self.hk_log.append(dict(day=self.hk_day, worker=w.wid, room=int(r), start=w.task_start, end=t,
                                        outcome="interrupted_guest_returned", towels_replaced=False,
                                        linen_replaced=False, towel_nudge_accepted=False, laundry_kg=0.0,
                                        delay_min=0))
                w.task_history.append((w.task_start, t, int(r), "interrupted"))
                w.room = None
                w.token += 1
                return

    def hk_day_summary(self, day):
        R = self.rooms
        pending = R.sold & ~self.cleaned_today
        self.hk_daily.append(dict(day=day, sold_rooms=int(R.sold.sum()), cleaned=int(self.cleaned_today.sum()),
                                  pending=int(pending.sum()), pending_dnd=int((pending & self.dnd).sum()),
                                  pending_never_vacant=int((pending & ~self.dnd & (self.hk_first_eligible < 0)).sum()),
                                  pending_capacity=int((pending & ~self.dnd & (self.hk_first_eligible >= 0)).sum()),
                                  interruptions=self.hk_interruptions))

    # ------------------------------------------------------------ laundry
    def _init_laundry(self):
        lc = self.cfg["laundry"]
        self.machines = [-1] * lc["machines"]
        self.machine_load = [0.0] * lc["machines"]
        self.laundry_bin = 0.0
        self.laundry_demand_kg = 0.0
        self.laundry_batches = []

    def laundry_tick(self, t):
        lc = self.cfg["laundry"]
        m = self.tday(t)
        flush_key = "coordinated_flush_times" if self.policies.get("housekeeping_coordination") else "baseline_flush_times"
        flush = any(m == hhmm(s) for s in lc[flush_key])
        for i in range(len(self.machines)):
            if self.machines[i] > t:
                continue
            if self.laundry_bin >= lc["capacity_kg"] - 1e-9:
                load = lc["capacity_kg"]
            elif flush and self.laundry_bin >= lc["min_partial_kg"]:
                load = self.laundry_bin
            else:
                break
            self.laundry_bin -= load
            frac = lc["fixed_energy_fraction"] + (1 - lc["fixed_energy_fraction"]) * load / lc["capacity_kg"]
            kwh = lc["kwh_per_full_batch"] * frac
            self.meter.add_energy("laundry", t, kwh, lc["cycle_min"])
            self.machines[i] = t + lc["cycle_min"]
            self.laundry_batches.append(dict(start=t, end=t + lc["cycle_min"], machine=i, load_kg=round(load, 2),
                                             kwh=round(kwh, 3), full=load >= lc["capacity_kg"] - 1e-9))

    # ------------------------------------------------------------ per-minute loads
    def base_loads(self, t):
        m = self.tday(t)
        tout = self.weather.temp[t]
        hc, pc, lc = self.cfg["hotel_common"], self.cfg["park_common"], self.cfg["laundry"]
        day = hhmm(hc["day_start_hhmm"]) <= m < hhmm(hc["day_end_hhmm"])
        kw = hc["public_kw_day"] if day else hc["public_kw_night"]
        kw += hc["temp_sensitivity_kw_per_c_above_28"] * max(0.0, tout - 28.0)
        on, off = hhmm(hc["exterior_on_hhmm"]), hhmm(hc["exterior_off_hhmm"])
        if m >= on or m < off:
            kw += hc["exterior_lighting_kw"]
        kw += hc["pool_pumps_kw"]
        self.meter.add_power("hotel_common", t, kw)
        for fid, eu in (("HOTEL_RESTAURANT", "hotel_restaurant"), ("PARK_RESTAURANT", "park_restaurant")):
            f = self.fac[fid]
            e = f.energy
            self.meter.add_power(eu, t, e["base_kw_open"] + e["kitchen_kw_open"] if f.is_open(t) else e["base_kw_closed"])
        for fid in ("THRILL", "FAMILY", "WATER", "INDOOR", "SHOW"):
            f = self.fac[fid]
            if f.is_open(t) or f.in_grace(t) or f.opens_within(t, 30):
                self.meter.add_power("attraction_standby", t, f.energy["standby_kw"])
        f = self.fac["INDOOR"]
        e = f.energy
        if f.is_open(t) or f.in_grace(t) or f.opens_within(t, e["precool_min"]):
            q = (e["cooling_ua_kw_per_degC"] * (tout - e["cooling_setpoint_c"])
                 + e["internal_kw_per_visitor"] * f.active_persons() + e["equipment_heat_kw"])
            self.meter.add_power("indoor_attraction_cooling", t, max(0.0, q) / e["cop"])
        park_open = hhmm(pc["park_open_hhmm"]) <= m < hhmm(pc["park_close_hhmm"])
        pk = pc["open_kw"] if park_open else 0.0
        if hhmm(pc["lighting_on_hhmm"]) <= m < hhmm(pc["park_close_hhmm"]) + pc["lighting_after_close_min"]:
            pk += pc["pathway_lighting_kw"]
        for fid in ("REST_A", "REST_B"):
            if self.fac[fid].is_open(t):
                pk += self.fac[fid].energy["base_kw_open"]
        self.meter.add_power("park_common", t, pk)
        wk = 0.0
        for f in self.hotel_amenities:
            if f.is_open(t) or f.in_grace(t):
                wk += f.energy["base_kw_open"] + f.energy["kw_per_visitor"] * f.active_persons()
        self.meter.add_power("hotel_wellness", t, wk)
        if hhmm(lc["open_hhmm"]) <= m < hhmm(lc["close_hhmm"]):
            self.meter.add_power("laundry", t, lc["standby_kw"])

    # ------------------------------------------------------------ snapshots
    def snapshot(self, t):
        node_persons, queue, active, walkers = {}, {}, {}, []
        state_persons = {}
        for g in self.groups:
            state_persons[g.state] = state_persons.get(g.state, 0) + g.size
            if g.node == "WALK":
                span = max(1, g.walk_end - g.walk_start)
                walkers.append((g.gid, g.walk_from, g.destination, round((t - g.walk_start) / span, 3), g.size,
                                1 if g.returning else 0))
            elif g.node != "VEHICLE":
                node_persons[g.node] = node_persons.get(g.node, 0) + g.size
        for f in self.fac.values():
            queue[f.id] = f.queue_persons()
            active[f.id] = f.active_persons()
        stop_q = {k: sum(g.size for g in q) for k, q in self.stop_queue.items()}
        in_vehicle = sum(g.size for g in self.groups if g.node == "VEHICLE")
        in_park = sum(g.size for g in self.groups if g.zone == "park" and g.node != "VEHICLE")
        self.snapshots.append(dict(t=t, node_persons=node_persons, queue=queue, active=active, stop_queue=stop_q,
                                   walkers=walkers, in_vehicle=in_vehicle, in_park=in_park,
                                   state_persons=state_persons,
                                   room_temp=np.round(self.rooms.temp, 2).astype(np.float32),
                                   room_occ=self.rooms.occupied.copy(), room_hvac=self.rooms.hvac_on.copy(),
                                   room_asleep=self.rooms.asleep.copy(), room_control=self.rooms.control.copy(),
                                   room_clean=self.rooms.cleaning.copy(), room_sp=self.rooms.setpoint.astype(np.float32),
                                   room_hvac_kw=self.rooms.hvac_kw.astype(np.float32),
                                   room_light_kw=self.rooms.light_kw.astype(np.float32),
                                   t_out=float(self.weather.temp[t])))

    # ------------------------------------------------------------ main loop
    def run(self, progress=None):
        t0 = _time.perf_counter()
        R = self.rooms
        self.room_occ_minutes = np.zeros(R.n)
        self.room_hot_minutes = np.zeros(R.n)
        self.room_degree_minutes = np.zeros(R.n)
        thr = self.cfg["rooms"]["comfort_threshold_c"]
        for f in self.fac.values():
            f.next_check = None
        for t in range(self.T):
            self.t = t
            while self.heap and self.heap[0][0] <= t:
                _, _, kind, payload = heapq.heappop(self.heap)
                self.dispatch(kind, payload, t)
            self.base_loads(t)
            hv, li = R.step(t, self.weather.temp[t], self.weather.solar[t], self.policies, self.pp)
            self.meter.add_power("room_hvac", t, hv)
            self.meter.add_power("room_lighting_plug", t, li)
            occ = R.occupied & R.sold
            self.room_occ_minutes += occ
            hot = occ & (R.temp > thr)
            self.room_hot_minutes += hot
            self.room_degree_minutes += np.where(occ, np.maximum(0.0, R.temp - thr), 0.0)
            if t % SNAPSHOT_MIN == 0:
                self.snapshot(t)
            if progress and t % 60 == 0:
                progress(t / self.T)
        self.runtime_s = _time.perf_counter() - t0
        return self

    def dispatch(self, kind, payload, t):
        G = self.groups
        if kind == "wake":
            self.wake(G[payload[0]], t)
        elif kind == "decide":
            g = G[payload[0]]
            if g.state in ("IN_ROOM",) and g.node == "ROOMS":
                self.decide(g, t)
        elif kind == "arrive":
            self.on_arrive(G[payload[0]], t)
        elif kind == "alight":
            self.alight(G[payload[0]], t)
        elif kind == "service_end":
            self.service_end(self.fac[payload[0]], G[payload[1]], t)
        elif kind == "abandon":
            self.abandon(self.fac[payload[0]], G[payload[1]], payload[2], t)
        elif kind == "ride_check":
            f = self.fac[payload[0]]
            if f.next_check == t:
                f.next_check = None
                self.ride_check(f, t)
        elif kind == "show_start":
            self.show_start(self.fac[payload[0]], t)
        elif kind == "close":
            self.close(self.fac[payload[0]], t)
        elif kind == "shuttle":
            self.shuttle(t, payload[0], payload[1])
        elif kind == "hk_tick":
            self.hk_tick(t)
        elif kind == "hk_done":
            self.hk_done(self.workers[payload[0]], payload[1], t)
        elif kind == "laundry_tick":
            self.laundry_tick(t)
        elif kind == "day_end":
            self.hk_day_summary(payload[0])
        elif kind == "day_start":
            self._hk_new_day(payload[0])
