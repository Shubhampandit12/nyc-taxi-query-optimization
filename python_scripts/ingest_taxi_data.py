import calendar
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, inspect, text

# DB password is read from an environment variable, not hardcoded,
# since this script is meant to be pushed to GitHub.
# Set it before running: export TAXI_DB_PASSWORD=your_password
DB_USER = os.environ.get("TAXI_DB_USER", "root")
DB_PASSWORD = os.environ["TAXI_DB_PASSWORD"]
engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:3306/taxi_db")

files_to_load = [
    ("yellow_tripdata_2025-01.parquet", 2025, 1),
    ("yellow_tripdata_2025-02.parquet", 2025, 2),
    ("yellow_tripdata_2025-03.parquet", 2025, 3),
]

if not inspect(engine).has_table("taxi_trips"):
    sys.exit(
        "taxi_trips does not exist. Run sql_migrations/01_create_table.sql "
        "first — this script no longer relies on pandas to auto-create the "
        "table, since that produced an undesigned schema (see README)."
    )

for file, year, month in files_to_load:
    month_start = f"{year:04d}-{month:02d}-01"
    days_in_month = calendar.monthrange(year, month)[1]
    month_end = f"{year:04d}-{month:02d}-{days_in_month:02d} 23:59:59"

    with engine.connect() as conn:
        existing = conn.execute(
            text(
                "SELECT COUNT(*) FROM taxi_trips "
                "WHERE tpep_pickup_datetime BETWEEN :start AND :end"
            ),
            {"start": month_start, "end": month_end},
        ).scalar()

    if existing:
        print(f"Skipping {file}: {existing} rows already loaded for {year}-{month:02d}.")
        continue

    print(f"Loading {file} into MySQL...")

    # Read the parquet file into Pandas memory
    df = pd.read_parquet(file)

    # Push the data to the 'taxi_trips' table
    # chunksize prevents memory crashes when loading millions of rows
    df.to_sql(name="taxi_trips", con=engine, if_exists="append", index=False, chunksize=100000)

    print(f"Successfully loaded {file}!\n")

print("All data ingested successfully!")
