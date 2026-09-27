"""Stage 2 - ingest raw files into the DuckDB warehouse with SQL.

Raw tables keep source values exactly as delivered (no cleaning here) plus lineage columns
(source_month, source_file, source_row). Loads are idempotent: each month is replaced inside
a single transaction, so a rerun or a crash half-way never leaves duplicate or partial rows.
"""
import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from . import config as C
from .log import get_logger

log = get_logger()

RAW_TRIP_DDL = """
create table if not exists raw_trips (
    source_month varchar, source_file varchar, source_row bigint,
    VendorID integer, tpep_pickup_datetime timestamp, tpep_dropoff_datetime timestamp,
    passenger_count bigint, trip_distance double, RatecodeID bigint, store_and_fwd_flag varchar,
    PULocationID integer, DOLocationID integer, payment_type bigint,
    fare_amount double, extra double, mta_tax double, tip_amount double, tolls_amount double,
    improvement_surcharge double, total_amount double, congestion_surcharge double,
    airport_fee double, cbd_congestion_fee double
)"""

# Columns that are optional in the source: they are selected if present, else NULL.
# NULL (not 0) on purpose: cbd_congestion_fee did not exist before 2025, "unknown" != "zero".
OPTIONAL_COLUMNS = {"store_and_fwd_flag", "extra", "mta_tax", "improvement_surcharge",
                    "congestion_surcharge", "airport_fee", "cbd_congestion_fee"}
TRIP_COLUMNS = [
    "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count",
    "trip_distance", "RatecodeID", "store_and_fwd_flag", "PULocationID", "DOLocationID",
    "payment_type", "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
    "improvement_surcharge", "total_amount", "congestion_surcharge", "airport_fee",
    "cbd_congestion_fee",
]


def connect(path: Path = C.WAREHOUSE) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(RAW_TRIP_DDL)
    return con


def source_columns(path: Path) -> list[str]:
    return pq.ParquetFile(path).schema_arrow.names


def load_trips(con, month: str, path: Path) -> int:
    present = {c.lower(): c for c in source_columns(path)}
    missing_required = [c for c in C.REQUIRED_TRIP_COLUMNS if c.lower() not in present]
    if missing_required:
        # Schema drift is caught before anything is written.
        raise ValueError(f"{path.name}: required columns missing: {missing_required}")
    select = ", ".join(f'"{present[c.lower()]}"' if c.lower() in present else f"null as {c}"
                       for c in TRIP_COLUMNS)
    absent = sorted(c for c in OPTIONAL_COLUMNS if c.lower() not in present)
    if absent:
        log.info("ingest: %s has no column(s) %s -> loaded as NULL", path.name, absent)

    con.execute("begin transaction")
    try:
        con.execute("delete from raw_trips where source_month = ?", [month])
        con.execute(f"""
            insert into raw_trips
            select ?, ?, row_number() over (), {select}
            from read_parquet(?)""", [month, path.name, str(path)])
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    n = con.execute("select count(*) from raw_trips where source_month = ?", [month]).fetchone()[0]
    log.info("ingest: raw_trips[%s] = %d rows", month, n)
    return n


def load_reference(con, zone_csv: Path, cbd_json: Path, speeds_json: Path) -> None:
    """Small reference tables are rebuilt in full on every run."""
    con.execute("create or replace table raw_zones as select * from read_csv(?, header=true)", [str(zone_csv)])
    cbd = [int(r["taxi_zone"]) for r in json.loads(cbd_json.read_text(encoding="utf-8"))]
    con.execute("create or replace table raw_cbd_zones (taxi_zone integer)")
    con.executemany("insert into raw_cbd_zones values (?)", [[z] for z in cbd])
    con.execute("""create or replace table raw_mta_speeds as
                   select cast(month as date) as month, zone, cast(zonal_speed as double) as zonal_speed
                   from read_json(?)""", [str(speeds_json)])
    log.info("ingest: reference tables loaded (zones, %d CBD zones, MTA speed benchmark)", len(cbd))


def load_weather(con, month: str, weather_json: Path) -> None:
    daily = json.loads(weather_json.read_text(encoding="utf-8"))
    con.execute("""create table if not exists raw_weather
                   (source_month varchar, day date, precip_mm double, snow_cm double, temp_c double)""")
    rows = [[month, d, p, s, t] for d, p, s, t in zip(daily["time"], daily["precipitation_sum"],
                                                      daily["snowfall_sum"], daily["temperature_2m_mean"])]
    con.execute("begin transaction")
    con.execute("delete from raw_weather where source_month = ?", [month])
    con.executemany("insert into raw_weather values (?, ?, ?, ?, ?)", rows)
    con.execute("commit")
