-- Phase 4: Single-column B-Tree index on the pickup datetime column.
-- Lets MySQL jump straight to a date range instead of scanning every row.

USE taxi_db;

CREATE INDEX idx_pickup_time ON taxi_trips(tpep_pickup_datetime);

-- Re-run the benchmark query from sql_queries/baseline_query.sql (unchanged)
-- and save the EXPLAIN ANALYZE output to
-- explain_outputs/02_single_index_explain.txt
