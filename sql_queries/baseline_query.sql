-- Phase 3: Benchmark query used to measure every optimization stage.
-- Run this unchanged against taxi_trips (baseline / single index /
-- composite index) and again against taxi_trips_partitioned (Phase 6).

USE taxi_db;

EXPLAIN ANALYZE
SELECT
    PULocationID,
    DATE(tpep_pickup_datetime) AS trip_date,
    COUNT(*)                   AS total_trips,
    AVG(tip_amount)            AS avg_tip,
    AVG(trip_distance)         AS avg_distance,
    SUM(fare_amount)           AS total_revenue
FROM taxi_trips
WHERE trip_distance > 5
  AND tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31'
GROUP BY PULocationID, DATE(tpep_pickup_datetime)
ORDER BY total_revenue DESC;

-- Wall-clock timing for the README comparison table (warm-up + 7 runs,
-- mean/stddev, not a single-run stopwatch capture):
-- python python_scripts/benchmark_query.py --table taxi_trips
