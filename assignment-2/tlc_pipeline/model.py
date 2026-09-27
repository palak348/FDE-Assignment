"""Stage 4 - model the workflow.

  Entities      dim_zone (location, borough, in_cbd), dim_date (weekday/holiday/weather/period)
  Event         fact_trip: one row per metered trip = pickup event -> dropoff event,
                with derived duration, speed, scope (inside / touching / outside the CBD)
                and one dq_* flag per validation rule
  Intervention  congestion pricing: dim_date.period ('before' / 'after' PRICING_START) and the
                per-trip cbd_congestion_fee actually charged
  Outcome       speed and reliability metrics built on top (metrics.py)

trip_event is a long view (one row per pickup and per dropoff) for zone/hour activity questions.
"""
from . import config as C
from . import validate as V
from .log import get_logger

log = get_logger()


def build_dimensions(con) -> None:
    con.execute("""
        create or replace table dim_zone as
        select z.LocationID as location_id, z.Borough as borough, z.Zone as zone,
               z.service_zone, c.taxi_zone is not null as in_cbd
        from raw_zones z left join raw_cbd_zones c on c.taxi_zone = z.LocationID""")

    holidays = ", ".join(f"date '{d}'" for d in sorted(C.HOLIDAYS))
    con.execute(f"""
        create or replace table dim_date as
        select day, source_month,
               isodow(day) <= 5 as is_weekday,
               day in ({holidays}) as is_holiday,
               precip_mm, snow_cm, temp_c,
               precip_mm >= {C.WET_DAY_PRECIP_MM} as is_wet,
               case when day >= date '{C.PRICING_START}' then 'after' else 'before' end as period,
               day(day) >= {C.WINDOW_FIRST_DAY} and isodow(day) <= 5 and day not in ({holidays})
                   as in_window
        from raw_weather""")
    log.info("model: dim_zone and dim_date rebuilt")


STG_TRIPS = """
create or replace view stg_trips as
select t.*,
       strftime(tpep_pickup_datetime, '%Y-%m') as pickup_month,
       epoch(tpep_dropoff_datetime - tpep_pickup_datetime) / 60.0 as duration_min,
       trip_distance / nullif(epoch(tpep_dropoff_datetime - tpep_pickup_datetime) / 3600.0, 0) as speed_mph,
       row_number() over (partition by source_month, VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
                          PULocationID, DOLocationID, trip_distance, total_amount
                          order by source_row) as dup_rank
from raw_trips t
"""


def build_fact(con, month: str) -> None:
    """Rebuild fact_trip for one month (delete + insert in one transaction = rerun-safe)."""
    con.execute(STG_TRIPS)
    select = f"""
        select s.source_month, s.source_row, s.VendorID as vendor_id,
               s.tpep_pickup_datetime as pickup_at, s.tpep_dropoff_datetime as dropoff_at,
               cast(s.tpep_pickup_datetime as date) as pickup_date,
               hour(s.tpep_pickup_datetime) as pickup_hour,
               s.PULocationID as pu_location_id, s.DOLocationID as do_location_id,
               s.trip_distance, s.duration_min, s.speed_mph,
               s.passenger_count, s.payment_type, s.total_amount, s.cbd_congestion_fee,
               case when pu_cbd and do_cbd then 'cbd_internal'
                    when pu_cbd or do_cbd then 'cbd_touch' else 'outside' end as trip_scope,
               {V.flag_columns_sql()}
        from (select s.*, pu.location_id is not null as pu_known, do_.location_id is not null as do_known,
                     coalesce(pu.in_cbd, false) as pu_cbd, coalesce(do_.in_cbd, false) as do_cbd
              from stg_trips s
              left join dim_zone pu on pu.location_id = s.PULocationID
              left join dim_zone do_ on do_.location_id = s.DOLocationID
              where s.source_month = '{month}') s"""

    con.execute(f"create table if not exists fact_trip as {select} limit 0")
    con.execute("begin transaction")
    try:
        con.execute("delete from fact_trip where source_month = ?", [month])
        con.execute(f"insert into fact_trip {select}")
        con.execute(f"""
            create or replace view fact_trip_clean as
            select *, not ({V.any_of('exclude_all')}) as is_valid_trip,
                   not ({V.any_of('exclude_all')} or {V.any_of('exclude_speed')}) as speed_eligible
            from fact_trip""")
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise

    con.execute("""
        create or replace view trip_event as
        select source_month, source_row, 'pickup' as event, pickup_at as event_at, pu_location_id as location_id
        from fact_trip_clean where is_valid_trip
        union all
        select source_month, source_row, 'dropoff', dropoff_at, do_location_id
        from fact_trip_clean where is_valid_trip""")
    n = con.execute("select count(*) from fact_trip where source_month = ?", [month]).fetchone()[0]
    log.info("model: fact_trip[%s] = %d rows with %d dq flags", month, n, len(V.ROW_RULES))
