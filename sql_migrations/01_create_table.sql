-- Phase 2/3: Inspect the taxi_trips table
-- The table itself was created automatically by pandas' df.to_sql()
-- in python_scripts/ingest_taxi_data.py. Run these to document its
-- structure before any optimization work begins.

USE taxi_db;

-- How many rows do we have?
SELECT COUNT(*) FROM taxi_trips;

-- Column names and data types
DESCRIBE taxi_trips;

-- Sample of actual data
SELECT * FROM taxi_trips LIMIT 5;

-- Date range covered
SELECT MIN(tpep_pickup_datetime) AS earliest,
       MAX(tpep_pickup_datetime) AS latest
FROM taxi_trips;

-- Row distribution across months
SELECT MONTH(tpep_pickup_datetime) AS month,
       COUNT(*) AS trip_count
FROM taxi_trips
GROUP BY MONTH(tpep_pickup_datetime);

-- Full CREATE TABLE statement (copy output into README / use as
-- reference when building the partitioned table in Phase 6)
SHOW CREATE TABLE taxi_trips;
