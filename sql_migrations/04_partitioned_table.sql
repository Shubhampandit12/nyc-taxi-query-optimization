-- Phase 6: Table partitioning by pickup month (RANGE COLUMNS).
-- Splits the table into physical chunks so a query for one month
-- never touches the other months' data (partition pruning).

USE taxi_db;

-- Step 1: create the partitioned table, matching the corrected schema
-- from sql_migrations/01_create_table.sql. Note: MySQL requires every
-- unique key on a partitioned table to include the partitioning
-- column, so the primary key here is composite
-- (trip_id, tpep_pickup_datetime) rather than trip_id alone.
CREATE TABLE taxi_trips_partitioned (
    trip_id                 BIGINT UNSIGNED NOT NULL,
    VendorID                 TINYINT UNSIGNED,
    tpep_pickup_datetime      DATETIME NOT NULL,
    tpep_dropoff_datetime     DATETIME,
    passenger_count            TINYINT UNSIGNED,
    trip_distance                DECIMAL(8,2),
    RatecodeID                     TINYINT UNSIGNED,
    store_and_fwd_flag               CHAR(1),
    PULocationID                       SMALLINT UNSIGNED,
    DOLocationID                        SMALLINT UNSIGNED,
    payment_type                         TINYINT UNSIGNED,
    fare_amount                           DECIMAL(10,2),
    extra                                   DECIMAL(10,2),
    mta_tax                                  DECIMAL(10,2),
    tip_amount                                DECIMAL(10,2),
    tolls_amount                               DECIMAL(10,2),
    improvement_surcharge                       DECIMAL(10,2),
    total_amount                                 DECIMAL(10,2),
    congestion_surcharge                          DECIMAL(10,2),
    Airport_fee                                    DECIMAL(10,2),
    cbd_congestion_fee                              DECIMAL(10,2),
    PRIMARY KEY (trip_id, tpep_pickup_datetime)
)
PARTITION BY RANGE COLUMNS(tpep_pickup_datetime) (
    PARTITION p_jan    VALUES LESS THAN ('2025-02-01'),
    PARTITION p_feb    VALUES LESS THAN ('2025-03-01'),
    PARTITION p_mar    VALUES LESS THAN ('2025-04-01'),
    PARTITION p_future VALUES LESS THAN (MAXVALUE)
);

-- Step 2: migrate the data (explicit column list, since the two
-- tables no longer share identical column sets by coincidence)
INSERT INTO taxi_trips_partitioned (
    trip_id, VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
    passenger_count, trip_distance, RatecodeID, store_and_fwd_flag,
    PULocationID, DOLocationID, payment_type, fare_amount, extra,
    mta_tax, tip_amount, tolls_amount, improvement_surcharge,
    total_amount, congestion_surcharge, Airport_fee, cbd_congestion_fee
)
SELECT
    trip_id, VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
    passenger_count, trip_distance, RatecodeID, store_and_fwd_flag,
    PULocationID, DOLocationID, payment_type, fare_amount, extra,
    mta_tax, tip_amount, tolls_amount, improvement_surcharge,
    total_amount, congestion_surcharge, Airport_fee, cbd_congestion_fee
FROM taxi_trips;

-- Verify row count matches the original table
SELECT COUNT(*) FROM taxi_trips_partitioned;

-- Step 3: add the composite covering index to the partitioned table
CREATE INDEX idx_composite
ON taxi_trips_partitioned(tpep_pickup_datetime, trip_distance,
                           PULocationID, fare_amount, tip_amount);

-- Step 4: re-run the benchmark query (pointed at taxi_trips_partitioned)
-- via python_scripts/benchmark_query.py and save the output to
-- explain_outputs/04_partitioned_explain.txt
