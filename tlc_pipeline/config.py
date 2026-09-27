"""Central configuration: paths, sources, business constants and thresholds.

Every number that changes a metric lives here so it can be reviewed in one place.
"""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
WAREHOUSE = ROOT / "data" / "warehouse" / "tlc.duckdb"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

# Jan 2024 = before congestion pricing, Jan 2025 = after.
DEFAULT_MONTHS = ["2024-01", "2025-01"]

# ---------------------------------------------------------------- sources
TLC_TRIP_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month}.parquet"
TLC_ZONE_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
SOCRATA_NY = "https://data.ny.gov/resource/{dataset}.json"
MTA_CBD_ZONES_DATASET = "yfdc-w5jh"   # MTA Central Business District Taxi Zones
MTA_CBD_SPEEDS_DATASET = "6p29-6xqn"  # MTA CBD Taxi and FHV Speeds (monthly benchmark)
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
NYC_LAT, NYC_LON = 40.7580, -73.9855  # Times Square, centre of the CBD

HTTP_RETRIES = 4
HTTP_TIMEOUT_S = 120
SOCRATA_PAGE_SIZE = 50  # small on purpose so pagination is exercised and verified

# ------------------------------------------------------ business constants
# Congestion pricing (Congestion Relief Zone toll) went live on this date.
PRICING_START = date(2025, 1, 5)
# Both years are compared on the same calendar span (5th to end of month), so the
# four pre-pricing days at the start of Jan 2025 never count as "after".
WINDOW_FIRST_DAY = 5
# Federal holidays inside the window: traffic on these days is not typical.
HOLIDAYS = {date(2024, 1, 1), date(2024, 1, 15), date(2025, 1, 1), date(2025, 1, 20)}
# MTA peak toll window: weekdays 05:00-21:00. Used for the headline speed KPI.
PEAK_START_HOUR, PEAK_END_HOUR = 5, 21
# A day with >=1 mm precipitation counts as wet. Open-Meteo's precipitation_sum already
# includes snow (as water), so trace snowfall (e.g. 0.07 cm) does not make a day wet.
WET_DAY_PRECIP_MM = 1.0

# Unknown / outside-NYC location ids in the TLC zone lookup.
UNKNOWN_ZONES = (264, 265)
# Vendor codes documented in the TLC yellow trip data dictionary.
KNOWN_VENDORS = (1, 2, 6, 7)

# --------------------------------------------------- row-level thresholds
MIN_DURATION_MIN, MAX_DURATION_MIN = 1.0, 180.0
MAX_DISTANCE_MI = 100.0
MIN_SPEED_MPH, MAX_SPEED_MPH = 1.0, 60.0
MAX_TOTAL_AMOUNT = 1000.0
CRAWL_MPH = 5.0  # "crawl" threshold for the reliability metric

# ---------------------------------------------------- batch-level gates
# A breach of any of these stops the month from being published.
REQUIRED_TRIP_COLUMNS = [
    "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count",
    "trip_distance", "RatecodeID", "PULocationID", "DOLocationID", "payment_type",
    "fare_amount", "tip_amount", "tolls_amount", "total_amount",
]
MAX_OUT_OF_MONTH_SHARE = 0.001      # >0.1% of rows outside the file's month => broken extract
MAX_SPEED_INELIGIBLE_SHARE = 0.15   # >15% of rows unusable for speed => something upstream changed
MIN_DAY_SHARE_OF_MEDIAN = 0.20      # a day with <20% of a median day's trips => partial file
EXPECTED_ZONE_ROWS = 265
EXPECTED_CBD_ZONES = 38
