"""Streamlit dashboard (Vietnamese UI). Launch: streamlit run app.py"""
import io
import json
import zipfile

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from simulation.config import ROOT, fmt_min, load_config
from simulation.energy import END_USES
from simulation.inputs import reference_daily_profile, self_reported_monthly_grid_kwh
from simulation.metrics import (events_df, groups_df, interval_df, itinerary_df, paired_comparison, queue_stats_df,
                                summary, verify)
from simulation.scenarios import ALL_OFF, SCENARIOS
from simulation.engine import Simulation
from simulation.viewer import build_payload, render_html

st.set_page_config(page_title="Virtual Hotel Monitoring", page_icon="🏝️", layout="wide")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Baloo+2:wght@700;800&family=Nunito:wght@500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stCaption { font-family: "Nunito", sans-serif; }
.block-container{padding-top:1.2rem}
h1, h2, h3 { font-family: "Baloo 2", sans-serif !important; color: #4a2a12; }
h1 { -webkit-text-stroke: 1px #7a3e12; color: #f08a24 !important; text-shadow: 0 3px 0 #7a3e12; }
div[data-testid="stMetric"] { background: #fffdf6; border: 3px solid #8a4b1e; border-radius: 18px; padding: 8px 12px;
  box-shadow: 0 4px 0 #6b3714; }
div[data-testid="stMetricValue"]{ font-family: "Baloo 2", sans-serif; font-size: 1.5rem; color: #3e9e2a; }
section[data-testid="stSidebar"] { border-right: 5px solid #8a4b1e; }
.stButton button, .stDownloadButton button, .stFormSubmitButton button { border-radius: 14px; border: 2px solid #2c6e1d;
  font-family: "Baloo 2", sans-serif; font-weight: 800; box-shadow: 0 3px 0 #2c6e1d; }
button[data-baseweb="tab"] { font-family: "Baloo 2", sans-serif; font-weight: 700; }
</style>""", unsafe_allow_html=True)
st.title("Virtual Hotel Monitoring · mô phỏng tác tử khách sạn 533 phòng + công viên")

POLICY_LABELS = {"occupancy_hvac": "Điều hòa phòng theo hiện diện (setback + làm mát trước)",
                 "housekeeping_coordination": "Phối hợp buồng phòng & khuyến khích tái sử dụng khăn",
                 "park_recommendations": "Gợi ý lộ trình trong công viên",
                 "ride_dispatch": "Điều phối chuyến trò chơi (gom khách trong giới hạn chờ)"}

# ------------------------------------------------------------------ sidebar
base = load_config()
with st.sidebar:
    st.header("Thiết lập")
    with st.form("cfg"):
        seed = st.number_input("Seed", 0, 10_000, int(base["seed"]))
        occupancy = st.slider("Công suất phòng (occupancy)", 0.0, 1.0, float(base["hotel"]["occupancy"]), 0.01)
        days = st.number_input("Số ngày mô phỏng", 1, int(base["max_horizon_days"]), int(base["horizon_days"]))
        st.markdown("**Chính sách xanh**")
        pol = {k: st.checkbox(v, value=False) for k, v in POLICY_LABELS.items()}
        ef = st.number_input("Hệ số phát thải (kg CO2e/kWh) – minh họa", 0.0, 2.0,
                             float(base["emissions"]["factor_kg_per_kwh"]), 0.01)
        with st.expander("Năng lực & hành vi"):
            park_int = st.slider("Tỷ lệ nhóm định đi công viên", 0.0, 1.0, float(base["population"]["park_intention"]), 0.05)
            rec_scale = st.slider("Hệ số chấp nhận gợi ý", 0.0, 2.0, 1.0, 0.1)
            tol = st.slider("Ngưỡng chịu chờ trung vị (phút)", 5, 90, int(base["population"]["queue_tolerance_min_median"]))
            bus_cap = st.number_input("Sức chứa xe nội khu (người/chuyến)", 5, 200, int(base["transport"]["capacity_persons"]))
            bus_int = st.number_input("Tần suất xe (phút)", 5, 60, int(base["transport"]["interval_min"]))
            workers = st.number_input("Số nhân viên buồng phòng", 0, 100, int(base["housekeeping"]["workers"]))
            machines = st.number_input("Số máy giặt công nghiệp", 0, 20, int(base["laundry"]["machines"]))
            transport_e = st.checkbox("Tính điện phương tiện kết nối", value=bool(base["transport"]["include_transport_energy"]))
        submitted = st.form_submit_button("▶ Chạy mô phỏng", type="primary", use_container_width=True)
    st.caption("Số phòng sản xuất cố định 533. Các tham số là giả định synthetic (xem tab Giả định).")

overrides = {"seed": int(seed), "horizon_days": int(days), "hotel": {"occupancy": occupancy},
             "policies": pol, "emissions": {"factor_kg_per_kwh": ef},
             "population": {"park_intention": park_int, "recommendation_acceptance_scale": rec_scale,
                            "queue_tolerance_min_median": tol},
             "transport": {"capacity_persons": int(bus_cap), "interval_min": int(bus_int),
                           "include_transport_energy": transport_e},
             "housekeeping": {"workers": int(workers)}, "laundry": {"machines": int(machines)}}


@st.cache_resource(max_entries=4, show_spinner=False)
def run_cached(cfg_json):
    return Simulation(json.loads(cfg_json)).run()


def scenario_label(p):
    on = [k for k, v in p.items() if v]
    for name, sc in SCENARIOS.items():
        if {**ALL_OFF, **sc["policies"]} == {**ALL_OFF, **p}:
            return sc["label_vi"]
    return "Tùy chỉnh: " + ", ".join(on)


if submitted or "cfg_json" not in st.session_state:
    st.session_state.cfg_json = json.dumps(load_config(overrides), sort_keys=True)
cfg = json.loads(st.session_state.cfg_json)
with st.spinner("Đang chạy mô phỏng 533 phòng…"):
    sim = run_cached(st.session_state.cfg_json)
S = summary(sim)
IV = interval_df(sim)
label = scenario_label(cfg["policies"])

c = st.columns(7)
c[0].metric("Tổng điện (kWh)", f"{S['total_kwh']:,.0f}")
c[1].metric("Đỉnh 15 phút (kW)", f"{S['peak_15min_kw']:,.0f}")
c[2].metric("Phát thải (kg CO2e)", f"{S['emissions_kgco2e']:,.0f}")
c[3].metric("Điện KS / đêm phòng", f"{S['hotel_kwh_per_occupied_room_night']:,.1f}" if S["hotel_kwh_per_occupied_room_night"] else "–")
c[4].metric("Lượt vào công viên (nhóm)", S["park_entries"])
c[5].metric("Chờ TB trò chơi (phút)", f"{S['attraction_mean_wait_min']:.1f}")
c[6].metric("Thời gian chạy (s)", f"{sim.runtime_s:.2f}")
st.caption(f"Kịch bản: **{label}** · seed {cfg['seed']} · {S['rooms']} phòng · {S['occupied_rooms']} phòng có khách · "
           f"{S['guest_groups']} nhóm · {S['guests']} khách · ranh giới: điện mua từ lưới, "
           f"{'có' if S['transport_energy_included'] else 'KHÔNG'} gồm điện phương tiện kết nối.")

tabs = st.tabs(["🗺️ Bản đồ 3D", "🏨 Lưới 533 phòng", "🧭 Hành trình nhóm khách", "⚡ Điện & phát thải",
                "⏳ Hàng chờ & dịch vụ", "⚖️ So sánh kịch bản", "📚 Giả định & dữ liệu"])

# ------------------------------------------------------------------ 1. 3D map
with tabs[0]:
    st.caption("Kéo để xoay, cuộn để phóng to, bấm nhãn để bay tới khu vực.")
    components.html(render_html(build_payload(sim, label)), height=760, scrolling=False)

# ------------------------------------------------------------------ 2. room grid
snaps = sim.snapshots
times = [s["t"] for s in snaps]
with tabs[1]:
    cc = st.columns([3, 1])
    ti = cc[0].select_slider("Thời điểm", options=list(range(len(times))), value=min(len(times) - 1, 14 * 12),
                             format_func=lambda i: fmt_min(times[i]))
    mode = cc[1].radio("Tô màu theo", ["Nhiệt độ", "Trạng thái"], horizontal=True)
    snap = snaps[ti]
    floors = cfg["hotel"]["floors"]
    per = int(np.ceil(sim.rooms.n / floors))
    pad = floors * per - sim.rooms.n
    if mode == "Nhiệt độ":
        z = np.append(snap["room_temp"], [np.nan] * pad).reshape(floors, per)
        fig = px.imshow(z[::-1], color_continuous_scale="RdYlBu_r", zmin=22, zmax=31, aspect="auto",
                        labels=dict(color="°C"), y=[f"Tầng {f}" for f in range(floors, 0, -1)])
    else:
        code = np.zeros(sim.rooms.n)
        sold = sim.rooms.sold
        code[sold & ~snap["room_occ"]] = 1
        code[sold & ~snap["room_occ"] & snap["room_control"]] = 2
        code[snap["room_occ"]] = 3
        code[snap["room_clean"]] = 4
        z = np.append(code, [np.nan] * pad).reshape(floors, per)
        fig = px.imshow(z[::-1], color_continuous_scale=[[0, "#2b3555"], [.25, "#4a5a86"], [.5, "#43c6db"],
                                                          [.75, "#f6d365"], [1, "#6cbf3f"]], aspect="auto", zmin=0, zmax=4,
                        y=[f"Tầng {f}" for f in range(floors, 0, -1)])
        fig.update_coloraxes(colorbar=dict(tickvals=[0, 1, 2, 3, 4], ticktext=["Không bán", "Vắng, HVAC tắt",
                                                                              "Vắng, HVAC chạy", "Có khách", "Đang dọn"]))
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Lưới gồm đúng 533 phòng (13 tầng × 41 phòng, ô cuối trống nếu có). Ngoài trời: "
               f"{snap['t_out']:.1f} °C. Ngưỡng tiện nghi: {cfg['rooms']['comfort_threshold_c']} °C.")
    room = st.number_input("Chọn phòng (1–533)", 1, sim.rooms.n, 1) - 1
    gid = int(sim.rooms.group_of[room])
    st.write(f"Phòng {room + 1}: " + (f"có nhóm #{gid} ({sim.groups[gid].size} người, điểm đặt "
                                       f"{sim.groups[gid].guest_setpoint} °C)" if gid >= 0 else "không bán (trống)") +
             f" · keycard bypass: {'có' if sim.rooms.bypass[room] else 'không'}")
    hist = pd.DataFrame(dict(t=[fmt_min(s["t"]) for s in snaps], temp=[float(s["room_temp"][room]) for s in snaps],
                             setpoint=[float(s["room_sp"][room]) for s in snaps], t_out=[s["t_out"] for s in snaps],
                             hvac_kw=[float(s["room_hvac_kw"][room]) for s in snaps],
                             light_plug_kw=[float(s["room_light_kw"][room]) for s in snaps],
                             occupied=[int(s["room_occ"][room]) for s in snaps],
                             cleaning=[int(s["room_clean"][room]) for s in snaps]))
    f2 = go.Figure()
    f2.add_scatter(x=hist.t, y=hist.temp, name="Nhiệt độ phòng (°C)")
    f2.add_scatter(x=hist.t, y=hist.setpoint, name="Điểm đặt (°C)", line=dict(dash="dot"))
    f2.add_scatter(x=hist.t, y=hist.t_out, name="Ngoài trời (°C)", line=dict(color="#aaa"))
    f2.add_bar(x=hist.t, y=hist.occupied * 22, name="Có khách", opacity=.15, marker_color="#f6b800")
    f2.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), yaxis=dict(range=[20, 36]), title="Nhiệt độ")
    st.plotly_chart(f2, use_container_width=True)
    st.plotly_chart(px.area(hist, x="t", y=["hvac_kw", "light_plug_kw"], height=260,
                            labels={"value": "kW", "variable": ""}, title="Công suất thiết bị phòng (ảnh chụp 5 phút)"),
                    use_container_width=True)

# ------------------------------------------------------------------ 3. group itinerary
with tabs[2]:
    if not sim.groups:
        st.info("Không có nhóm khách (công suất 0%).")
    else:
        gsel = st.number_input("Mã nhóm", 0, len(sim.groups) - 1, 0)
        g = sim.groups[gsel]
        cols = st.columns(4)
        cols[0].metric("Phòng", g.room + 1)
        cols[1].metric("Quy mô", f"{g.size} ({g.adults} NL, {g.children} TE)")
        cols[2].metric("Loại nhóm", {"solo": "Một mình", "couple": "Cặp đôi", "family": "Gia đình", "friends": "Bạn bè"}[g.category])
        cols[3].metric("Ngưỡng chịu chờ", f"{g.queue_tolerance:.0f} phút")
        st.write("Sở thích:", {k: round(v, 2) for k, v in g.pref.items()},
                 f"· ngân sách còn {g.budget_left:,.0f} VND · ưu tiên tiết kiệm {g.conservation_pref:.2f} · "
                 f"xác suất chấp nhận gợi ý {g.rec_acceptance:.2f}")
        it = itinerary_df(sim)
        it = it[it.group_id == gsel][["start", "end", "activity", "node", "wait_min"]]
        st.subheader("Hành trình")
        st.dataframe(it, use_container_width=True, hide_index=True)
        dec = [d for d in sim.decision_log if d["gid"] == gsel]
        st.subheader("Lý do lựa chọn (các thành phần hữu dụng)")
        for d in dec:
            with st.expander(f"{fmt_min(d['t'])} → {d['chosen']}  (đói {d['hunger']}, mệt {d['fatigue']}, nóng {d['heat']})"
                             + (f" · {d['recommendation']}" if d["recommendation"] else "")):
                st.dataframe(pd.DataFrame(d["options"]), use_container_width=True, hide_index=True)
        ev = events_df(sim)
        with st.expander("Nhật ký sự kiện của nhóm"):
            st.dataframe(ev[ev.group_id == gsel], use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ 4. electricity
with tabs[3]:
    long = IV.melt(id_vars=["thoi_gian"], value_vars=[f"{k}_kwh" for k in END_USES], var_name="end_use", value_name="kwh")
    long["kW"] = long["kwh"] / (cfg["reporting_interval_min"] / 60)
    long["Mục đích"] = long["end_use"].str.replace("_kwh", "").map(lambda k: END_USES[k][0])
    st.plotly_chart(px.area(long, x="thoi_gian", y="kW", color="Mục đích", height=420,
                            title="Công suất trung bình 15 phút theo mục đích sử dụng (kW)"), use_container_width=True)
    tot = pd.DataFrame([(END_USES[k][0], END_USES[k][1], S[f"kwh_{k}"]) for k in END_USES],
                       columns=["Mục đích", "Ranh giới", "kWh"])
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.bar(tot.sort_values("kWh"), x="kWh", y="Mục đích", color="Ranh giới", orientation="h",
                           height=420, title="Điện theo mục đích (kWh)"), use_container_width=True)
    c2.plotly_chart(px.line(IV, x="thoi_gian", y="emissions_kgco2e", height=420,
                            title=f"Phát thải liên quan điện mỗi 15 phút (kg CO2e) · hệ số {S['emission_factor_mean']:.2f}"),
                    use_container_width=True)
    st.info("Phát thải = điện mua từ lưới × hệ số phát thải minh họa. Không phải tổng dấu chân carbon của khu nghỉ "
            "(không gồm nhiên liệu khác, thực phẩm, rác thải, vật liệu, đi lại của khách). Với hệ số không đổi, dịch "
            "chuyển kWh theo giờ không làm giảm phát thải tính toán.")
    st.subheader("Tham chiếu (không cộng vào mô hình)")
    ref = self_reported_monthly_grid_kwh()
    month = int(cfg["start_date"][5:7])
    if ref:
        days_in = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        st.write(f"Điện lưới **tự khai (bản nháp, chưa duyệt)** của khu nghỉ tham chiếu tháng {month}: "
                 f"{ref['kwh'][month - 1]:,.0f} kWh ≈ **{ref['kwh'][month - 1] / days_in:,.0f} kWh/ngày** "
                 f"(nguồn: {ref['sheet']}, dòng {ref['row']}). Mô hình bottom-up khách sạn: "
                 f"**{S['hotel_kwh'] / sim.days:,.0f} kWh/ngày**. Chênh lệch chưa được giải thích/hiệu chỉnh — ranh giới "
                 "của số tự khai chưa rõ và mô hình chưa gồm nhiều phụ tải (chiller trung tâm, spa, hội nghị, khu nhân viên…).")
    try:
        prof = reference_daily_profile(month=month)
        model = IV.groupby(IV.interval_start_min % 1440)["total_kwh"].mean()
        model = (model / model.mean()).reset_index(drop=True)
        cmp = pd.DataFrame({"giờ": np.arange(96) / 4, "Tham chiếu NREL 36667 (chuẩn hóa)": prof.values[:96],
                            "Mô hình (chuẩn hóa)": model.values[:96]})
        st.plotly_chart(px.line(cmp, x="giờ", y=cmp.columns[1:], height=320,
                                title="So sánh HÌNH DẠNG phụ tải ngày (chia cho trung bình) — tòa nhà tham chiếu NREL "
                                      "không phải khu nghỉ mô phỏng, loại tòa nhà không được ghi trong file"),
                        use_container_width=True)
    except Exception as e:  # missing reference file must not break the dashboard
        st.caption(f"Không đọc được hồ sơ tham chiếu NREL: {e}")

# ------------------------------------------------------------------ 5. queues & services
with tabs[4]:
    qs = queue_stats_df(sim)
    st.dataframe(qs, use_container_width=True, hide_index=True)
    qt = pd.DataFrame([{**{"t": fmt_min(s["t"])}, **s["queue"]} for s in snaps])
    st.plotly_chart(px.line(qt, x="t", y=list(sim.fac), height=380, labels={"value": "người xếp hàng"},
                            title="Độ dài hàng chờ (người, ảnh chụp 5 phút)"), use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Phòng đã dọn", S["rooms_cleaned"])
    c2.metric("Phòng chưa dọn", S["rooms_pending_cleaning"])
    c3.metric("Dọn bị gián đoạn (khách về)", S["housekeeping_interruptions"])
    st.dataframe(pd.DataFrame(sim.hk_daily), use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Nhu cầu giặt (kg)", f"{S['laundry_demand_kg']:,.0f}")
    c2.metric("Đồ giặt tồn cuối kỳ (kg)", f"{S['laundry_pending_kg']:,.1f}")
    c3.metric("Điện để giặt nốt (kWh, ước tính)", f"{S['laundry_completion_kwh_estimate']:,.1f}")
    st.dataframe(pd.DataFrame(sim.laundry_batches), use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Suất ăn phục vụ (người)", S["meals_served_persons"])
    c2.metric("Đi bộ TB / nhóm (phút)", f"{S['mean_walking_min_per_group']:.0f}")
    c3.metric("Chờ + đi xe TB / nhóm (phút)", f"{S['mean_transport_min_per_group']:.0f}")
    st.metric("Giờ-phòng có khách vượt ngưỡng nóng", f"{S['occupied_room_hours_above_comfort']:.1f}")

# ------------------------------------------------------------------ 6. scenario comparison
with tabs[5]:
    st.write("Chạy các kịch bản với **cùng seed** (so sánh cặp). Điều kiện ngoại sinh (dân số khách, thời tiết, nhiệt độ "
             "phòng ban đầu, thiết bị) được giữ nguyên theo seed; quỹ đạo hành vi có thể khác do chính sách.")
    sc_sel = st.multiselect("Kịch bản", list(SCENARIOS), default=list(SCENARIOS),
                            format_func=lambda k: SCENARIOS[k]["label_vi"])
    nseeds = st.number_input("Số seed ghép cặp", 1, 50, 10)
    if st.button("Chạy so sánh", type="primary"):
        if "baseline" not in sc_sel:
            sc_sel = ["baseline"] + sc_sel
        rows, bar = [], st.progress(0.0)
        base_ov = {k: v for k, v in json.loads(st.session_state.cfg_json).items()}
        total_runs = len(sc_sel) * nseeds
        for i, sc in enumerate(sc_sel):
            for j in range(nseeds):
                c2_ = json.loads(json.dumps(base_ov))
                c2_["seed"] = int(cfg["seed"]) + j
                c2_["policies"] = {**ALL_OFF, **SCENARIOS[sc]["policies"]}
                s2 = Simulation(c2_).run()
                rows.append({"scenario": sc, "scenario_vi": SCENARIOS[sc]["label_vi"], **summary(s2)})
                bar.progress((i * nseeds + j + 1) / total_runs)
        st.session_state.per_seed = pd.DataFrame(rows)
    if "per_seed" in st.session_state:
        ps = st.session_state.per_seed
        comp = paired_comparison(ps)
        comp["Kịch bản"] = comp["scenario"].map(lambda k: SCENARIOS[k]["label_vi"])
        metric = st.selectbox("Chỉ số", comp.metric_vi.unique())
        sub = comp[comp.metric_vi == metric]
        st.plotly_chart(px.bar(sub, x="Kịch bản", y="mean_paired_diff", error_y="sd_paired_diff", height=380,
                               title=f"Chênh lệch ghép cặp so với baseline: {metric} (trung bình ± độ lệch chuẩn qua seed)"),
                        use_container_width=True)
        st.dataframe(comp[["Kịch bản", "metric_vi", "n_seeds", "mean", "sd", "baseline_mean", "mean_paired_diff",
                           "sd_paired_diff", "min_paired_diff", "max_paired_diff", "mean_pct_diff"]],
                     use_container_width=True, hide_index=True)
        tr = comp[comp.metric.isin(["total_kwh", "attraction_mean_wait_min"])].pivot(index="Kịch bản", columns="metric",
                                                                                       values="mean_paired_diff").reset_index()
        if {"total_kwh", "attraction_mean_wait_min"} <= set(tr.columns):
            st.plotly_chart(px.scatter(tr, x="total_kwh", y="attraction_mean_wait_min", text="Kịch bản", height=380,
                                       labels={"total_kwh": "Δ tổng điện (kWh)", "attraction_mean_wait_min": "Δ chờ TB (phút)"},
                                       title="Đánh đổi: điện năng vs. thời gian chờ (chênh lệch ghép cặp)"),
                            use_container_width=True)
        st.caption("Phạm vi min–max là phạm vi giữa các seed synthetic, không phải khoảng tin cậy và không phải kiểm chứng thực tế.")
        st.download_button("Tải per-seed CSV", ps.to_csv(index=False).encode("utf-8-sig"), "per_seed_comparison.csv")
        st.download_button("Tải bảng so sánh CSV", comp.to_csv(index=False).encode("utf-8-sig"), "scenario_summary.csv")

# ------------------------------------------------------------------ 7. assumptions & data
with tabs[6]:
    checks = pd.DataFrame(verify(sim))
    st.subheader(f"Kiểm tra mô hình: {int(checks.passed.sum())}/{len(checks)} đạt")
    st.dataframe(checks, use_container_width=True, hide_index=True)
    for fname, title in [("assumptions.csv", "Giả định"), ("verified_facts.csv", "Dữ kiện đã kiểm chứng"),
                         ("rule_registry.csv", "Sổ đăng ký quy tắc (từ file Excel)")]:
        p = ROOT / fname
        if p.exists():
            with st.expander(title, expanded=fname == "assumptions.csv"):
                st.dataframe(pd.read_csv(p), use_container_width=True, hide_index=True)
    p = ROOT / "data_provenance.md"
    if p.exists():
        with st.expander("Nguồn gốc dữ liệu"):
            st.markdown(p.read_text(encoding="utf-8"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("interval_electricity.csv", IV.to_csv(index=False))
        z.writestr("events.csv", events_df(sim).to_csv(index=False))
        z.writestr("itineraries.csv", itinerary_df(sim).to_csv(index=False))
        z.writestr("groups.csv", groups_df(sim).to_csv(index=False))
        z.writestr("queue_stats.csv", queue_stats_df(sim).to_csv(index=False))
        z.writestr("housekeeping.csv", pd.DataFrame(sim.hk_log).to_csv(index=False))
        z.writestr("laundry_batches.csv", pd.DataFrame(sim.laundry_batches).to_csv(index=False))
        z.writestr("summary.csv", pd.DataFrame([S]).to_csv(index=False))
        z.writestr("verification.csv", checks.to_csv(index=False))
        z.writestr("config.json", st.session_state.cfg_json)
        z.writestr("resort3d.html", render_html(build_payload(sim, label)))
    st.download_button("⬇ Tải toàn bộ kết quả (ZIP)", buf.getvalue(), f"virtual_hotel_monitoring_seed{cfg['seed']}.zip",
                       type="primary")
    st.caption(f"Tổng điện trong file xuất = {IV['total_kwh'].sum():,.3f} kWh = chỉ số trên dashboard "
               f"({S['total_kwh']:,.3f} kWh).")
