"""
Live, interactive companion to the NYC Taxi Query Optimization case study.

This app ships with a 440K-row real-data sample (stratified from the same
Jan-Mar 2025 NYC TLC parquet files the full project used) so visitors can
run the actual benchmark query themselves, with and without a covering
index, and see the timing/plan difference live in the browser -- backed by
SQLite here since a hosted MySQL 8 server with an 11.2M-row table and
RANGE COLUMNS partitioning isn't something a free static host can run.

The full-scale numbers (11.2M rows, real MySQL, 7-run statistical
benchmarking, RANGE COLUMNS partitioning, write-cost trade-offs) are the
project's real, github-published results -- reproduced below as captured,
not re-simulated. See the README for the complete methodology:
https://github.com/Shubhampandit12/nyc-taxi-query-optimization
"""

import sqlite3
import statistics
import time
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="NYC Taxi Query Optimization", page_icon="🚕", layout="wide")

DATA_PATH = Path(__file__).parent / "streamlit_app" / "sample_trips.parquet"
REPO_URL = "https://github.com/Shubhampandit12/nyc-taxi-query-optimization"


@st.cache_resource
def build_demo_db():
    """Load the sample parquet into two in-memory SQLite tables: one
    plain, one with the same composite covering index the real MySQL
    project uses -- so the live query below can compare both."""
    df = pd.read_parquet(DATA_PATH)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    df.to_sql("trips_noindex", conn, index=False)
    df.to_sql("trips_indexed", conn, index=False)
    conn.execute(
        "CREATE INDEX idx_composite ON trips_indexed "
        "(tpep_pickup_datetime, trip_distance, PULocationID, fare_amount, tip_amount)"
    )
    conn.commit()
    return conn, df


QUERY_TEMPLATE = """
SELECT
    PULocationID,
    date(tpep_pickup_datetime) AS trip_date,
    COUNT(*)             AS total_trips,
    AVG(tip_amount)      AS avg_tip,
    AVG(trip_distance)   AS avg_distance,
    SUM(fare_amount)     AS total_revenue
FROM {table}
WHERE trip_distance > 5
  AND tpep_pickup_datetime BETWEEN '2025-01-01' AND '2025-01-31'
GROUP BY PULocationID, date(tpep_pickup_datetime)
ORDER BY total_revenue DESC
"""


def run_timed(conn, table, repeats=15):
    sql = QUERY_TEMPLATE.format(table=table)
    cur = conn.cursor()
    cur.execute(sql)  # warm-up
    cur.fetchall()

    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchall()
        times.append(time.perf_counter() - start)

    plan_rows = cur.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    plan = "\n".join(r[-1] for r in plan_rows)
    return {
        "mean_ms": statistics.mean(times) * 1000,
        "stdev_ms": (statistics.stdev(times) if len(times) > 1 else 0.0) * 1000,
        "rows_returned": len(rows),
        "plan": plan,
    }


conn, sample_df = build_demo_db()

st.title("🚕 NYC Taxi Query Optimization")
st.caption(
    "A case study in taking a slow analytical MySQL query from a full "
    f"table scan to a covering-index range scan, proven with EXPLAIN "
    f"ANALYZE. [Full write-up & source →]({REPO_URL})"
)

st.markdown(
    """
On the real project (11.2M real NYC taxi trip rows, MySQL, hosted locally),
a query asking *"for trips over 5 miles in January, which pickup zones
earned the most revenue?"* took **~7 seconds** with no index, because MySQL
had to scan every row. A **composite covering index** — one index holding
every column the query needs — cut that to **~1.7 seconds (a real,
statistically-validated 4x)**, by letting MySQL answer the query straight
from the index with zero trips back to the table.

The interactive demo below runs that *exact* query for real, against a
440K-row real-data sample, so you can see the difference yourself.
"""
)

st.divider()

st.header("Try it yourself")
st.caption(
    "440,000 real trip rows (a stratified sample of the same Jan-Mar 2025 "
    "NYC TLC data). Selectivity differs from the full 11.2M-row table, so "
    "treat the *relative* speedup — not the absolute milliseconds — as the "
    "story here; the full-scale numbers are below."
)

col1, col2 = st.columns(2)

with col1:
    st.subheader("No index (baseline)")
    if st.button("Run baseline query", width="stretch"):
        with st.spinner("Running 15 timed repeats..."):
            st.session_state["baseline"] = run_timed(conn, "trips_noindex")
    if "baseline" in st.session_state:
        r = st.session_state["baseline"]
        st.metric("Mean query time", f"{r['mean_ms']:.1f} ms", f"±{r['stdev_ms']:.1f} ms stdev")
        st.code(r["plan"], language="text")

with col2:
    st.subheader("Composite covering index")
    if st.button("Run indexed query", width="stretch", type="primary"):
        with st.spinner("Running 15 timed repeats..."):
            st.session_state["indexed"] = run_timed(conn, "trips_indexed")
    if "indexed" in st.session_state:
        r = st.session_state["indexed"]
        st.metric("Mean query time", f"{r['mean_ms']:.1f} ms", f"±{r['stdev_ms']:.1f} ms stdev")
        st.code(r["plan"], language="text")

if "baseline" in st.session_state and "indexed" in st.session_state:
    speedup = st.session_state["baseline"]["mean_ms"] / max(st.session_state["indexed"]["mean_ms"], 1e-6)
    st.success(f"Covering index was **{speedup:.1f}x faster** on this sample, just now, in your browser.")
    chart_df = pd.DataFrame(
        {
            "Stage": ["No index", "Composite covering index"],
            "Mean query time (ms)": [
                st.session_state["baseline"]["mean_ms"],
                st.session_state["indexed"]["mean_ms"],
            ],
        }
    ).set_index("Stage")
    st.bar_chart(chart_df)

st.divider()

st.header("Full-scale results (11.2M rows, real MySQL)")
st.caption(
    "Captured on the actual project, not simulated here — mean of 7 timed "
    "runs after a warm-up, per stage. Full methodology, EXPLAIN ANALYZE "
    f"output, and write-cost trade-offs: [{REPO_URL}]({REPO_URL})"
)

full_scale = pd.DataFrame(
    [
        {"Stage": "1. No index, no partition (baseline)", "Mean (s)": 6.99, "Note": "Table scan, 11.2M rows"},
        {"Stage": "2. Single B-Tree index", "Mean (s)": 6.74, "Note": "Optimizer rejected the index — still a table scan"},
        {"Stage": "3. Composite covering index", "Mean (s)": 1.74, "Note": "Covering index range scan — 4.0x faster"},
        {"Stage": "4. Partitioning + composite index", "Mean (s)": 1.99, "Note": "Partition pruning confirmed, but redundant with stage 3"},
    ]
)
st.bar_chart(full_scale.set_index("Stage")["Mean (s)"])
st.dataframe(full_scale, hide_index=True, width="stretch")

with st.expander("Why did the single index get rejected — and why didn't partitioning help on top of the index?"):
    st.markdown(
        """
**The single index was rejected by MySQL's own cost-based optimizer.**
The January date range covers about 31% of the whole table. At that
selectivity, using the index means ~3.5 million random-access lookups back
to the table (a "heap fetch" per matching row) — which costs *more* than
just scanning through sequentially. MySQL correctly chose the table scan.
A **covering index** fixes this by holding every column the query needs,
so there's no heap fetch to pay for at all — hence the 4x win.

**Partitioning (by month) is real** — `EXPLAIN` confirms only the January
partition is ever opened — but stacked on top of the composite index it
doesn't add anything, because both are pruning the *same* rows for the
*same* reason (the `tpep_pickup_datetime` predicate). Tested in isolation
(index removed), partitioning alone is a genuine ~3.3x win. The honest
takeaway: partitioning and a covering index are redundant when they target
the same predicate — not that partitioning "doesn't work."
"""
    )

st.divider()

st.header("Business analytics (computed live, from the sample)")
tab1, tab2, tab3 = st.tabs(["Peak hour revenue", "Busiest pickup zones", "Trip length mix"])

with tab1:
    hourly = (
        sample_df.assign(hour=pd.to_datetime(sample_df["tpep_pickup_datetime"]).dt.hour)
        .groupby("hour")["total_amount"].sum()
        .rename("Total revenue ($)")
    )
    st.bar_chart(hourly)

with tab2:
    zones = sample_df["PULocationID"].value_counts().head(10).rename("Pickups")
    st.bar_chart(zones)

with tab3:
    dist = sample_df["trip_distance"].clip(upper=15)
    st.bar_chart(pd.cut(dist, bins=[0, 2, 10, 15], labels=["Short (<2mi)", "Medium (2-10mi)", "Long (>10mi)"])
                 .value_counts().sort_index().rename("Trip count"))

st.caption(
    "Computed from the 440K-row sample used in this demo, not the full "
    "11.2M-row dataset — proportions hold, absolute counts don't. See "
    f"[business_queries.sql]({REPO_URL}/blob/main/sql_queries/business_queries.sql) "
    "for the real numbers against the full table."
)

st.divider()
st.markdown(
    f"**Full project, source, tests, and CI:** [{REPO_URL}]({REPO_URL}) "
    "— includes the explicit schema design, a scoped least-privilege DB "
    "user, idempotent ingestion, write-cost/storage trade-off "
    "measurements, and generalization tests across 3 differently-selective "
    "query shapes."
)
