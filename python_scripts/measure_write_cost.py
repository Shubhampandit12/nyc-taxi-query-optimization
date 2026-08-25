"""
Measures the write-side cost of the indexing/partitioning strategies
used in this project -- something the earlier version of this README
discussed in theory ("indexes aren't free") without ever measuring.

For each of 4 configurations (no index / single index / composite
index / partitioned + composite index), this script:

  1. Creates a scratch table and preloads it with 1,000,000 real rows
     (so indexes have realistic depth, not an empty B-tree).
  2. Times a warm-up batch INSERT of 5,000 more real rows (discarded).
  3. Times 7 more batch INSERTs of 5,000 rows each, reporting
     mean/stddev -- same methodology as benchmark_query.py.
  4. Reports each table's final data_length / index_length from
     information_schema, to quantify storage overhead.

All source rows come from taxi_trips_partitioned via INSERT ... SELECT
into small staging tables first (so the timed INSERT only measures
insert/index-maintenance cost, not a large-offset SELECT scan).

Usage:
    export TAXI_DB_PASSWORD=your_password
    python python_scripts/measure_write_cost.py
    python python_scripts/measure_write_cost.py --cleanup   # drop scratch tables when done
"""

import argparse
import os
import statistics
import time

import pymysql

PRELOAD_ROWS = 1_000_000
BATCH_ROWS = 5_000
TIMED_RUNS = 7

COLUMNS = """
    VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
    passenger_count, trip_distance, RatecodeID, store_and_fwd_flag,
    PULocationID, DOLocationID, payment_type, fare_amount, extra,
    mta_tax, tip_amount, tolls_amount, improvement_surcharge,
    total_amount, congestion_surcharge, Airport_fee, cbd_congestion_fee
""".strip()

TABLE_DDL = """
    VendorID INT,
    tpep_pickup_datetime DATETIME NOT NULL,
    tpep_dropoff_datetime DATETIME,
    passenger_count DOUBLE,
    trip_distance DOUBLE,
    RatecodeID DOUBLE,
    store_and_fwd_flag TEXT,
    PULocationID INT,
    DOLocationID INT,
    payment_type BIGINT,
    fare_amount DOUBLE,
    extra DOUBLE,
    mta_tax DOUBLE,
    tip_amount DOUBLE,
    tolls_amount DOUBLE,
    improvement_surcharge DOUBLE,
    total_amount DOUBLE,
    congestion_surcharge DOUBLE,
    Airport_fee DOUBLE,
    cbd_congestion_fee DOUBLE
"""

CONFIGS = {
    "bench_no_index": f"CREATE TABLE bench_no_index ({TABLE_DDL})",
    "bench_single_index": f"""
        CREATE TABLE bench_single_index ({TABLE_DDL},
            KEY idx_pickup_time (tpep_pickup_datetime))
    """,
    "bench_composite_index": f"""
        CREATE TABLE bench_composite_index ({TABLE_DDL},
            KEY idx_composite (tpep_pickup_datetime, trip_distance,
                                PULocationID, fare_amount, tip_amount))
    """,
    "bench_partitioned": f"""
        CREATE TABLE bench_partitioned ({TABLE_DDL},
            KEY idx_composite (tpep_pickup_datetime, trip_distance,
                                PULocationID, fare_amount, tip_amount))
        PARTITION BY RANGE COLUMNS(tpep_pickup_datetime) (
            PARTITION p_jan VALUES LESS THAN ('2025-02-01'),
            PARTITION p_feb VALUES LESS THAN ('2025-03-01'),
            PARTITION p_mar VALUES LESS THAN ('2025-04-01'),
            PARTITION p_future VALUES LESS THAN (MAXVALUE)
        )
    """,
}


def connect():
    return pymysql.connect(
        host="127.0.0.1",
        port=3306,
        user=os.environ.get("TAXI_DB_USER", "root"),
        password=os.environ["TAXI_DB_PASSWORD"],
        database="taxi_db",
        autocommit=True,
    )


def setup_staging(conn):
    """One-time: carve out preload + 8 batch-sized staging tables from
    the real dataset, so timed inserts never pay a large-OFFSET scan."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench_preload")
        cur.execute(f"CREATE TABLE bench_preload ({TABLE_DDL})")
        print(f"Staging {PRELOAD_ROWS:,} preload rows...")
        cur.execute(
            f"INSERT INTO bench_preload SELECT {COLUMNS} "
            f"FROM taxi_trips_partitioned LIMIT {PRELOAD_ROWS}"
        )

        for i in range(8):  # batch 0 = warm-up, 1-7 = timed
            offset = PRELOAD_ROWS + i * BATCH_ROWS
            cur.execute(f"DROP TABLE IF EXISTS bench_batch_{i}")
            cur.execute(f"CREATE TABLE bench_batch_{i} ({TABLE_DDL})")
            cur.execute(
                f"INSERT INTO bench_batch_{i} SELECT {COLUMNS} "
                f"FROM taxi_trips_partitioned LIMIT {BATCH_ROWS} OFFSET {offset}"
            )
        print("Staging done.")


def run_config(conn, table_name, create_sql):
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table_name}")
        cur.execute(create_sql)
        cur.execute(f"INSERT INTO {table_name} SELECT {COLUMNS} FROM bench_preload")

        # warm-up (not timed)
        cur.execute(f"INSERT INTO {table_name} SELECT {COLUMNS} FROM bench_batch_0")

        times = []
        for i in range(1, TIMED_RUNS + 1):
            start = time.perf_counter()
            cur.execute(f"INSERT INTO {table_name} SELECT {COLUMNS} FROM bench_batch_{i}")
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            print(f"    batch insert {i}/{TIMED_RUNS}: {elapsed:.4f}s ({BATCH_ROWS:,} rows)")

        mean = statistics.mean(times)
        stdev = statistics.stdev(times) if len(times) > 1 else 0.0

        cur.execute(f"ANALYZE TABLE {table_name}")
        cur.execute(
            "SELECT data_length, index_length FROM information_schema.tables "
            "WHERE table_schema='taxi_db' AND table_name=%s", (table_name,)
        )
        data_len, index_len = cur.fetchone()

    return {
        "table": table_name,
        "mean": mean,
        "stdev": stdev,
        "data_mb": data_len / 1024 / 1024,
        "index_mb": index_len / 1024 / 1024,
    }


def cleanup(conn):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bench_preload")
        for i in range(8):
            cur.execute(f"DROP TABLE IF EXISTS bench_batch_{i}")
        for name in CONFIGS:
            cur.execute(f"DROP TABLE IF EXISTS {name}")
    print("Scratch tables dropped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true",
                         help="Drop all scratch/staging tables after running")
    args = parser.parse_args()

    conn = connect()
    try:
        setup_staging(conn)

        results = []
        for table_name, create_sql in CONFIGS.items():
            print(f"\n=== {table_name} ===")
            results.append(run_config(conn, table_name, create_sql))

        print(f"\n{'table':<24}{'mean':>10}{'stdev':>10}{'data_mb':>12}{'index_mb':>12}")
        for r in results:
            print(f"{r['table']:<24}{r['mean']:>9.4f}s{r['stdev']:>9.4f}s"
                  f"{r['data_mb']:>12.1f}{r['index_mb']:>12.1f}")

        if args.cleanup:
            cleanup(conn)
    finally:
        conn.close()
