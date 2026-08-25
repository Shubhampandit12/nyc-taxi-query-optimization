-- Phase 4: Single-column B-Tree index on the pickup datetime column.
-- Lets MySQL jump straight to a date range instead of scanning every row.

USE taxi_db;

CREATE INDEX idx_pickup_time ON taxi_trips(tpep_pickup_datetime);

-- Re-run the benchmark: python python_scripts/benchmark_query.py --table taxi_trips
-- and save the output to explain_outputs/02_single_index_explain.txt
