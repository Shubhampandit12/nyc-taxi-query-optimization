-- Phase 6: Table partitioning by pickup month (RANGE COLUMNS).
-- Splits the table into physical chunks so a query for one month
-- never touches the other months' data (partition pruning).

USE taxi_db;

-- Step 1: confirm the source table structure matches what's below
-- (from sql_migrations/01_create_table.sql -> SHOW CREATE TABLE taxi_trips)

-- Step 2: create the partitioned table
CREATE TABLE taxi_trips_partitioned (
    VendorID               INT,
    tpep_pickup_datetime    DATETIME NOT NULL,
    tpep_dropoff_datetime   DATETIME,
    passenger_count         DOUBLE,
    trip_distance           DOUBLE,
    RatecodeID              DOUBLE,
    store_and_fwd_flag      TEXT,
    PULocationID             INT,
    DOLocationID             INT,
    payment_type             BIGINT,
    fare_amount              DOUBLE,
    extra                    DOUBLE,
    mta_tax                  DOUBLE,
    tip_amount                DOUBLE,
    tolls_amount               DOUBLE,
    improvement_surcharge     DOUBLE,
    total_amount               DOUBLE,
    congestion_surcharge       DOUBLE,
    Airport_fee                 DOUBLE,
    cbd_congestion_fee          DOUBLE
)
PARTITION BY RANGE COLUMNS(tpep_pickup_datetime) (
    PARTITION p_jan    VALUES LESS THAN ('2025-02-01'),
    PARTITION p_feb    VALUES LESS THAN ('2025-03-01'),
    PARTITION p_mar    VALUES LESS THAN ('2025-04-01'),
    PARTITION p_future VALUES LESS THAN (MAXVALUE)
);

-- Step 3: migrate the data
INSERT INTO taxi_trips_partitioned SELECT * FROM taxi_trips;

-- Verify row count matches the original table
SELECT COUNT(*) FROM taxi_trips_partitioned;

-- Step 4: add the composite covering index to the partitioned table
CREATE INDEX idx_composite
ON taxi_trips_partitioned(tpep_pickup_datetime, trip_distance,
                           PULocationID, fare_amount, tip_amount);

-- Step 5: re-run the benchmark query (pointed at taxi_trips_partitioned)
-- and save the EXPLAIN ANALYZE output to
-- explain_outputs/04_partitioned_explain.txt
