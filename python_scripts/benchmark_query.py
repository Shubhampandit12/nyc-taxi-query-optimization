"""
Multi-run query benchmark harness.

Replaces single-run wall-clock timing (which this project used to
rely on) with a warm-up run followed by N timed repeats, reporting
mean/stddev/min/max, plus the EXPLAIN ANALYZE plan captured after
warm-up so it reflects a representative, not cold-cache, run.

A single measurement can easily swing 20-30% from buffer pool and OS
disk cache state alone -- see README Section 4 for a real example
where this caused the single-index stage's own EXPLAIN ANALYZE output
to show it running *slower* internally while a single wall-clock
capture reported it as faster.

Three structurally different query shapes are included (--query), not
just the one this project originally optimized, to check whether the
indexing/partitioning strategy actually generalizes:

  revenue_rollup (default) -- medium selectivity (~31% of the table):
      one month's date range + a distance filter, grouped and summed.
      This is the query every earlier stage of this project tuned for.
  point_lookup -- high selectivity: a single hour, single pickup zone,
      no aggregation. Representative of "look up these specific trips."
  wide_aggregation -- low selectivity: no date filter at all (the full
      3-month table), grouped by zone. Representative of a report that
      can't be narrowed by date, and forces every partition to be
      touched regardless of pruning.

Usage:
    export TAXI_DB_PASSWORD=your_password
    python python_scripts/benchmark_query.py --table taxi_trips
    python python_scripts/benchmark_query.py --table taxi_trips_partitioned --query point_lookup
"""

import argparse
import os
import statistics
import time

import pymysql

QUERIES = {
    "revenue_rollup": """
        SELECT
            PULocationID,
            DATE(tpep_pickup_datetime) AS trip_date,
            COUNT(*)                   AS total_trips,
            AVG(tip_amount)            AS avg_tip,
            AVG(trip_distance)         AS avg_distance,
            SUM(fare_amount)           AS total_revenue
        FROM {table}
        WHERE trip_distance > 5
          AND tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31'
        GROUP BY PULocationID, DATE(tpep_pickup_datetime)
        ORDER BY total_revenue DESC
    """.strip(),

    "point_lookup": """
        SELECT tpep_pickup_datetime, DOLocationID, trip_distance,
               fare_amount, tip_amount, total_amount
        FROM {table}
        WHERE tpep_pickup_datetime BETWEEN '2025-01-15 08:00:00' AND '2025-01-15 08:59:59'
          AND PULocationID = 161
        ORDER BY tpep_pickup_datetime
    """.strip(),

    "wide_aggregation": """
        SELECT
            PULocationID,
            COUNT(*)                    AS total_trips,
            ROUND(SUM(fare_amount), 2)  AS total_revenue
        FROM {table}
        WHERE trip_distance > 5
        GROUP BY PULocationID
        ORDER BY total_revenue DESC
    """.strip(),
}


def connect():
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user=os.environ.get("TAXI_DB_USER", "root"),
        password=os.environ["TAXI_DB_PASSWORD"],
        database="taxi_db",
    )


def run_once(conn, sql):
    with conn.cursor() as cur:
        start = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchall()
        elapsed = time.perf_counter() - start
    return elapsed, len(rows)


def benchmark(table, runs, query_name="revenue_rollup"):
    query = QUERIES[query_name].format(table=table)
    conn = connect()
    try:
        print(f"Warm-up run against {table} ({query_name})...")
        run_once(conn, query)

        times = []
        for i in range(runs):
            elapsed, row_count = run_once(conn, query)
            times.append(elapsed)
            print(f"  run {i + 1}/{runs}: {elapsed:.4f}s ({row_count} rows)")

        mean = statistics.mean(times)
        stdev = statistics.stdev(times) if len(times) > 1 else 0.0
        print(f"\nmean={mean:.4f}s  stddev={stdev:.4f}s  "
              f"min={min(times):.4f}s  max={max(times):.4f}s  (n={runs})")

        with conn.cursor() as cur:
            cur.execute("EXPLAIN ANALYZE " + query)
            plan = "\n".join(row[0] for row in cur.fetchall())
        print(f"\n--- EXPLAIN ANALYZE ({table}) ---\n{plan}")

        if table.endswith("_partitioned"):
            with conn.cursor() as cur:
                cur.execute("EXPLAIN FORMAT=TRADITIONAL " + query)
                cols = [d[0] for d in cur.description]
                explain_row = dict(zip(cols, cur.fetchone()))
            print(f"\n--- Partition pruning proof (EXPLAIN FORMAT=TRADITIONAL) ---")
            print(f"partitions: {explain_row['partitions']}")

        return {"table": table, "query": query_name, "mean": mean, "stdev": stdev,
                 "times": times, "plan": plan}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True,
                         help="Table to benchmark, e.g. taxi_trips or taxi_trips_partitioned")
    parser.add_argument("--query", choices=list(QUERIES), default="revenue_rollup",
                         help="Which query shape to run (default: revenue_rollup)")
    parser.add_argument("--runs", type=int, default=7,
                         help="Number of timed runs after the warm-up (default: 7)")
    args = parser.parse_args()

    benchmark(args.table, args.runs, args.query)
