"""Stage 3 - validation.

Two layers:
  * ROW RULES flag individual trips. Nothing is deleted or "fixed": every rule becomes a
    boolean dq_<rule> column on fact_trip, and the rule's severity decides which metrics the
    trip may feed. Counts per rule are stored in dq_results for every month.
  * BATCH GATES decide whether a whole month is trustworthy enough to publish. A failed gate
    quarantines the month: its metrics are not produced and the run exits non-zero.

Severity:
  exclude_all   - not a real, locatable trip in this month; kept out of every metric
  exclude_speed - a real trip, but its duration/distance cannot support a speed figure
  warn          - recorded and reported; does not change any metric
"""
from dataclasses import dataclass

from . import config as C
from .log import get_logger

log = get_logger()


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: str
    sql: str          # boolean SQL over stg_trips + zone flags (pu_known, do_known, pu_cbd, do_cbd)
    why: str


ROW_RULES = [
    Rule("R01", "out_of_month", "exclude_all",
         "pickup_month <> source_month",
         "Pickup falls outside the file's month (e.g. 2002 or 31 Dec). Belongs to another month's file."),
    Rule("R02", "duplicate", "exclude_all",
         "dup_rank > 1",
         "Exact repeat of another record (same vendor, times, zones, distance, amount)."),
    Rule("R03", "reversal_record", "exclude_all",
         "total_amount < 0",
         "Negative total = void/refund adjustment of another trip, not a separate trip."),
    Rule("R04", "unknown_zone", "exclude_all",
         f"not pu_known or not do_known or PULocationID in {C.UNKNOWN_ZONES} or DOLocationID in {C.UNKNOWN_ZONES}",
         "Pickup or dropoff zone is Unknown/Outside NYC or not in the zone lookup: cannot place the trip."),
    Rule("R05", "bad_duration", "exclude_speed",
         f"duration_min < {C.MIN_DURATION_MIN} or duration_min > {C.MAX_DURATION_MIN}",
         f"Duration under {C.MIN_DURATION_MIN:g} min (incl. zero/negative) or over {C.MAX_DURATION_MIN:g} min: meter not closed properly."),
    Rule("R06", "zero_distance", "exclude_speed",
         "trip_distance is null or trip_distance <= 0",
         "No distance recorded: cancelled trip or odometer fault."),
    Rule("R07", "extreme_distance", "exclude_speed",
         f"trip_distance > {C.MAX_DISTANCE_MI}",
         f"Over {C.MAX_DISTANCE_MI:g} miles inside the city: odometer error (max seen ~300,000 mi)."),
    Rule("R08", "implausible_speed", "exclude_speed",
         f"speed_mph < {C.MIN_SPEED_MPH} or speed_mph > {C.MAX_SPEED_MPH}",
         f"Average speed outside {C.MIN_SPEED_MPH:g}-{C.MAX_SPEED_MPH:g} mph is physically implausible in NYC."),
    Rule("R09", "extreme_amount", "warn",
         f"total_amount > {C.MAX_TOTAL_AMOUNT}",
         "Total above $1,000 (max seen $863k): likely keying error. No fare metric is used, so warn only."),
    Rule("R10", "unknown_vendor", "warn",
         f"VendorID not in {C.KNOWN_VENDORS}",
         "Vendor code not in the TLC data dictionary."),
    Rule("R11", "missing_trip_metadata", "warn",
         "passenger_count is null",
         "payment_type=0 records arrive without passenger count / rate code. Times and zones look normal, so kept."),
    Rule("R12", "fee_before_pricing", "warn",
         f"cbd_congestion_fee > 0 and tpep_pickup_datetime < date '{C.PRICING_START}'",
         "CBD fee charged before the toll existed: meter configuration issue."),
    Rule("R13", "fee_missing_in_cbd", "warn",
         f"tpep_pickup_datetime >= date '{C.PRICING_START}' and (pu_cbd or do_cbd) "
         "and coalesce(cbd_congestion_fee, 0) <= 0",
         "Trip started or ended in the zone after launch but was not charged the CBD fee."),
]

RULES_BY_SEVERITY = {s: [r for r in ROW_RULES if r.severity == s]
                     for s in ("exclude_all", "exclude_speed", "warn")}


def flag_columns_sql() -> str:
    return ",\n".join(f"coalesce({r.sql}, false) as dq_{r.name}" for r in ROW_RULES)


def any_of(severity: str) -> str:
    return " or ".join(f"dq_{r.name}" for r in RULES_BY_SEVERITY[severity]) or "false"


# ----------------------------------------------------------------- results
def record_rule_results(con, month: str) -> None:
    con.execute("""create table if not exists dq_results
                   (source_month varchar, rule_id varchar, rule varchar, severity varchar,
                    rows_flagged bigint, share double, why varchar)""")
    total = con.execute("select count(*) from fact_trip where source_month = ?", [month]).fetchone()[0]
    counts = con.execute(
        "select " + ", ".join(f"sum(dq_{r.name}::int)" for r in ROW_RULES) +
        " from fact_trip where source_month = ?", [month]).fetchone()
    con.execute("delete from dq_results where source_month = ?", [month])
    con.executemany("insert into dq_results values (?, ?, ?, ?, ?, ?, ?)",
                    [[month, r.id, r.name, r.severity, int(n or 0), (n or 0) / total, r.why]
                     for r, n in zip(ROW_RULES, counts)])


# ------------------------------------------------------------------- gates
def batch_gates(con, month: str, parquet_rows: int) -> list[dict]:
    """Return one result per gate. Any passed=False quarantines the month."""
    q = lambda sql, *p: con.execute(sql, [month, *p]).fetchone()
    results = []

    def gate(name, passed, detail):
        results.append({"source_month": month, "gate": name, "passed": bool(passed), "detail": detail})

    raw = q("select count(*) from raw_trips where source_month = ?")[0]
    fact = q("select count(*) from fact_trip where source_month = ?")[0]
    gate("rows_match_source_file", raw == parquet_rows == fact,
         f"parquet metadata={parquet_rows:,} raw_trips={raw:,} fact_trip={fact:,}")

    oom = q("select avg(dq_out_of_month::int) from fact_trip where source_month = ?")[0]
    gate("out_of_month_share", oom <= C.MAX_OUT_OF_MONTH_SHARE,
         f"{oom:.4%} of rows outside the month (limit {C.MAX_OUT_OF_MONTH_SHARE:.2%})")

    worst_day, worst_n, median_n, n_days, expected_days = q(f"""
        with d as (select pickup_date, count(*) as n
                   from fact_trip where source_month = ? and not dq_out_of_month group by 1)
        select arg_min(pickup_date, n), min(n), median(n), count(*),
               day(last_day(strptime(? || '-01', '%Y-%m-%d')))
        from d""", month)
    ok = n_days == expected_days and worst_n >= C.MIN_DAY_SHARE_OF_MEDIAN * median_n
    gate("every_day_present", ok,
         f"{n_days}/{expected_days} days; lowest {worst_day} with {worst_n:,} trips "
         f"({worst_n / median_n:.0%} of median day {median_n:,.0f})")

    inel = q(f"select avg(({any_of('exclude_all')} or {any_of('exclude_speed')})::int) "
             "from fact_trip where source_month = ?")[0]
    gate("speed_ineligible_share", inel <= C.MAX_SPEED_INELIGIBLE_SHARE,
         f"{inel:.2%} of trips unusable for speed (limit {C.MAX_SPEED_INELIGIBLE_SHARE:.0%})")

    wdays = q("select count(*) from raw_weather where source_month = ?")[0]
    gate("weather_complete", wdays == expected_days, f"{wdays}/{expected_days} days of weather")

    for g in results:
        (log.info if g["passed"] else log.error)("gate %s [%s] %s: %s", month,
                                                  "PASS" if g["passed"] else "FAIL", g["gate"], g["detail"])
    return results


def reference_gates(con) -> list[dict]:
    zones = con.execute("select count(*) from raw_zones").fetchone()[0]
    cbd, not_found, not_manhattan = con.execute("""
        select count(*), count(*) filter (where z.LocationID is null),
               count(*) filter (where z.Borough <> 'Manhattan')
        from raw_cbd_zones c left join raw_zones z on z.LocationID = c.taxi_zone""").fetchone()
    results = [
        {"source_month": "reference", "gate": "zone_lookup_rows", "passed": zones == C.EXPECTED_ZONE_ROWS,
         "detail": f"{zones} zones (expected {C.EXPECTED_ZONE_ROWS})"},
        {"source_month": "reference", "gate": "cbd_zones_valid",
         "passed": cbd == C.EXPECTED_CBD_ZONES and not_found == 0 and not_manhattan == 0,
         "detail": f"{cbd} CBD zones (expected {C.EXPECTED_CBD_ZONES}); {not_found} missing from lookup; "
                   f"{not_manhattan} outside Manhattan"},
    ]
    for g in results:
        (log.info if g["passed"] else log.error)("gate reference [%s] %s: %s",
                                                  "PASS" if g["passed"] else "FAIL", g["gate"], g["detail"])
    return results
