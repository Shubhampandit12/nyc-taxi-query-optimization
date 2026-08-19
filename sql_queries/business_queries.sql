-- Phase 7: Business analytics queries, run against the final
-- taxi_trips_partitioned table.

USE taxi_db;

-- Query 1: Peak Hour Revenue Analysis
SELECT
    HOUR(tpep_pickup_datetime) AS hour_of_day,
    COUNT(*)                   AS total_trips,
    ROUND(AVG(fare_amount), 2) AS avg_fare,
    ROUND(SUM(total_amount), 2) AS total_revenue
FROM taxi_trips_partitioned
GROUP BY HOUR(tpep_pickup_datetime)
ORDER BY total_revenue DESC;

-- Query 2: Tip Behaviour by Payment Type
SELECT
    payment_type,
    COUNT(*) AS trips,
    ROUND(AVG(tip_amount), 2) AS avg_tip,
    ROUND(AVG(tip_amount / NULLIF(fare_amount, 0)) * 100, 2) AS avg_tip_pct
FROM taxi_trips_partitioned
WHERE fare_amount > 0
GROUP BY payment_type;

-- Query 3: Top 10 Busiest Pickup Zones
SELECT
    PULocationID,
    COUNT(*) AS total_pickups,
    ROUND(AVG(trip_distance), 2) AS avg_distance,
    ROUND(AVG(total_amount), 2) AS avg_fare
FROM taxi_trips_partitioned
GROUP BY PULocationID
ORDER BY total_pickups DESC
LIMIT 10;

-- Query 4: Month-over-Month Revenue Trend
SELECT
    MONTH(tpep_pickup_datetime) AS month,
    COUNT(*) AS total_trips,
    ROUND(SUM(total_amount), 2) AS total_revenue,
    ROUND(AVG(total_amount), 2) AS avg_fare_per_trip
FROM taxi_trips_partitioned
GROUP BY MONTH(tpep_pickup_datetime)
ORDER BY month;

-- Query 5: Trip Category Comparison
SELECT
    CASE
        WHEN trip_distance < 2  THEN 'Short (< 2 miles)'
        WHEN trip_distance <= 10 THEN 'Medium (2-10 miles)'
        ELSE 'Long (> 10 miles)'
    END AS trip_category,
    COUNT(*) AS trip_count,
    ROUND(AVG(fare_amount), 2) AS avg_fare,
    ROUND(AVG(tip_amount), 2) AS avg_tip
FROM taxi_trips_partitioned
GROUP BY trip_category;
