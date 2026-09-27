"""Stage 1 - retrieval.

Retrieval modes used:
  * files : TLC trip records (Parquet) and the taxi zone lookup (CSV) over HTTP
  * API   : MTA datasets via the Socrata SoQL JSON API (paginated) and Open-Meteo REST JSON
  * SQL   : raw files are loaded into the DuckDB warehouse with SQL (see ingest.py)

Every retrieval proves its own completeness (bytes / records against what the source
declares) and is recorded in data/raw/manifest.json with a checksum. Raw files are never
edited; reruns reuse them unless a refresh is requested.
"""
import calendar
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import requests

from . import config as C
from .log import get_logger

log = get_logger()
MANIFEST = C.RAW_DIR / "manifest.json"


class RetrievalError(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path) -> str:
    return path.relative_to(C.ROOT).as_posix()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _record(path: Path, entry: dict) -> None:
    manifest = load_manifest()
    manifest[_rel(path)] = {**entry, "retrieved_at": _now(), "sha256": _sha256(path)}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP with exponential backoff. Retries network errors, 5xx and 429; fails fast on other 4xx."""
    for attempt in range(1, C.HTTP_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=C.HTTP_TIMEOUT_S, **kwargs)
        except requests.RequestException as e:
            reason = repr(e)
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code < 500 and resp.status_code != 429:
                raise RetrievalError(f"{method} {url} -> HTTP {resp.status_code} (not retryable)")
            reason = f"HTTP {resp.status_code}"
        if attempt == C.HTTP_RETRIES:
            raise RetrievalError(f"{method} {url} failed after {attempt} attempts: {reason}")
        wait = 2 ** attempt
        log.warning("retrieve: %s (attempt %d/%d), retrying in %ds", reason, attempt, C.HTTP_RETRIES, wait)
        time.sleep(wait)


def _is_cached(path: Path, refresh: bool) -> bool:
    entry = load_manifest().get(_rel(path))
    if refresh or not path.exists() or not entry:
        return False
    if _sha256(path) != entry["sha256"]:
        log.warning("retrieve: %s does not match its manifest checksum; re-downloading", path.name)
        return False
    return True


def _write_json(dest: Path, payload) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".json.part")
    part.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    part.replace(dest)


# ------------------------------------------------------------------ files
def download_file(url: str, dest: Path, refresh: bool = False) -> Path:
    """Download atomically (.part then rename) and verify bytes against Content-Length."""
    if _is_cached(dest, refresh):
        log.info("retrieve: %s already retrieved, checksum verified, reusing raw copy", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    declared = int(_request("HEAD", url).headers.get("Content-Length", -1))
    part = dest.with_suffix(dest.suffix + ".part")
    with _request("GET", url, stream=True) as resp, open(part, "wb") as f:
        for chunk in resp.iter_content(1 << 20):
            f.write(chunk)
    got = part.stat().st_size
    if declared >= 0 and got != declared:
        part.unlink()
        raise RetrievalError(f"{url}: received {got} bytes, server declared {declared}")
    part.replace(dest)

    entry = {"source_url": url, "bytes": got, "declared_bytes": declared}
    if dest.suffix == ".parquet":
        entry["rows_in_parquet_metadata"] = pq.ParquetFile(dest).metadata.num_rows
    _record(dest, entry)
    log.info("retrieve: downloaded %s (%.1f MB, byte count matches server)", dest.name, got / 1e6)
    return dest


def trip_file(month: str, refresh: bool = False) -> Path:
    dest = C.RAW_DIR / "tlc" / f"yellow_tripdata_{month}.parquet"
    return download_file(C.TLC_TRIP_URL.format(month=month), dest, refresh)


def zone_file(refresh: bool = False) -> Path:
    return download_file(C.TLC_ZONE_URL, C.RAW_DIR / "tlc" / "taxi_zone_lookup.csv", refresh)


# ------------------------------------------------------------------ APIs
def socrata_dataset(dataset: str, dest: Path, select: str | None = None,
                    order: str = ":id", refresh: bool = False) -> Path:
    """Page through a Socrata dataset and prove every record arrived.

    Completeness: ask the API for count(*) first, page with a stable $order, then require
    fetched == count with no duplicate records.
    """
    if _is_cached(dest, refresh):
        log.info("retrieve: %s snapshot already retrieved, reusing (--refresh-api to re-pull)", dest.name)
        return dest
    url = C.SOCRATA_NY.format(dataset=dataset)
    expected = int(_request("GET", url, params={"$select": "count(*)"}).json()[0]["count"])

    records, pages = [], 0
    while True:
        params = {"$limit": C.SOCRATA_PAGE_SIZE, "$offset": pages * C.SOCRATA_PAGE_SIZE, "$order": order}
        if select:
            params["$select"] = select
        page = _request("GET", url, params=params).json()
        records.extend(page)
        pages += 1
        if len(page) < C.SOCRATA_PAGE_SIZE:
            break

    distinct = len({json.dumps(r, sort_keys=True) for r in records})
    if len(records) != expected or distinct != len(records):
        raise RetrievalError(f"{dataset}: fetched {len(records)} records ({distinct} distinct), "
                             f"API count(*) says {expected}")
    _write_json(dest, records)
    _record(dest, {"source_url": url, "dataset": dataset, "select": select,
                   "records": len(records), "api_count": expected, "pages": pages})
    log.info("retrieve: %s -> %d records in %d pages (matches API count)", dataset, len(records), pages)
    return dest


def cbd_zones(refresh: bool = False) -> Path:
    return socrata_dataset(C.MTA_CBD_ZONES_DATASET, C.RAW_DIR / "api" / "mta_cbd_taxi_zones.json",
                           select="taxi_zone", order="taxi_zone", refresh=refresh)


def cbd_speed_benchmark(refresh: bool = False) -> Path:
    return socrata_dataset(C.MTA_CBD_SPEEDS_DATASET, C.RAW_DIR / "api" / "mta_cbd_speeds.json",
                           order="month,zone", refresh=refresh)


def weather(month: str, refresh: bool = False) -> Path:
    """Daily NYC weather for one month. Completeness: one non-null record per calendar day."""
    dest = C.RAW_DIR / "api" / f"open_meteo_{month}.json"
    if _is_cached(dest, refresh):
        log.info("retrieve: %s already retrieved, reusing", dest.name)
        return dest
    y, m = map(int, month.split("-"))
    days = calendar.monthrange(y, m)[1]
    params = {"latitude": C.NYC_LAT, "longitude": C.NYC_LON,
              "start_date": f"{month}-01", "end_date": f"{month}-{days:02d}",
              "daily": "precipitation_sum,snowfall_sum,temperature_2m_mean",
              "timezone": "America/New_York"}
    daily = _request("GET", C.OPEN_METEO_URL, params=params).json()["daily"]
    n = len(daily["time"])
    if n != days or None in daily["precipitation_sum"] or None in daily["snowfall_sum"]:
        raise RetrievalError(f"weather {month}: {n} days returned (expected {days}) or null values")
    _write_json(dest, daily)
    _record(dest, {"source_url": C.OPEN_METEO_URL, "params": params, "records": n})
    log.info("retrieve: weather %s -> %d days (one per calendar day)", month, n)
    return dest
