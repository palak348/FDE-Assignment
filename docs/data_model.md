# Workflow and data model (Class 7)

## Real-world workflow → what the data can see

```mermaid
flowchart LR
    A[Passenger hails taxi<br/>in zone X] -->|meter starts| B((pickup event<br/>tpep_pickup_datetime,<br/>PULocationID))
    B --> C[Trip on the street<br/>may enter / cross the toll zone]
    C -->|meter stops| D((dropoff event<br/>tpep_dropoff_datetime,<br/>DOLocationID, distance))
    D --> E[Fare settled<br/>cbd_congestion_fee]
    P[[Intervention:<br/>congestion pricing<br/>from 2025-01-05]] -.changes.-> C
    P -.adds.-> E
    W[[Context: weather]] -.affects.-> C
    style C stroke-dasharray: 5 5
```

The dashed box is **not observed**: there is no GPS, so speed = distance / (dropoff − pickup) for the whole trip.

## Relational model (DuckDB)

```mermaid
erDiagram
    dim_zone ||--o{ fact_trip : "pickup zone"
    dim_zone ||--o{ fact_trip : "dropoff zone"
    dim_date ||--o{ fact_trip : "pickup date"
    fact_trip ||--|{ trip_event : "pickup + dropoff"
    dim_zone {
        int location_id PK
        string borough
        string zone
        bool in_cbd "from MTA API"
    }
    dim_date {
        date day PK
        bool is_weekday
        bool is_holiday
        bool is_wet "from weather API"
        string period "before / after"
        bool in_window "5th-31st, weekday, not holiday"
    }
    fact_trip {
        string source_month PK
        bigint source_row PK
        timestamp pickup_at
        timestamp dropoff_at
        double trip_distance
        double duration_min
        double speed_mph
        double cbd_congestion_fee
        string trip_scope "cbd_internal / cbd_touch / outside"
        bool dq_rule_x "one flag per rule (13)"
    }
    trip_event {
        string event "pickup / dropoff"
        timestamp event_at
        int location_id
    }
```

| Concept | Where it lives |
|---|---|
| **Entities** | `dim_zone`, `dim_date` |
| **Event / state** | `fact_trip` (a trip moves from pickup to dropoff); `trip_event` is the long form |
| **Intervention** | `dim_date.period` + the `cbd_congestion_fee` actually charged |
| **Outcome** | speed, crawl share, and travel-time-per-mile metrics (`metrics.py`) |
| **Lineage** | `source_month`, `source_row` → `raw_trips` → the Parquet file in `manifest.json` |

Layers: `raw_*` (as delivered) → `stg_trips` (derived columns) → `fact_trip` + flags → `fact_trip_clean` (eligibility) → metrics.
