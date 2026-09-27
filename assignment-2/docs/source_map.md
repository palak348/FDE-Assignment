# Source map (Class 4)

Start from the business question, not the data. Every sub-question maps to the information it needs and the system that owns it.

## Business question → information → source

| # | Sub-question | Information needed | Source (system of record?) | Owner | Retrieval mode | Grain |
|---|---|---|---|---|---|---|
| Q1 | Did taxi trips inside the zone get faster? | pickup/dropoff time, distance, pickup/dropoff zone | TLC Yellow Taxi Trip Records **(SoR for trips)** | NYC TLC, from vendor meters | File: Parquet over HTTPS | 1 row = 1 metered trip |
| Q2 | Which zones are inside the toll zone? | zone id → name/borough; zone id → in CBD | TLC Taxi Zone Lookup + MTA CBD Taxi Zones **(MTA is SoR for the toll boundary)** | TLC / MTA | File: CSV + API: Socrata JSON (paginated) | 1 row = 1 zone |
| Q3 | Was the toll actually applied to the trip? | `cbd_congestion_fee` per trip | TLC trip records (2025+) | NYC TLC | same Parquet file | per trip |
| Q4 | Could weather explain a change? | daily rain and snow | Open-Meteo historical archive (ERA5 reanalysis, **not an official station**) | Open-Meteo | API: REST JSON | 1 row = 1 day, 1 point for the city |
| Q5 | Does our number agree with the official one? | monthly CBD speed | MTA CBD Taxi/FHV Speeds | MTA | API: Socrata JSON | 1 row = zone × month |

All raw files land in `data/raw/`, listed in `manifest.json`, and are then loaded to DuckDB with SQL (the third mode).

## Four lenses per source

| Source | Ownership | Freshness | Replication | Semantics / gaps |
|---|---|---|---|---|
| TLC trip records | Vendors (CMT, Curb, Myle, Helix) record trips; TLC publishes them | Monthly, ~2 month lag; files get re-issued | TLC's cleaned copy of vendor submissions | No GPS or route, so pass-through trips are invisible. Distance is odometer. `cbd_congestion_fee` only exists from 2025. `payment_type=0` rows lack metadata. Vendor 7 records pickup = dropoff. |
| TLC zone lookup | TLC | Static | n/a | Zones 264/265 = "Unknown"/"Outside NYC" |
| MTA CBD zones | MTA (toll operator) | Static since 2024 | n/a | Zone-level approximation of the real street boundary (60th St) |
| Open-Meteo | Third party | Daily, final after ~5 days | Model output, not a measurement | One point for the whole city; daily, not hourly |
| MTA speeds | MTA | Monthly | Published aggregate | Taxi **and** FHV, whole month, methodology not published per row |

## Gaps I would raise with the client

| Gap | Impact | Ask |
|---|---|---|
| No route or GPS trace | Can't measure speed *inside* the zone for pass-through trips | TLC: expose a per-trip "entered CRZ" flag |
| 15.5% of 2025 rows have `payment_type=0` with no passenger count or rate code (4.7% in 2024) | Can't segment by rate code | TLC: explain the payment_type=0 feed |
| Vendor 7 durations are all zero | Its trips can't be used for speed | TLC / vendor: fix timestamp capture |
| 1.6% of zone trips after launch have no fee | Either revenue leakage or undocumented exemptions / meter setup | TLC + MTA: which trips are exempt, and which meters were not updated |
