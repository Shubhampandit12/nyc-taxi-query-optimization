# NYC Taxi Query Performance Optimization

A case study in taking a slow analytical query on a multi-million row
MySQL table and making it fast — using B-Tree indexing, a composite
covering index, and RANGE COLUMNS partitioning, with every stage proven
using `EXPLAIN ANALYZE`.

## 1. The Problem

A single MySQL table holding 11.2 million real NYC Yellow Taxi trip
records took **7.94 seconds** to answer a simple analytical question —
"which pickup zones earned the most revenue on trips over 5 miles in
January?" — because it had no indexes at all, so every query forced a
full table scan. This project documents the systematic process of
reducing that to **1.87–2.09 seconds** (a ~4x improvement), using
indexing and partitioning strategies, and explains *why* each stage
helped (or, in one case, didn't help as expected).

## 2. Dataset

[NYC TLC Yellow Taxi Trip Records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
for January, February, and March 2025, loaded from the official
`.parquet` files into a MySQL table called `taxi_trips`.

| | |
|---|---|
| Rows | 11,198,026 |
| Columns | 20 |
| Date range | 2025-01-01 to 2025-03-31 (see data quality note below) |
| Jan trips | 3,475,235 |
| Feb trips | 3,577,542 |
| Mar trips | 4,145,225 |

**Data quality note:** 22 rows carry a December 2007 pickup timestamp
and 2 rows carry an April 2025 timestamp — known artifacts in the raw
TLC data (a handful of mis-stamped trips ship with every monthly
file). They fall outside the `p_jan`/`p_feb`/`p_mar` partitions and
land in the catch-all `p_future` partition; they don't affect the
benchmark query, which filters to January only.

## 3. The Analytical Query

```sql
SELECT
    PULocationID,
    DATE(tpep_pickup_datetime) AS trip_date,
    COUNT(*) AS total_trips,
    AVG(tip_amount) AS avg_tip,
    AVG(trip_distance) AS avg_distance,
    SUM(fare_amount) AS total_revenue
FROM taxi_trips
WHERE trip_distance > 5
  AND tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31'
GROUP BY PULocationID, DATE(tpep_pickup_datetime)
ORDER BY total_revenue DESC;
```

**Business question:** for trips longer than 5 miles in January, which
pickup zone/day combinations generated the most fare revenue? This is
the kind of daily rollup a data analyst or ops team would run
regularly — filter a date range, filter a distance threshold, group by
zone and day, sum revenue.

## 4. Results Table

| Stage | Wall-clock time | Execution plan | Key observation |
|---|---|---|---|
| No index (baseline) | 7.94s | `Table scan on taxi_trips` | Full scan of all 11.2M rows |
| Single B-Tree index on `tpep_pickup_datetime` | 6.77s | `Table scan on taxi_trips` (index **not** used) | Optimizer rejected the index — see explanation below |
| Composite covering index | 1.87s | `Covering index range scan using idx_composite` | ~4.2x faster than baseline; no heap fetches |
| RANGE COLUMNS partitioning + composite index | 2.09s | `Covering index range scan` + `partitions: p_jan` only | Feb/Mar/future partitions never opened |

Raw `EXPLAIN ANALYZE` output for each stage is in
[`explain_outputs/`](explain_outputs/).

*Note: these numbers are lower than the 30–90s the original project
plan estimated — that estimate assumed 15M rows on unspecified
hardware; this dataset is 11.2M rows on a fast local SSD. The
mechanism proven at each stage (table scan → index scan → covering
index → partition pruning) is what matters, not the absolute seconds.*

## 5. Deep Dive Sections

### What is a table scan, and why is it slow?

A table scan means MySQL has no way to jump to the rows it needs, so
it reads every single row from the first to the last, checking each
one against the `WHERE` clause. With 11.2 million rows, that's 11.2
million comparisons even though only ~500K rows (the January,
>5-mile trips) actually matter. It's the database equivalent of
reading an entire book cover to cover to find every mention of one
word, instead of using the index at the back.

### How do B-Tree indexes work — and why didn't the single-column index get used here?

A B-Tree index on `tpep_pickup_datetime` sorts pointers to rows by
date in a tree structure, so MySQL can binary-search to "January 1st"
instead of scanning from row one. In principle this should skip all
non-January rows.

In practice, the optimizer **declined to use it** here (see
`explain_outputs/02_single_index_explain.txt`) and fell back to a
table scan. The reason: the January date range covers about 31% of
the entire table (3.47M of 11.2M rows). Using a secondary index means
looking up each matching row's location in the index, then jumping
to the actual table row to fetch the rest of the columns (a "heap
fetch") — and at 31% selectivity, that's ~3.5 million random-access
jumps, which costs more than reading the table sequentially. This is
a real and common outcome: **an index only helps when it's selective
enough that using it beats scanning past it**, and MySQL's cost-based
optimizer correctly reasoned that a plain B-Tree index wasn't worth
it at this selectivity. This is exactly the problem a covering index
solves.

### What is a covering index, and why did it work?

A covering index bundles every column the query needs — the filter
columns (`tpep_pickup_datetime`, `trip_distance`), the group-by column
(`PULocationID`), and the aggregated columns (`fare_amount`,
`tip_amount`) — into a single index structure. Because all of that
data lives in the index itself, MySQL never needs to jump back to the
actual table row (no heap fetch), even when scanning a large fraction
of the table. That eliminated the random-access cost that sank the
single-column index, dropping execution time from 6.77s to 1.87s —
the single biggest improvement in this project.

### What is partition pruning?

Partitioning physically splits a table into separate storage segments
based on a column's value — here, one segment per month
(`p_jan`, `p_feb`, `p_mar`, `p_future`). When a query's `WHERE` clause
only touches January dates, MySQL doesn't just skip reading February
and March rows — it never even opens those partitions' files on disk.
`EXPLAIN FORMAT=TRADITIONAL` confirms this directly: the `partitions`
column reads `p_jan` only. Combined with the composite index, this
kept execution time essentially the same as the plain composite index
(1.87s → 2.09s here, since the January partition itself still holds
~3.5M matching rows) but would matter far more at larger scale, or for
queries spanning a single day instead of a full month, or as more
months of data are added — new partitions won't slow down queries
that don't touch them.

## 6. EXPLAIN ANALYZE Snippets

**Baseline:**
```
-> Table scan on taxi_trips  (cost=1.21e+6 rows=10.9e+6) (actual time=0.439..5546 rows=11.2e+6 loops=1)
```

**Single index (rejected by optimizer):**
```
-> Table scan on taxi_trips  (cost=1.21e+6 rows=10.9e+6) (actual time=0.415..5840 rows=11.2e+6 loops=1)
```

**Composite covering index:**
```
-> Covering index range scan on taxi_trips using idx_composite
   over ('2025-01-01 00:00:00' <= tpep_pickup_datetime <= '2025-01-31 00:00:00' AND 5 < trip_distance)
   (actual time=0.0158..1407 rows=3.34e+6 loops=1)
```

**Partitioned + composite index:**
```
-> Covering index range scan on taxi_trips_partitioned using idx_composite ...
partitions: p_jan   (p_feb, p_mar, p_future not scanned)
```

## 7. Business Analytics Queries

Run against the final `taxi_trips_partitioned` table
([full queries](sql_queries/business_queries.sql)). Sample results:

**Peak hour by revenue (top 5):** 6pm generated the most revenue
($21.0M), followed by 5pm ($20.5M) and 4pm ($18.7M) — the evening
rush.

**Tip behavior by payment type:** card payments (`payment_type = 1`)
average a 26.7% tip; cash payments (`payment_type = 2`) show 0% —
cash tips aren't captured in this dataset, a known TLC data
limitation.

**Busiest pickup zone:** `PULocationID 161` (Midtown Center) with
513,560 pickups, average fare $23.67.

**Month-over-month:** revenue grew from $89.0M (Jan) to $89.5M (Feb)
to $108.9M (Mar).

**Trip length mix:** short trips (<2 miles) are the most common
(6.34M trips, 57% of the total) but long trips (>10 miles) earn the
most per trip ($59.07 avg fare vs $10.05 for short trips).

## 8. How to Reproduce

1. Download the NYC TLC Yellow Taxi parquet files for the months you
   want (2025-01 through 2025-03 used here) from the
   [TLC trip record page](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page).
2. Set up MySQL locally and create a database: `CREATE DATABASE taxi_db;`
3. `python -m venv venv && source venv/bin/activate && pip install pandas sqlalchemy pymysql pyarrow`
4. `export TAXI_DB_PASSWORD=your_mysql_password`
5. Run `python python_scripts/ingest_taxi_data.py` to load the parquet
   files into `taxi_trips`.
6. Run `sql_migrations/01_create_table.sql` to inspect the schema.
7. Run `sql_queries/baseline_query.sql` and save the `EXPLAIN ANALYZE`
   output — this is your baseline.
8. Run `sql_migrations/02_single_index.sql`, then re-run the same
   query and compare.
9. Run `sql_migrations/03_composite_index.sql`, then re-run again.
10. Run `sql_migrations/04_partitioned_table.sql` to create the
    partitioned table, migrate the data, and add the composite index.
11. Re-run the benchmark query against `taxi_trips_partitioned` and
    confirm partition pruning with
    `EXPLAIN FORMAT=TRADITIONAL <query>` (check the `partitions` column).
12. Run `sql_queries/business_queries.sql` for the analytics queries.
