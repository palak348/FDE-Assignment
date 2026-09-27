"""Stage 5 - metrics.

Project KPI: median taxi speed inside the Congestion Relief Zone (CBD) at weekday peak.
Supporting metrics describe reliability, demand and whether the intervention was applied.
Controls guard against crediting pricing for a change that weather or a citywide trend explains.

Only months that passed every batch gate ("published") are used.
"""
from dataclasses import dataclass

from . import config as C

PEAK = f"pickup_hour >= {C.PEAK_START_HOUR} and pickup_hour < {C.PEAK_END_HOUR}"
CBD_PEAK = f"speed_eligible and trip_scope = 'cbd_internal' and {PEAK}"


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    unit: str
    kind: str       # 'kpi' or 'control'
    better: str     # 'up', 'down' or 'neutral'
    value_sql: str
    n_sql: str      # number of trips (or days) behind the value


METRICS = [
    Metric("M1", "Median CBD trip speed, weekday peak", "mph", "kpi", "up",
           f"median(speed_mph) filter (where {CBD_PEAK})",
           f"count(*) filter (where {CBD_PEAK})"),
    Metric("M2", f"Share of CBD peak trips crawling (<{C.CRAWL_MPH:g} mph)", "%", "kpi", "down",
           f"100 * avg((speed_mph < {C.CRAWL_MPH})::int) filter (where {CBD_PEAK})",
           f"count(*) filter (where {CBD_PEAK})"),
    Metric("M3", "90th pct travel time per mile, CBD peak", "min/mile", "kpi", "down",
           f"quantile_cont(60 / speed_mph, 0.9) filter (where {CBD_PEAK})",
           f"count(*) filter (where {CBD_PEAK})"),
    Metric("M4", "CBD-internal taxi trips per weekday", "trips/day", "kpi", "neutral",
           "count(*) filter (where trip_scope = 'cbd_internal') / count(distinct pickup_date)",
           "count(distinct pickup_date)"),
    Metric("M5", "CBD trips charged the congestion fee", "%", "kpi", "up",
           "case when count(cbd_congestion_fee) filter (where trip_scope <> 'outside') = 0 then null else "
           "100 * avg((coalesce(cbd_congestion_fee, 0) > 0)::int) filter (where trip_scope <> 'outside') end",
           "count(*) filter (where trip_scope <> 'outside')"),
    Metric("C1", "Control: median speed of trips outside the CBD, weekday peak", "mph", "control", "neutral",
           f"median(speed_mph) filter (where speed_eligible and trip_scope = 'outside' and {PEAK})",
           f"count(*) filter (where speed_eligible and trip_scope = 'outside' and {PEAK})"),
    Metric("C2", "Control: M1 on dry days only", "mph", "control", "up",
           f"median(speed_mph) filter (where {CBD_PEAK} and not is_wet)",
           f"count(*) filter (where {CBD_PEAK} and not is_wet)"),
    Metric("C4", "Control: C1 on dry days only", "mph", "control", "neutral",
           f"median(speed_mph) filter (where speed_eligible and trip_scope = 'outside' and {PEAK} and not is_wet)",
           f"count(*) filter (where speed_eligible and trip_scope = 'outside' and {PEAK} and not is_wet)"),
]

# Net effect = change inside the zone minus change outside it, both on dry days only, so
# weather and any citywide trend cancel out (a light difference-in-differences).
NET_EFFECT = ("C2", "C4")


def window_trips_sql(months: list[str]) -> str:
    in_list = ", ".join(f"'{m}'" for m in months)
    return f"""
        select f.*, d.period, d.is_wet
        from fact_trip_clean f join dim_date d on d.day = f.pickup_date
        where d.in_window and f.is_valid_trip and f.source_month in ({in_list})"""


def compute(con, months: list[str]):
    """Long-format metrics per month/period, plus the MTA benchmark (C3) as a cross-check."""
    cols = ",\n".join(f"{m.value_sql} as v_{m.id}, {m.n_sql} as n_{m.id}" for m in METRICS)
    wide = con.execute(f"""
        select source_month, any_value(period) as period,
               count(distinct pickup_date) as window_days,
               count(distinct pickup_date) filter (where is_wet) as wet_days,
               {cols}
        from ({window_trips_sql(months)}) group by source_month order by source_month""").df()

    rows = []
    for _, r in wide.iterrows():
        for m in METRICS:
            rows.append({"source_month": r.source_month, "period": r.period, "metric_id": m.id,
                         "metric": m.name, "unit": m.unit, "kind": m.kind, "better": m.better,
                         "value": r[f"v_{m.id}"], "n": int(r[f"n_{m.id}"])})
        mta = con.execute("select zonal_speed from raw_mta_speeds where zone = 'CBD' and month = ?",
                          [f"{r.source_month}-01"]).fetchone()
        rows.append({"source_month": r.source_month, "period": r.period, "metric_id": "C3",
                     "metric": "Control: MTA published CBD taxi/FHV speed (whole month)", "unit": "mph",
                     "kind": "control", "better": "up", "value": mta[0] if mta else None, "n": None})
    context = wide[["source_month", "period", "window_days", "wet_days"]]
    return rows, context


def before_after(rows: list[dict]):
    import pandas as pd
    df = pd.DataFrame(rows)
    keys = ["metric_id", "metric", "unit", "kind", "better"]
    side = lambda p: (df[df.period == p].set_index(keys)[["value", "n"]]
                      .rename(columns={"value": p, "n": f"n_{p}"}))
    out = side("before").join(side("after"), how="outer").reset_index()
    out = out[keys + ["before", "after", "n_before", "n_after"]]
    out["change"] = out["after"] - out["before"]
    out["change_pct"] = 100 * out["change"] / out["before"]

    def verdict(r):
        if pd.isna(r.change) or r.better == "neutral":
            return ""
        return "better" if (r.change > 0) == (r.better == "up") else "worse"
    out["direction"] = out.apply(verdict, axis=1)
    return out.sort_values("metric_id", key=lambda s: s.str[0].map({"M": 0, "C": 1}) * 10 + s.str[1:].astype(int))


def net_effect_pp(table) -> float:
    inside, outside = (table.set_index("metric_id").loc[m, "change_pct"] for m in NET_EFFECT)
    return inside - outside


def hourly_cbd_speed(con, months: list[str]):
    return con.execute(f"""
        select period, pickup_hour, median(speed_mph) as median_mph, count(*) as trips
        from ({window_trips_sql(months)})
        where speed_eligible and trip_scope = 'cbd_internal'
        group by all order by period, pickup_hour""").df()
