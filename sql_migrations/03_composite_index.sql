-- Phase 5: Composite covering index.
-- Replaces the single-column index so the query can be answered
-- entirely from the index, with no extra lookups back to the table.

USE taxi_db;

DROP INDEX idx_pickup_time ON taxi_trips;

CREATE INDEX idx_composite
ON taxi_trips(tpep_pickup_datetime, trip_distance, PULocationID,
              fare_amount, tip_amount);

-- Re-run the benchmark: python python_scripts/benchmark_query.py --table taxi_trips
-- and save the output to explain_outputs/03_composite_index_explain.txt
