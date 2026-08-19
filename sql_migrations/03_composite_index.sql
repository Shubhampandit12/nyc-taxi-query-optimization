-- Phase 5: Composite covering index.
-- Replaces the single-column index so the query can be answered
-- entirely from the index, with no extra lookups back to the table.

USE taxi_db;

DROP INDEX idx_pickup_time ON taxi_trips;

CREATE INDEX idx_composite
ON taxi_trips(tpep_pickup_datetime, trip_distance, PULocationID,
              fare_amount, tip_amount);

-- Re-run the benchmark query from sql_queries/baseline_query.sql (unchanged)
-- and save the EXPLAIN ANALYZE output to
-- explain_outputs/03_composite_index_explain.txt
