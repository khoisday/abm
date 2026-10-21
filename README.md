# Virtual Hotel Monitoring

Agent-based simulation of guest behaviour across an integrated **533-room hotel and amusement park** on a tropical
island resort. It links guest choices to hotel and park operations, then to electricity, electricity-related
emissions, queues and service outcomes. Three views replay the simulation:
- a Streamlit dashboard;
- a static web dashboard;
- a 3D low-poly / pixel map with a cutaway view of the hotel's floors.

> **Synthetic research prototype.** It is not a validated operational replica and not a live digital twin. All
> behaviour, capacities, travel times, weather and equipment parameters are synthetic assumptions.

## Install and launch (Windows, Python 3.12 tested)

```powershell
pip install -r requirements.txt

# 1) Interactive dashboard (http://localhost:8501)
streamlit run app.py

# 2) Command line: 533-room baseline, seed 42, 1 day -> outputs/baseline_seed42/
python run_simulation.py
python run_simulation.py --scenario combined --seed 7 --days 3 --occupancy 0.53

# 3) Paired comparison: 6 scenarios x 10 seeds -> outputs/scenario_summary.csv, outputs/per_seed_comparison.csv
python run_simulation.py --compare --seeds 10

# 4) Verification (diagnostic / full / 100% stress / 0% boundary / zero factor / reproducibility) and tests
python run_simulation.py --verify-suite
python -m pytest -q

# 5) Static website -> site/ (dashboard + 3D maps + precomputed JSON/CSV)
python build_site.py
```

Each CLI run also writes a standalone 3D view, `outputs/<run>/<run>_resort3d.html`. It loads three.js, GSAP and
Google Fonts from CDNs, so it needs internet access.

### Hosting the static site
The site has no backend, so any static host works:
- **Vercel**: `cd site; vercel deploy --prod`.
- **Cloudflare Pages**: `npx wrangler pages deploy site`, or connect the repository.
- **GitHub Pages**: `python publish_pages.py` pushes `site/` to a `gh-pages` branch.

The Streamlit dashboard needs a long-running Python server, for example Streamlit Community Cloud or a VPS.

## Source data (local only)
The source files are not committed:
- the green-tourism assessment workbook `assessment_answers_categorized.xlsx`;
- the reference load profile `*_15min_NREL.csv`;
- two illustrated maps.

Simulation, tests and site build all run without them. When they are absent, the self-reported grid reference
and the load-shape comparison are skipped. To rebuild `rule_registry.csv` (`python -m simulation.inputs`), add
the workbook and put the assessed property's name in `config/source_data.json` as `{"facility": "..."}`.

## What is modelled

- **Rooms.** 533, a fixed inventory. At 80% occupancy, 426 rooms are sold and get one guest group each; that is
  1,135 guests with seed 42. The 107 unsold rooms stay in the electricity accounting.
- **Guest-group agents.** A group keeps its identity across room → breakfast → shuttle → park → meals/rest →
  shuttle → dinner → room. Preferences vary within solo/couple/family/friends groups. Each group also has:
  - a budget and a queue tolerance;
  - a conservation preference and a probability of accepting recommendations;
  - bounded 0–100 indices for hunger, fatigue and heat discomfort.
- **Decisions.** Infeasible options are filtered out. The remaining options are scored by utility (preference,
  interest, hunger/fatigue/heat relief, travel, expected wait, price, repeat visits, schedule conflict) and one is
  chosen with a numerically stable softmax. Groups see posted waits with noise, not future queues. The components
  of every decision are logged.
- **Environment.** A schematic walking graph with cached shortest paths, and a finite-capacity hotel–park shuttle.
- **Facilities.** FIFO queues with fill-in and anti-starvation rules. Types:
  - park: batch rides, a continuous indoor attraction, scheduled shows, a restaurant, rest areas;
  - hotel: restaurant, pool, **spa, gym and kids club**.
- **Queue rules.** Opening hours, a closing grace period, balking, abandonment and rejection.
- **Room thermal model.** Vectorized and first-order, on a 1-minute step. It includes a deadband thermostat and
  keycard / bypass / setback / pre-cooling logic. Pre-cooling uses predicted return times with error, never the
  actual future return.
- **Housekeeping.** 26 attendants with shifts. Privacy rules: rooms are cleaned only while guests are away, and
  DND is respected. The model also covers interruptions when guests return, towel and linen requests, and the
  laundry demand they create.
- **Laundry.** A finite number of machines running batches. The kilograms left over and the energy needed to
  finish them are reported.
- **Electricity.** 13 end uses, aggregated into 15-minute intervals. Emissions = kWh × factor (illustrative
  0.60 kg CO2e/kWh, or a time-varying CSV). Transport electricity is excluded by default, and that boundary is shown.
- **Scenarios.**
  1. Baseline.
  2. Occupancy-based room HVAC.
  3. Housekeeping and towel-reuse coordination.
  4. Park recommendations.
  5. Ride-dispatch coordination.
  6. All of the above combined.

## Repository layout

```
app.py                      Streamlit dashboard (Vietnamese UI)
run_simulation.py           CLI entry point
build_site.py               static site build (dashboard + 3D maps + data)
publish_pages.py            publish site/ to a gh-pages branch
simulation/                 config, rng streams, environment, agents, decisions, energy, engine, scenarios,
                            metrics, inputs (source-file inspection), viewer (3D payload)
web/dashboard.html          static dashboard (Plotly)
web/resort3d_template.html  3D map with hotel interior view (three.js r147 + GSAP 3.12.5)
config/defaults.json        all synthetic parameters
tests/                      pytest verification tests
assumptions.csv  verified_facts.csv  data_provenance.md  rule_registry.csv
docs/                       data plan for calibration / digital-twin work (.md, .docx)
```

## Verification (measured 2026-10-21)

- `pytest`: **19 passed**.
- `--verify-suite`: 17/17 model checks pass in every run.

  | Run | Runtime |
  |---|---|
  | Diagnostic, 60 rooms | 0.4 s |
  | 533 rooms at 80% | 1.6 s |
  | 533 rooms at 100% (1,433 guests) | 4.7 s |
  | 533 rooms at 0% (base loads only) | 0.4 s |

- A zero emission factor gives 0 kg. The same seed and policy give identical results.
- What the checks cover:
  - inventory: 533 unique rooms, occupancy, one valid room per group, group composition;
  - flows: visitor conservation in every 5-minute snapshot, travel takes time, facility and shuttle capacities
    hold, no admissions after closing, no stranded groups, no worker doing two tasks at once;
  - energy accounting: vacant-room standby, end uses sum to the total, energy equals integrated power, 15-minute
    and 1-minute totals agree, emissions equal kWh × factor;
  - outstanding work is reported.
- The 6 × 10 paired comparison (60 runs) took 170 s.

## Computed baseline (seed 42, 1 day, synthetic)

| Metric | Value |
|---|---|
| Total electricity | 15,532 kWh (hotel 11,432 · park 4,100 · transport excluded) |
| Peak 15-min power | 949 kW |
| Electricity-related emissions | 9,319 kg CO2e (illustrative factor) |
| Hotel kWh per occupied room-night | 26.8 |
| Park entries / completed attraction visits | 332 groups / 9,038 person-visits |
| Mean attraction wait | 5.5 min; 60 abandoned queue joins |
| Hot occupied room-hours (> 27 °C) | 133 |

**Nothing has been calibrated.** The self-reported grid electricity in the assessment workbook is much higher.
Several loads are not modelled (central chiller plant, conference, staff areas…), and the boundary of the
self-reported figure is unknown.

## Paired comparison (10 seeds, mean paired difference vs baseline ± SD)

Source: `outputs/scenario_summary.csv`.

- **Occupancy HVAC**: +257 ± 14 kWh (+1.6%). A 28 °C setback replaces the baseline keycard shut-off. In return,
  hot occupied room-hours fall from 127 to 0.9.
- **Housekeeping coordination**: −106 ± 14 kWh (−0.7%), but with two costs:
  - hot room-hours rise by 76, because HVAC stays off during cleaning;
  - about 27 more rooms are left uncleaned.
- **Recommendations**: +5 ± 19 kWh. Mean wait −0.06 ± 0.17 min, which is within seed noise.
- **Ride dispatch**: −101 ± 29 kWh (−0.6%). Mean wait −0.14 ± 0.11 min.
- **Combined**: +90 ± 37 kWh (+0.6%). Overheating drops to near zero, but about 27 more rooms are left
  uncleaned.

These are synthetic tradeoffs that illustrate the mechanism. The ranges show seed-to-seed variability; they are
not confidence intervals and not validation.

## Limitations

- Not modelled within the horizon: check-in/check-out, external day visitors, on-site PV, water and fuel use.
- Guests are modelled at group level: members of a group never split up.
- Attraction names, capacities, hours, staffing and geometry are generic. The map is schematic and not to scale.
- There is no calibration or external validation yet. `docs/ke_hoach_du_lieu_digital_twin.md` lists the data
  this would need.
