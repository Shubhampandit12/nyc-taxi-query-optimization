-- Phase 2/3: Define the taxi_trips table explicitly.
--
-- Previously this table was created implicitly by pandas' df.to_sql()
-- in python_scripts/ingest_taxi_data.py, which infers types from the
-- parquet dtypes rather than designing them: money columns came out
-- as DOUBLE (binary floating point, not exact for currency), small
-- categorical/count columns came out as DOUBLE or BIGINT, and there
-- was no primary key at all. Types below were picked from the real
-- value ranges in the dataset (see README), not guessed:
-- PULocationID/DOLocationID reach 265 (needs SMALLINT, not TINYINT),
-- RatecodeID reaches 99, fare_amount has real negative values and
-- outliers up to ~863K (still fits DECIMAL(10,2)).

USE taxi_db;

CREATE TABLE taxi_trips (
    trip_id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    VendorID                TINYINT UNSIGNED,
    tpep_pickup_datetime     DATETIME NOT NULL,
    tpep_dropoff_datetime    DATETIME,
    passenger_count           TINYINT UNSIGNED,
    trip_distance              DECIMAL(8,2),
    RatecodeID                  TINYINT UNSIGNED,
    store_and_fwd_flag            CHAR(1),
    PULocationID                   SMALLINT UNSIGNED,
    DOLocationID                    SMALLINT UNSIGNED,
    payment_type                     TINYINT UNSIGNED,
    fare_amount                       DECIMAL(10,2),
    extra                               DECIMAL(10,2),
    mta_tax                              DECIMAL(10,2),
    tip_amount                            DECIMAL(10,2),
    tolls_amount                           DECIMAL(10,2),
    improvement_surcharge                   DECIMAL(10,2),
    total_amount                             DECIMAL(10,2),
    congestion_surcharge                      DECIMAL(10,2),
    Airport_fee                                DECIMAL(10,2),
    cbd_congestion_fee                          DECIMAL(10,2)
);

-- Run these to document the loaded table's structure.

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
