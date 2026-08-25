import os
import re

import pymysql
import pytest

TEST_DB = "taxi_db_test"


def run_sql_file(cursor, path):
    """Execute a .sql migration file statement by statement.

    These migration files are plain DDL/DML with no semicolons inside
    string literals, so a naive split on ';' is safe and keeps the
    tests running the actual committed files rather than a
    reimplementation that could drift from them.
    """
    sql = open(path).read()
    sql = re.sub(r"--.*", "", sql)  # strip line comments
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement and not statement.upper().startswith("USE "):
            cursor.execute(statement)


@pytest.fixture(scope="function")
def db_conn():
    conn = pymysql.connect(
        host=os.environ.get("TAXI_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("TAXI_DB_PORT", "3306")),
        user=os.environ.get("TAXI_DB_USER", "root"),
        password=os.environ["TAXI_DB_PASSWORD"],
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
        cur.execute(f"USE {TEST_DB}")

    yield conn

    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    conn.close()
