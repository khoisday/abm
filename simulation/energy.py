"""Electricity metering and vectorized room thermal / equipment model."""
import numpy as np

END_USES = {
    # end_use: (label_vi, boundary)
    "room_hvac": ("Điều hòa phòng", "hotel"),
    "room_lighting_plug": ("Chiếu sáng & ổ cắm phòng", "hotel"),
    "hotel_hot_water": ("Nước nóng phòng (bơm nhiệt)", "hotel"),
    "hotel_common": ("Khu chung khách sạn", "hotel"),
    "hotel_restaurant": ("Nhà hàng khách sạn", "hotel"),
    "hotel_wellness": ("Spa, gym & câu lạc bộ trẻ em", "hotel"),
    "laundry": ("Giặt là", "hotel"),
    "park_restaurant": ("Nhà hàng công viên", "park"),
    "attraction_standby": ("Trò chơi – tải nền giờ mở cửa", "park"),
    "attraction_cycles": ("Trò chơi – chu kỳ vận hành / biểu diễn", "park"),
    "indoor_attraction_cooling": ("Làm mát khu trong nhà", "park"),
    "park_common": ("Khu chung công viên", "park"),
    "transport": ("Phương tiện kết nối", "transport"),
}


class Meter:
    """Per-minute kWh arrays per end use. Energy spread over an activity's
    duration is written into future minutes; anything past the horizon lands
    in a buffer and is reported as spillover, not as in-horizon electricity."""

    BUFFER = 600

    def __init__(self, horizon_min):
        self.T = horizon_min
        self.kwh = {k: np.zeros(horizon_min + self.BUFFER) for k in END_USES}

    def add_power(self, end_use, t, kw, minutes=1):
        self.kwh[end_use][t:t + minutes] += kw / 60.0

    def add_energy(self, end_use, t, kwh, duration_min=1):
        d = max(1, int(duration_min))
        self.kwh[end_use][t:t + d] += kwh / d

    def in_horizon(self, end_use):
        return self.kwh[end_use][:self.T]

    def spillover_kwh(self):
        return {k: float(v[self.T:].sum()) for k, v in self.kwh.items()}

    def intervals(self, step_min):
        n = self.T // step_min
        return {k: v[:n * step_min].reshape(n, step_min).sum(axis=1) for k, v in self.kwh.items()}


class RoomBank:
    """Vectorized state machine for all 533 rooms (sold and unsold).

    First-order model per minute:
      T_next = T + dt/C * (UA*(T_out - T) + Q_internal - Q_cool),  Q_cool = COP * P_compressor
    dt in hours, C in kWh/degC, UA in kW/degC, heat in kW.
    """

    def __init__(self, cfg, sold_mask, groups, rng_init):
        r = cfg["rooms"]
        n = len(sold_mask)
        self.n = n
        self.cfg = r
        self.sold = sold_mask.copy()
        self.guest_sp = np.full(n, float(r["staff_cleaning_setpoint_c"]))
        self.persons = np.zeros(n, dtype=int)
        self.group_of = np.full(n, -1, dtype=int)
        for g in groups:
            self.guest_sp[g.room] = g.guest_setpoint
            self.group_of[g.room] = g.gid
        lo, hi = r["unsold_initial_temp_c"]
        self.temp = np.where(sold_mask, self.guest_sp + rng_init.normal(0, 0.3, n), rng_init.uniform(lo, hi, n))
        self.bypass = rng_init.random(n) < r["keycard_bypass_share"]
        self.occupied = sold_mask.copy()       # guests start the day asleep in their rooms
        self.asleep = sold_mask.copy()
        self.cleaning = np.zeros(n, dtype=bool)
        self.precool_from = np.full(n, 10 ** 9)  # absolute minute when pre-cooling starts
        self.hvac_on = np.zeros(n, dtype=bool)
        self.setpoint = self.guest_sp.copy()
        self.control = np.zeros(n, dtype=bool)
        self.hvac_kw = np.zeros(n)
        self.light_kw = np.zeros(n)
        self.precooling = np.zeros(n, dtype=bool)

    def step(self, t, t_out, solar, policies, pparams):
        r = self.cfg
        L = r["loads_kw"]
        occ, sold, clean = self.occupied, self.sold, self.cleaning
        unocc_sold = sold & ~occ & ~clean
        occ_hvac = policies.get("occupancy_hvac", False)
        keep_setback = policies.get("housekeeping_coordination", False) and \
            pparams["housekeeping_coordination"]["keep_setback_while_cleaning"]

        control = np.zeros(self.n, dtype=bool)
        sp = self.guest_sp.copy()
        control |= occ
        precool = np.zeros(self.n, dtype=bool)
        if occ_hvac:
            setback = pparams["occupancy_hvac"]["setback_c"]
            control |= unocc_sold
            precool = unocc_sold & (t >= self.precool_from)
            sp = np.where(unocc_sold & ~precool, setback, sp)
            if keep_setback:
                control |= clean
                sp = np.where(clean, setback, sp)
            else:
                control |= clean
                sp = np.where(clean, r["staff_cleaning_setpoint_c"], sp)
        else:
            control |= unocc_sold & self.bypass      # keycard left in slot: HVAC keeps running
            if keep_setback:
                pass                                 # attendant does not switch HVAC on
            else:
                control |= clean
                sp = np.where(clean, r["staff_cleaning_setpoint_c"], sp)
        self.precooling = precool

        half = r["deadband_c"] / 2.0
        on = self.hvac_on.copy()
        on = np.where(self.temp > sp + half, True, on)
        on = np.where(self.temp < sp - half, False, on)
        on &= control
        self.hvac_on, self.setpoint, self.control = on, sp, control

        q_cool = np.where(on, r["cooling_capacity_kw_thermal"], 0.0)
        hvac_kw = q_cool / r["cop"] + np.where(control, r["fan_kw"], 0.0)

        light = np.where(~sold, L["unsold_standby"], L["sold_unoccupied_standby"])
        awake = occ & ~self.asleep
        light = np.where(occ & self.asleep, L["occupied_asleep_light"] + L["occupied_asleep_plug"], light)
        light = np.where(awake, L["occupied_awake_light"] + L["occupied_awake_plug"], light)
        light = np.where(unocc_sold & self.bypass, L["bypass_unoccupied_light"] + L["bypass_unoccupied_plug"], light)
        light = np.where(clean, L["cleaning_light_plug"], light)

        q_int = self.persons * occ * r["person_heat_kw"] + light + r["solar_gain_peak_kw"] * solar
        dt = 1.0 / 60.0
        self.temp = self.temp + dt / r["thermal_capacitance_kwh_per_c"] * (
            r["ua_kw_per_c"] * (t_out - self.temp) + q_int - q_cool)
        self.hvac_kw, self.light_kw = hvac_kw, light
        return float(hvac_kw.sum()), float(light.sum())
