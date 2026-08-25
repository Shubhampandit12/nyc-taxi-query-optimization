# NYC Taxi Query Performance Optimization

A case study in taking a slow analytical query on a multi-million row
MySQL table and making it fast — using B-Tree indexing, a composite
covering index, and RANGE COLUMNS partitioning, with every stage proven
using `EXPLAIN ANALYZE`.

## 1. The Problem

A single MySQL table holding 11.2 million real NYC Yellow Taxi trip
records took **~7.0 seconds on average** (mean of 7 runs; see
Section 4a) to answer a simple analytical question — "which pickup
zones earned the most revenue on trips over 5 miles in January?" —
because it had no indexes at all, so every query forced a full table
scan. This project documents the systematic process of reducing that
to **~1.7 seconds** (a real, measured ~4x improvement) using a
composite covering index, and separately verifies what indexing and
partitioning each actually contribute on their own — including two
results that didn't match the obvious expectation (see Section 4a).
Two more structurally different queries (Section 6b) confirm the
underlying mechanisms generalize, while showing the *size* of the win
ranges from ~4x to ~1900x to essentially nothing, entirely depending
on how selective the query actually is.

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

### 2a. Schema Design

The first version of this project let `pandas.to_sql()` create
`taxi_trips` implicitly from the parquet dtypes. That produced a
schema nobody actually designed: `fare_amount`, `tip_amount`,
`total_amount` etc. as `DOUBLE` (binary floating point — not exact
for currency, and error compounds across `SUM`/`AVG`), small
categorical columns like `passenger_count` and `RatecodeID` as
`DOUBLE` instead of an integer type, `store_and_fwd_flag` as `TEXT`
instead of `CHAR(1)`, and no primary key at all.

`sql_migrations/01_create_table.sql` now defines the table explicitly,
with types picked from the dataset's real value ranges rather than
guessed: `PULocationID`/`DOLocationID` reach 265 (needs `SMALLINT`,
overflows `TINYINT`'s 255 max), `RatecodeID` reaches 99, `fare_amount`
has real negative values and outliers up to ~$863K (still fits
`DECIMAL(10,2)`) — all money columns are `DECIMAL(10,2)`, and a
surrogate `trip_id BIGINT UNSIGNED AUTO_INCREMENT` primary key was
added, which also closes a real gap: without a PK or unique
constraint, a partial ingest re-run could silently duplicate rows.
`04_partitioned_table.sql` matches this schema; its primary key is
composite (`trip_id, tpep_pickup_datetime`) since MySQL requires every
unique key on a partitioned table to include the partitioning column.

**Note on the results below:** the numbers in Section 4 were captured
against the original pandas-inferred schema (the state the data was
actually in when benchmarked), not this corrected one. Column types
don't change which rows match the `WHERE`/`GROUP BY` predicates or
how many rows the covering index has to scan, so the performance
comparison across indexing/partitioning stages still holds — but if
you reproduce this from scratch with the fixed schema, don't be
surprised if absolute numbers shift slightly.

### 2b. Database User

Earlier versions of this project connected as `root` (see
`ingest_taxi_data.py`'s original connection string). Using the
database superuser for an application connection is unnecessary risk
— a bug or injected input in application code shouldn't be able to
touch anything outside `taxi_db`. Both `ingest_taxi_data.py` and
`benchmark_query.py` now read `TAXI_DB_USER` (default `root`, kept
only as a fallback so the scripts still run without extra setup) —
Section 8's reproduction steps create and use a scoped `taxi_app`
user instead, granted privileges on `taxi_db` only.

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

Every number below is a **mean of 7 timed runs after a warm-up run**,
via `python_scripts/benchmark_query.py`, not a single stopwatch
capture. Stddev is included because it's informative on its own —
see Section 4a.

| Stage | Mean (7 runs) | Stddev | vs. baseline | Execution plan |
|---|---|---|---|---|
| 1. No index, no partition (baseline) | 6.99s | 1.15s (16%) | — | Table scan, 11.2M rows |
| 2. Single B-Tree index on `tpep_pickup_datetime` | 6.74s | 0.45s | ~4% faster (noise) | Table scan (index **not** used) |
| 3. Composite covering index | **1.74s** | 0.015s | **4.0x faster** | Covering index range scan |
| 4. Partitioning + composite index | 1.99s | 0.19s | 3.5x faster, but *slower than stage 3* | Covering index range scan, `partitions: p_jan` only |
| 4b. Partitioning alone (no index, isolated) | 2.12s | 0.036s | 3.3x faster | Table scan, `partitions: p_jan` only (3.48M rows) |

Raw `EXPLAIN ANALYZE` output for every stage, including the run-by-run
timings, is in [`explain_outputs/`](explain_outputs/).

### 4a. Two results that didn't match the obvious expectation

**Stage 2 (single index) is a false step.** The optimizer looked at
the index and chose *not* to use it — see the deep dive below. The
0.25s difference from baseline is smaller than baseline's own
run-to-run stddev (1.15s), so it isn't a real effect.

**Stage 4 (partitioning + index) doesn't beat stage 3 (index alone).**
This looks like partitioning "not working," but it isn't — `EXPLAIN
FORMAT=TRADITIONAL` confirms partition pruning is real (only `p_jan`
is ever scanned). The actual explanation is in stage 4b: partitioning
*alone*, with the index removed, gets 2.12s — a genuine ~3.3x win on
its own, nearly matching the composite index's 1.74s. Partitioning
and the composite index are solving the *same* problem for this
query (skip everything outside January via the leading
`tpep_pickup_datetime` predicate), so stacking them buys nothing, and
the extra partition-routing overhead makes stage 4 slightly noisier
(stddev 0.19s vs. stage 3's 0.015s) and marginally slower than stage
3 alone. **The honest takeaway is not "partitioning didn't help" —
it's "partitioning and a covering index are redundant when they
target the same predicate."** Partitioning's independent value would
show on a query that isn't already served by a matching index, or at
a scale where a single partition still exceeds available memory (see
Section 4b below).

### 4b. Methodology and hardware caveats

- **Multi-run, not single-shot:** each stage above is a warm-up run
  followed by 7 timed runs (`time.perf_counter`), reporting
  mean/stddev/min/max — see `python_scripts/benchmark_query.py`. An
  earlier version of this project measured each stage once; the
  baseline alone showed a 16% run-to-run swing, and one earlier
  single-run capture (stage 2) reported a *faster* wall-clock time
  than baseline while its own `EXPLAIN ANALYZE` showed it running
  internally *slower* — two facts that can't both be true for the
  same query. That contradiction is fully explained in
  `explain_outputs/02_single_index_explain.txt`.
- **Buffer pool vs. table size:** `innodb_buffer_pool_size` on the
  machine these numbers were captured on is 128MB; the table's data
  is ~1.8GB. InnoDB itself isn't caching the whole table, but the
  machine has 8GB of RAM, so the OS filesystem cache can still warm
  repeat reads across runs within a benchmark session. These numbers
  reflect a laptop, not a server with a working set that exceeds
  total RAM — partitioning and indexing would both matter more there.
- **Hardware:** these numbers are lower than the 30–90s the original
  project plan estimated, which assumed 15M rows on unspecified
  hardware; this dataset is 11.2M rows on a local SSD.

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
table scan — confirmed across all 7 benchmark runs, not just one. The
reason: the January date range covers about 31% of the entire table
(3.47M of 11.2M rows). Using a secondary index means looking up each
matching row's location in the index, then jumping to the actual
table row to fetch the rest of the columns (a "heap fetch") — and at
31% selectivity, that's ~3.5 million random-access jumps, which costs
more than reading the table sequentially. This is a real and common
outcome: **an index only helps when it's selective enough that using
it beats scanning past it**, and MySQL's cost-based optimizer
correctly reasoned that a plain B-Tree index wasn't worth it at this
selectivity. The measured mean (6.74s vs. baseline's 6.99s) reflects
that: the difference is smaller than the baseline's own run-to-run
noise, i.e. not a real improvement. This is exactly the problem a
covering index solves.

### What is a covering index, and why did it work?

A covering index bundles every column the query needs — the filter
columns (`tpep_pickup_datetime`, `trip_distance`), the group-by column
(`PULocationID`), and the aggregated columns (`fare_amount`,
`tip_amount`) — into a single index structure. Because all of that
data lives in the index itself, MySQL never needs to jump back to the
actual table row (no heap fetch), even when scanning a large fraction
of the table. That eliminated the random-access cost that sank the
single-column index, dropping the mean execution time from 6.74s to
1.74s — the single biggest, and most consistent (stddev 0.015s),
improvement in this project.

### What is partition pruning — and why didn't it help on top of the index?

Partitioning physically splits a table into separate storage segments
based on a column's value — here, one segment per month
(`p_jan`, `p_feb`, `p_mar`, `p_future`). When a query's `WHERE` clause
only touches January dates, MySQL doesn't just skip reading February
and March rows — it never even opens those partitions' files on disk.
`EXPLAIN FORMAT=TRADITIONAL` confirms this directly: the `partitions`
column reads `p_jan` only.

Combined with the composite index (stage 4), this did **not** beat
the plain composite index (stage 3): 1.74s → 1.99s, i.e. slightly
*slower*, not faster. Tested in isolation with the index removed
(stage 4b, `explain_outputs/04b_partition_only_explain.txt`),
partitioning alone gets 2.12s — a real ~3.3x win over the
unpartitioned/unindexed baseline. The reason stacking them doesn't
compound is that **partitioning and the composite index are pruning
the same rows for the same reason**: both use
`tpep_pickup_datetime` to skip everything outside January. Once the
index is already doing that job via a range scan, partition pruning
has nothing left to add, and the partition-routing step adds a small
amount of overhead instead (stage 4's stddev, 0.19s, is over 10x
stage 3's). Partitioning's independent value would show on a query
that isn't already served by a matching index, or once a single
partition's data exceeds what fits in memory — at 11.2M rows on a
laptop, this dataset doesn't reach that point (see Section 4b).

## 6. EXPLAIN ANALYZE Snippets

Captured after warm-up, alongside the multi-run timings in Section 4
(full plans with all node-level timings are in `explain_outputs/`).

**Baseline:**
```
-> Table scan on taxi_trips  (cost=1.21e+6 rows=10.9e+6) (actual time=1.1..5638 rows=11.2e+6 loops=1)
```

**Single index (rejected by optimizer):**
```
-> Table scan on taxi_trips  (cost=1.21e+6 rows=10.9e+6) (actual time=0.198..5682 rows=11.2e+6 loops=1)
```

**Composite covering index:**
```
-> Covering index range scan on taxi_trips using idx_composite
   over ('2025-01-01 00:00:00' <= tpep_pickup_datetime <= '2025-01-31 00:00:00' AND 5 < trip_distance)
   (actual time=0.0167..1424 rows=3.34e+6 loops=1)
```

**Partitioned + composite index:**
```
-> Covering index range scan on taxi_trips_partitioned using idx_composite ...
partitions: p_jan   (p_feb, p_mar, p_future not scanned)
```

**Partitioned, index removed (isolates partition pruning alone):**
```
-> Table scan on taxi_trips_partitioned  (cost=377846 rows=3.42e+6) (actual time=0.0788..1740 rows=3.48e+6 loops=1)
partitions: p_jan   (p_feb, p_mar, p_future not scanned)
```

## 6a. Trade-offs: Write Cost & Storage Overhead

Indexes and partitioning aren't free — they cost write throughput and
disk space. This wasn't measured anywhere in earlier versions of this
project. `python_scripts/measure_write_cost.py` measures it directly:
4 scratch tables (one per stage) are each preloaded with 1,000,000
real rows, then timed on 7 batch inserts of 5,000 new rows each
(warm-up + mean/stddev, same methodology as the read benchmarks).

| Configuration | Mean insert time (5,000 rows) | vs. no index | Index storage (on 1.005M rows) |
|---|---|---|---|
| No index | 0.045s | — | 0 MB |
| Single index (`idx_pickup_time`) | 0.053s | +18.3% slower | 29.6 MB (+16.8% over 175.7MB data) |
| Composite index (`idx_composite`) | 0.056s | +25.2% slower | 78.9 MB (+44.9% over data) |
| Partitioned + composite index | 0.073s | **+61.7% slower** | 78.9 MB (same index size; partitioning doesn't add index overhead) |

**What this means for the earlier results:** the composite index that
made reads ~4x faster (Section 4) also makes writes ~25% slower and
uses 45% more disk than the data itself — a real cost, not a free
lunch. Partitioning stacked on top roughly *doubles* that write
penalty again (+61.7% vs. +25.2%) for a read benefit that Section 4a
showed is redundant with the index it's stacked on. For a
write-heavy system ingesting new trips continuously, the honest
recommendation from this data is: **use the composite index, skip
the partitioning** — it costs more on every write for no proven read
benefit once the index is already in place. Partitioning would earn
its write-cost back on a table large enough that a single partition
still doesn't fit in memory, or on a workload with genuinely
different (non-covered) query patterns per partition — neither is
true for this 11.2M-row dataset.

## 6b. Testing Whether This Generalizes: Two More Query Shapes

Everything above optimizes one query (`revenue_rollup`: a one-month
date range + distance filter, ~31% selectivity). Optimizing one query
four ways is a narrower story than "I can optimize queries" — so
`benchmark_query.py` now supports two more, structurally different
shapes (`--query point_lookup` / `--query wide_aggregation`), run
across all 4 stages the same way. Full run-by-run data is in
[`explain_outputs/multi_query_results.txt`](explain_outputs/multi_query_results.txt).

**`point_lookup`** — a single hour, single pickup zone, no
aggregation (175 matching rows; high selectivity):

| Stage | Mean | vs. baseline |
|---|---|---|
| 1. No index | 7.18s | — |
| 2. Single index | 0.0146s | **~492x faster** |
| 3. Composite index | 0.0038s | **~1889x faster** (best) |
| 4. Partitioned + index | 0.0046s | ~1561x, same as stage 3 (noise) |

**`wide_aggregation`** — no date filter at all, grouped by zone (low
selectivity — touches the full 3-month table):

| Stage | Mean | vs. baseline |
|---|---|---|
| 1. No index | 8.61s (stdev 2.36s — extremely noisy) | — |
| 2. Single index | 5.96s | modest, index not actually used |
| 3. Composite index | 5.70s | modest (covering scan, no heap fetches) |
| 4. Partitioned + index | 4.96s (stdev 0.14s — most consistent) | modest; all 4 partitions still scanned |

**What this shows:** the mechanisms explained in Section 5 hold up
consistently — a B-Tree index only gets used when the optimizer
judges it selective enough, and here the *same* `idx_pickup_time`
index that was rejected for `revenue_rollup` (31% selectivity) is
used and delivers a ~492x win for `point_lookup` (a tiny fraction of
a percent selectivity). But the **magnitude** of every technique's
benefit is entirely selectivity-dependent, not a fixed multiplier:
the composite index goes from "4x" (revenue_rollup) to "1889x"
(point_lookup) to "barely better than a table scan" (wide_aggregation,
where it can't skip any rows, just avoid heap fetches). Partitioning
is the clearest case: `EXPLAIN` confirms it prunes to one partition
for `point_lookup` (redundant with the index, as in Section 5) but
touches **all four partitions** for `wide_aggregation`, since that
query has no date predicate to prune with — partitioning's benefit is
entirely conditional on the query actually filtering the partition
key, not something a table gets "for free" once partitioned.

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

**Verified limitation:** `idx_composite` is built specifically for the
benchmark query's exact filter/group-by shape
(`tpep_pickup_datetime` range + `trip_distance` filter, grouped by
`PULocationID`). Checked with `EXPLAIN FORMAT=TRADITIONAL` against the
real dataset:

| Query | `type` | `key` used | Result |
|---|---|---|---|
| 1. Peak Hour Revenue | `ALL` | none | Full scan, all 4 partitions — `HOUR(...)` isn't sargable |
| 2. Tip by Payment Type | `ALL` | none | Full scan — `payment_type`/`fare_amount`/`tip_amount` aren't the index's leading column |
| 3. Busiest Pickup Zones | `ALL` | none | Full scan — `PULocationID` is in the index but not leading |
| 4. Month-over-Month | `ALL` | none | Full scan — `MONTH(...)` isn't sargable |
| 5. Trip Category | `index` | `idx_composite` | Partial win: full index scan (not a range scan), avoids heap fetches since it's covering, but still touches all 10.9M index entries — no rows skipped |

**4 of the 5 business queries get zero benefit from any optimization
work in this project**, and the 5th gets a partial benefit rather than
the selective win seen in the benchmark query. This confirms the
suspicion: none of these queries filter on `tpep_pickup_datetime`
(the index's and the partitioning's leading/pruning column), so
neither optimization applies to them. A production fix would mean
either a second index matching one of these access patterns (e.g. on
`PULocationID` or `payment_type` alone) or accepting that this
project's optimizations are scoped to one specific query shape, not
a general-purpose speedup for the whole table.

## 8. How to Reproduce

1. Download the NYC TLC Yellow Taxi parquet files for the months you
   want (2025-01 through 2025-03 used here) from the
   [TLC trip record page](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page).
2. Set up MySQL locally and create a database plus a scoped
   application user — don't use `root` for this (see Section 2b):
   ```sql
   CREATE DATABASE taxi_db;
   CREATE USER 'taxi_app'@'localhost' IDENTIFIED BY 'your_password';
   GRANT ALL PRIVILEGES ON taxi_db.* TO 'taxi_app'@'localhost';
   FLUSH PRIVILEGES;
   ```
3. `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
4. `export TAXI_DB_USER=taxi_app TAXI_DB_PASSWORD=your_password`
   (both scripts default `TAXI_DB_USER` to `root` if unset, purely as
   a fallback — `taxi_app` is the intended user)
5. Run `sql_migrations/01_create_table.sql` to create the `taxi_trips`
   table with an explicit, designed schema (see Section 2a above —
   this used to be pandas-inferred, which is why earlier versions of
   this README described running this step *after* ingestion).
6. Run `python python_scripts/ingest_taxi_data.py` to load the parquet
   files into `taxi_trips`. Idempotent per month: re-running it after
   a partial failure skips months that already have rows instead of
   duplicating them.
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
