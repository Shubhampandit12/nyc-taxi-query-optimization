import os

import pandas as pd
from sqlalchemy import create_engine

# DB password is read from an environment variable, not hardcoded,
# since this script is meant to be pushed to GitHub.
# Set it before running: export TAXI_DB_PASSWORD=your_password
DB_PASSWORD = os.environ["TAXI_DB_PASSWORD"]
engine = create_engine(f"mysql+pymysql://root:{DB_PASSWORD}@127.0.0.1:3306/taxi_db")

files_to_load = [
    "yellow_tripdata_2025-01.parquet",
    "yellow_tripdata_2025-02.parquet",
    "yellow_tripdata_2025-03.parquet",
]

for file in files_to_load:
    print(f"Loading {file} into MySQL...")

    # Read the parquet file into Pandas memory
    df = pd.read_parquet(file)

    # Push the data to the 'taxi_trips' table
    # chunksize prevents memory crashes when loading millions of rows
    df.to_sql(name="taxi_trips", con=engine, if_exists="append", index=False, chunksize=100000)

    print(f"Successfully loaded {file}!\n")

print("All data ingested successfully!")
