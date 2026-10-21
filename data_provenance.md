# Data provenance

Every input to this prototype falls into one of the categories below. Nothing in it is measured operating data
from a real property.

## 1. Project requirements
- From the project brief: 533 hotel rooms, 80% default occupancy, the group-size distribution, 75% park
  intention, seed 42, 15-minute reporting and an illustrative emission factor of 0.60 kg CO2e/kWh.

## 2. Verified destination facts
- None. `verified_facts.csv` is intentionally empty.
- Attraction names, capacities, opening hours, staffing and equipment are generic synthetic values.

## 3. Source files (local only, not in the repository)

### Green-tourism assessment workbook (`assessment_answers_categorized.xlsx`)
- Self-assessment answers for several coastal resorts.
- The profile used here is a **draft** with every answer still **pending review**. It is therefore treated as
  reference material, not as verified facts.
- The property name is read from the local file `config/source_data.json`, which is not versioned.
- All rows of that profile are in `rule_registry.csv`. Each row keeps its sheet, row number, original wording,
  category, interpretation and status. 7 rows are implemented as model rules or parameters; the rest are
  reference-only, deferred or not applicable.
- Answers that shaped the model:
  - Q1.1: number of rooms.
  - Q2.14: automatic room HVAC sensors in 76–100% of rooms. This motivates the keycard baseline with 25% bypass.
  - Q2.18: room AC set to 24–26 °C in summer.
  - Q2.42: towels and linen replaced only on request.
  - Q2.79: guest conservation incentives.
- Q1.8 (monthly grid electricity) is shown **only as a reference**. It is never added to the bottom-up totals,
  because its system boundary is not documented.
- Inconsistency noted: Q1.2 (room-nights per year) implies 68.2% occupancy, while Q1.3 states 53%.

### Reference load profile (`36667_2018_15min_NREL.csv`)
- One building (`bldg_id` 36667), 35,040 rows at 15-minute resolution, covering 2018-01-01 00:15 to
  2019-01-01 00:00. Timestamps mark the end of each interval. There are no duplicates and no missing cells.
- Columns follow the NREL ComStock end-use convention.
- Values are treated as **kWh per 15-minute interval**. That is the column convention; the file itself does not
  document units, timezone or building type.
- It is used only for a normalized daily-shape comparison and is **never added** to the model totals.

### Illustrated resort maps
- Two illustrated tourist maps.
- They were used only as loose inspiration for the colour palette and the rough island layout of the 3D view.
- Zone names in the view are invented. The view is schematic and not to scale.

### UI design reference
- The dashboard and 3D-view UI styling (cream/wood panels, rounded type, green primary button, low-poly trees)
  is adapted from the visual style of https://cozyfarmy.com/.
- No assets or code from that site were copied.

## 4. Synthetic assumptions
- See `assumptions.csv` and `config/defaults.json`.
- Weather, guests, preferences, schedules, capacities, travel times, thermal parameters and equipment
  parameters are all synthetic.

## 5. Computed results
- Everything in `outputs/` and on the dashboards is computed by the simulation from the inputs above.
- These results are neither observations nor validation.
