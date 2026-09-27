"""Offline tests on a tiny synthetic month: rules flag (never delete), reruns are idempotent,
gates catch partial files and schema drift."""
from datetime import datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tlc_pipeline import ingest, model, validate

MONTH = "2025-01"
CBD_ZONE, OUTSIDE_ZONE = 161, 138  # Midtown Center, LaGuardia


def trip(pickup, minutes=10, miles=2.0, pu=CBD_ZONE, do=CBD_ZONE, total=15.0, fee=0.75, vendor=2):
    return {"VendorID": vendor, "tpep_pickup_datetime": pickup,
            "tpep_dropoff_datetime": pickup + timedelta(minutes=minutes),
            "passenger_count": 1, "trip_distance": miles, "RatecodeID": 1, "PULocationID": pu,
            "DOLocationID": do, "payment_type": 1, "fare_amount": total, "tip_amount": 0.0,
            "tolls_amount": 0.0, "total_amount": total, "cbd_congestion_fee": fee}


def good_month(days=31, per_day=10):
    return [trip(datetime(2025, 1, d, 9) + timedelta(minutes=5 * i), pu=CBD_ZONE if i % 2 else OUTSIDE_ZONE)
            for d in range(1, days + 1) for i in range(per_day)]


BAD = {
    "out_of_month": trip(datetime(2002, 12, 31, 23)),
    "reversal_record": trip(datetime(2025, 1, 10, 9), total=-15.0),
    "unknown_zone": trip(datetime(2025, 1, 10, 9), do=264),
    "bad_duration": trip(datetime(2025, 1, 10, 9), minutes=0),
    "zero_distance": trip(datetime(2025, 1, 10, 9), miles=0.0),
    "implausible_speed": trip(datetime(2025, 1, 10, 9), minutes=2, miles=10.0),
    "fee_missing_in_cbd": trip(datetime(2025, 1, 10, 9), fee=0.0),
    "fee_before_pricing": trip(datetime(2025, 1, 2, 9)),
}


def write_parquet(path, rows, drop=()):
    table = pa.Table.from_pylist(rows)
    pq.write_table(table.drop_columns(list(drop)), path)
    return path


@pytest.fixture
def con(tmp_path):
    c = ingest.connect(tmp_path / "test.duckdb")
    c.execute("create table raw_zones as select * from (values "
              f"({CBD_ZONE}, 'Manhattan', 'Midtown Center', 'Yellow Zone'), "
              f"({OUTSIDE_ZONE}, 'Queens', 'LaGuardia Airport', 'Airports'), "
              "(264, 'Unknown', 'N/A', 'N/A')) t(LocationID, Borough, Zone, service_zone)")
    c.execute(f"create table raw_cbd_zones as select {CBD_ZONE}::integer as taxi_zone")
    c.execute("create table raw_weather as select '2025-01' as source_month, d::date as day, "
              "0.0 as precip_mm, 0.0 as snow_cm, 0.0 as temp_c "
              "from range(date '2025-01-01', date '2025-02-01', interval 1 day) t(d)")
    model.build_dimensions(c)
    yield c
    c.close()


def load(con, path):
    ingest.load_trips(con, MONTH, path)
    model.build_fact(con, MONTH)
    validate.record_rule_results(con, MONTH)
    return validate.batch_gates(con, MONTH, pq.ParquetFile(path).metadata.num_rows)


def test_each_bad_row_is_flagged_by_its_rule_and_kept(con, tmp_path):
    rows = good_month() + list(BAD.values())
    load(con, write_parquet(tmp_path / "m.parquet", rows))

    assert con.execute("select count(*) from fact_trip").fetchone()[0] == len(rows)  # nothing deleted
    flagged = dict(con.execute("select rule, rows_flagged from dq_results").fetchall())
    for rule in BAD:
        assert flagged[rule] >= 1, rule
    valid = con.execute("select count(*) from fact_trip_clean where is_valid_trip").fetchone()[0]
    assert valid == len(rows) - 3  # out_of_month, reversal, unknown_zone are exclude_all


def test_rerun_is_idempotent(con, tmp_path):
    path = write_parquet(tmp_path / "m.parquet", good_month())
    load(con, path)
    first = con.execute("select count(*), sum(speed_mph) from fact_trip").fetchone()
    load(con, path)
    assert con.execute("select count(*), sum(speed_mph) from fact_trip").fetchone() == first
    assert con.execute("select count(*) from raw_trips").fetchone()[0] == first[0]


def test_good_month_passes_all_gates(con, tmp_path):
    gates = load(con, write_parquet(tmp_path / "m.parquet", good_month()))
    assert all(g["passed"] for g in gates), gates


def test_partial_file_fails_daily_coverage_gate(con, tmp_path):
    gates = load(con, write_parquet(tmp_path / "m.parquet", good_month(days=20)))
    assert not {g["gate"]: g["passed"] for g in gates}["every_day_present"]


def test_too_many_unusable_rows_fails_gate(con, tmp_path):
    rows = good_month() + [trip(datetime(2025, 1, 10, 9), miles=0.0)] * 100
    gates = load(con, write_parquet(tmp_path / "m.parquet", rows))
    assert not {g["gate"]: g["passed"] for g in gates}["speed_ineligible_share"]


def test_missing_required_column_is_rejected_before_writing(con, tmp_path):
    path = write_parquet(tmp_path / "m.parquet", good_month(), drop=["trip_distance"])
    with pytest.raises(ValueError, match="trip_distance"):
        ingest.load_trips(con, MONTH, path)
    assert con.execute("select count(*) from raw_trips").fetchone()[0] == 0


def test_absent_fee_column_is_null_not_zero(con, tmp_path):
    ingest.load_trips(con, MONTH, write_parquet(tmp_path / "m.parquet", good_month(), drop=["cbd_congestion_fee"]))
    assert con.execute("select count(cbd_congestion_fee) from raw_trips").fetchone()[0] == 0
