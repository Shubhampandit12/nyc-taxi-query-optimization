"""
Verifies the SQL migration files themselves -- not the full 11.2M-row
dataset (too heavy for CI), but the schema/DDL correctness and the
row-count/idempotency invariants they're supposed to guarantee. Runs
against a throwaway taxi_db_test database, dropped after every test.
"""

import os

from conftest import run_sql_file

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(REPO_ROOT, "sql_migrations")


def test_create_table_has_designed_types_and_primary_key(db_conn):
    with db_conn.cursor() as cur:
        run_sql_file(cur, os.path.join(MIGRATIONS, "01_create_table.sql"))

        cur.execute("""
            SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'taxi_trips'
        """)
        columns = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    # Money columns must be DECIMAL, not the pandas-inferred DOUBLE
    # (see README Section 2a) -- floating point isn't exact for currency.
    for col in ["fare_amount", "tip_amount", "total_amount"]:
        assert columns[col][0] == "decimal(10,2)", (
            f"{col} should be DECIMAL(10,2), got {columns[col][0]}"
        )

    # PULocationID/DOLocationID reach 265 in the real data -- must be
    # SMALLINT, not TINYINT (which maxes at 255).
    assert columns["PULocationID"][0] == "smallint unsigned"

    # There must be a primary key -- without one, a partial ingest
    # rerun can silently duplicate rows (see README Section 2a).
    assert columns["trip_id"][1] == "PRI"


def test_partitioned_table_has_four_correct_partitions(db_conn):
    with db_conn.cursor() as cur:
        run_sql_file(cur, os.path.join(MIGRATIONS, "01_create_table.sql"))
        run_sql_file(cur, os.path.join(MIGRATIONS, "04_partitioned_table.sql"))

        cur.execute("""
            SELECT PARTITION_NAME
            FROM information_schema.partitions
            WHERE table_schema = DATABASE() AND table_name = 'taxi_trips_partitioned'
            ORDER BY PARTITION_ORDINAL_POSITION
        """)
        partitions = [row[0] for row in cur.fetchall()]

    assert partitions == ["p_jan", "p_feb", "p_mar", "p_future"]


def test_partitioned_migration_preserves_row_count(db_conn):
    with db_conn.cursor() as cur:
        run_sql_file(cur, os.path.join(MIGRATIONS, "01_create_table.sql"))

        # Synthetic rows spanning all 3 real months + one "future" row,
        # to exercise every partition without needing the real dataset.
        cur.execute("""
            INSERT INTO taxi_trips (
                VendorID, tpep_pickup_datetime, passenger_count, trip_distance,
                RatecodeID, store_and_fwd_flag, PULocationID, DOLocationID,
                payment_type, fare_amount, extra, mta_tax, tip_amount,
                tolls_amount, improvement_surcharge, total_amount
            ) VALUES
                (1, '2025-01-15 08:00:00', 1, 3.2, 1, 'N', 161, 237, 1, 15.50, 0.5, 0.5, 3.10, 0, 1.0, 20.60),
                (1, '2025-02-10 12:00:00', 2, 1.1, 1, 'N', 100, 200, 2, 8.00,  0.5, 0.5, 0.00, 0, 1.0, 10.00),
                (2, '2025-03-20 22:30:00', 1, 5.0, 1, 'Y', 50, 60, 1, 22.00, 0.5, 0.5, 4.40, 0, 1.0, 28.40),
                (1, '2025-04-01 00:00:00', 1, 2.0, 1, 'N', 10, 20, 1, 10.00, 0.5, 0.5, 2.00, 0, 1.0, 14.00)
        """)

        run_sql_file(cur, os.path.join(MIGRATIONS, "04_partitioned_table.sql"))

        cur.execute("SELECT COUNT(*) FROM taxi_trips")
        source_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM taxi_trips_partitioned")
        dest_count = cur.fetchone()[0]

        # Confirm every partition actually got the row meant for it,
        # not just that the total matches (pruning behavior depends on this).
        cur.execute("""
            SELECT PARTITION_NAME, TABLE_ROWS
            FROM information_schema.partitions
            WHERE table_schema = DATABASE() AND table_name = 'taxi_trips_partitioned'
              AND TABLE_ROWS IS NOT NULL
            ORDER BY PARTITION_ORDINAL_POSITION
        """)
        rows_per_partition = dict(cur.fetchall())

    assert source_count == 4
    assert dest_count == 4
    assert rows_per_partition["p_jan"] == 1
    assert rows_per_partition["p_feb"] == 1
    assert rows_per_partition["p_mar"] == 1
    assert rows_per_partition["p_future"] == 1


def test_ingest_idempotency_guard_detects_existing_month(db_conn):
    """
    Mirrors the guard query in python_scripts/ingest_taxi_data.py: a
    rerun after a partial failure should see existing rows for a month
    and skip it, rather than silently duplicating them.
    """
    with db_conn.cursor() as cur:
        run_sql_file(cur, os.path.join(MIGRATIONS, "01_create_table.sql"))

        cur.execute("""
            SELECT COUNT(*) FROM taxi_trips
            WHERE tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31 23:59:59'
        """)
        assert cur.fetchone()[0] == 0  # nothing loaded yet -> should NOT skip

        cur.execute("""
            INSERT INTO taxi_trips (VendorID, tpep_pickup_datetime, fare_amount)
            VALUES (1, '2025-01-15 08:00:00', 15.50)
        """)

        cur.execute("""
            SELECT COUNT(*) FROM taxi_trips
            WHERE tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31 23:59:59'
        """)
        assert cur.fetchone()[0] == 1  # January already has a row -> SHOULD skip
