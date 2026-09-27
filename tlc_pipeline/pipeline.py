"""Run the whole pipeline: retrieve -> ingest -> validate -> model -> metrics -> outputs.

    python -m tlc_pipeline.pipeline                      # default months (2024-01, 2025-01)
    python -m tlc_pipeline.pipeline --months 2024-01 2025-01 --refresh-api

Rerun behaviour
  * raw files are reused when their checksum matches the manifest (no re-download)
  * every warehouse write for a month is delete+insert in one transaction -> same result on rerun
  * outputs are written to *.tmp and renamed, so a crash never leaves half-written files

Failure handling
  * a month that fails retrieval, ingest or any batch gate is marked failed/quarantined and
    skipped; other months still run
  * published outputs are only replaced when both a 'before' and an 'after' month are published;
    otherwise the last good outputs are left untouched
  * exit code: 0 = all months published, 1 = some month failed, 2 = reference data failed
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone

import pyarrow.parquet as pq

from . import config as C
from . import ingest, metrics, model, report, retrieve, validate
from .log import get_logger

log = get_logger()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _init_ops_tables(con):
    con.execute("""create table if not exists run_log (run_id varchar, started_at timestamp,
                   finished_at timestamp, months varchar, status varchar, message varchar)""")
    con.execute("""create table if not exists month_status (source_month varchar, run_id varchar,
                   status varchar, detail varchar, updated_at timestamp)""")
    con.execute("""create table if not exists gate_results (run_id varchar, source_month varchar,
                   gate varchar, passed boolean, detail varchar)""")


def _set_status(con, run_id, month, status, detail=""):
    con.execute("delete from month_status where source_month = ?", [month])
    con.execute("insert into month_status values (?, ?, ?, ?, ?)", [month, run_id, status, detail, _now()])
    (log.info if status == "published" else log.error)("status %s -> %s %s", month, status, detail)


def _save_gates(con, run_id, gates):
    con.executemany("insert into gate_results values (?, ?, ?, ?, ?)",
                    [[run_id, g["source_month"], g["gate"], g["passed"], g["detail"]] for g in gates])


def run(months: list[str], refresh: bool = False, refresh_api: bool = False) -> int:
    run_id = uuid.uuid4().hex[:8]
    started = _now()
    log.info("=== run %s started for months %s", run_id, months)
    con = ingest.connect()
    _init_ops_tables(con)

    def finish(status, message, code):
        con.execute("insert into run_log values (?, ?, ?, ?, ?, ?)",
                    [run_id, started, _now(), ",".join(months), status, message])
        log.info("=== run %s finished: %s (%s)", run_id, status, message)
        con.close()
        return code

    # ---- reference data: without it no month can be placed on the map -> stop.
    try:
        ingest.load_reference(con, retrieve.zone_file(refresh), retrieve.cbd_zones(refresh_api),
                              retrieve.cbd_speed_benchmark(refresh_api))
        ref_gates = validate.reference_gates(con)
        _save_gates(con, run_id, ref_gates)
        if not all(g["passed"] for g in ref_gates):
            return finish("failed", "reference gates failed", 2)
    except Exception as e:
        log.exception("reference data failed")
        return finish("failed", f"reference data: {e}", 2)

    # ---- retrieve + ingest each month independently.
    loaded = []
    for m in months:
        try:
            path = retrieve.trip_file(m, refresh)
            ingest.load_trips(con, m, path)
            ingest.load_weather(con, m, retrieve.weather(m, refresh_api))
            loaded.append((m, path))
        except Exception as e:
            log.exception("month %s failed during retrieval/ingest", m)
            _set_status(con, run_id, m, "failed", str(e)[:500])

    # ---- model + validate each loaded month.
    published = []
    if loaded:
        model.build_dimensions(con)
    for m, path in loaded:
        try:
            model.build_fact(con, m)
            validate.record_rule_results(con, m)
            gates = validate.batch_gates(con, m, pq.ParquetFile(path).metadata.num_rows)
            _save_gates(con, run_id, gates)
            failed = [g["gate"] for g in gates if not g["passed"]]
            if failed:
                _set_status(con, run_id, m, "quarantined", "failed gates: " + ", ".join(failed))
            else:
                _set_status(con, run_id, m, "published")
                published.append(m)
        except Exception as e:
            log.exception("month %s failed during modelling/validation", m)
            _set_status(con, run_id, m, "failed", str(e)[:500])

    # ---- metrics: need at least one published month on each side of the intervention.
    periods = {("after" if m >= C.PRICING_START.strftime("%Y-%m") else "before") for m in published}
    if periods != {"before", "after"}:
        return finish("failed", f"published months {published} do not cover before+after; "
                                "previous outputs left unchanged", 1)
    rows, context = metrics.compute(con, published)
    table = metrics.before_after(rows)
    report.write_outputs(con, run_id, published, table, rows, context,
                         metrics.hourly_cbd_speed(con, published))

    all_ok = len(published) == len(months)
    return finish("success" if all_ok else "partial",
                  f"published {published}" + ("" if all_ok else f", not published {sorted(set(months) - set(published))}"),
                  0 if all_ok else 1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--months", nargs="+", default=C.DEFAULT_MONTHS, help="YYYY-MM months to process")
    p.add_argument("--refresh", action="store_true", help="re-download trip and zone files")
    p.add_argument("--refresh-api", action="store_true", help="re-pull API snapshots (MTA, weather)")
    a = p.parse_args(argv)
    return run(a.months, a.refresh, a.refresh_api)


if __name__ == "__main__":
    sys.exit(main())
