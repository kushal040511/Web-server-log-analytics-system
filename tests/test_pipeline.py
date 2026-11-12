"""ETL, Spark SQL reports, Z-score anomalies and the dashboard export, end to end."""
import json, os
import pandas as pd
from conftest import KNOWN

REPORTS = ["by_hour", "daily", "by_weekday", "top_urls", "by_content_type", "status_distribution",
           "errors_daily", "broken_links", "size_percentiles", "size_buckets", "heaviest_hosts"]


def read(path):
    return pd.read_parquet(path)


def test_etl_counts_every_line(pipeline):
    cur = read(pipeline["curated"])
    n_rejects = sum(1 for _, exp in KNOWN if exp is None)
    assert len(cur) == pipeline["n_lines"] - n_rejects          # nothing silently lost
    summary = pipeline["logs"]["etl"]
    assert f'"malformed": {n_rejects}' in summary
    rej = open(next((pipeline["dir"] / "curated_rejects").glob("part-*"))).read()
    assert "alyssa.p" in rej                                     # the reject is kept for inspection


def test_etl_parses_known_lines(pipeline):
    cur = read(pipeline["curated"])
    cur["date"] = cur["date"].astype(str)
    # Parquet stores instants in UTC; the logs (and the Spark session) use New York time
    t = cur["ts"]
    local = (t.dt.tz_localize("UTC") if t.dt.tz is None else t).dt.tz_convert("America/New_York")
    for line, exp in KNOWN:
        if exp is None:
            continue
        host = line.split()[0]
        ts = line.split("[")[1].split()[0]
        row = cur[(cur["host"] == host) & (local.dt.strftime("%d/%b/%Y:%H:%M:%S") == ts)]
        assert len(row) == 1, line
        row = row.iloc[0]
        for k, v in exp.items():
            assert row[k] == v, f"{k}: {row[k]!r} != {v!r} in {line}"


def test_etl_schema_and_partitions(pipeline):
    cur = read(pipeline["curated"])
    assert {"host", "ts", "method", "endpoint", "protocol", "status", "bytes",
            "hour", "dow", "ext", "date"} <= set(cur.columns)
    parts = sorted(p.name for p in pipeline["curated"].glob("date=*"))
    assert parts[0] == "date=1995-07-01" and len(parts) == 21
    assert all(f.suffix == ".parquet" and ".snappy" in f.name
               for f in pipeline["curated"].rglob("part-*") if not f.name.endswith(".crc"))


def test_all_11_reports_written_and_consistent(pipeline):
    for name in REPORTS:
        assert (pipeline["reports"] / name).exists(), name
    total = len(read(pipeline["curated"]))
    assert read(pipeline["reports"] / "daily")["hits"].sum() == total
    assert read(pipeline["reports"] / "by_hour")["hits"].sum() == total
    status = read(pipeline["reports"] / "status_distribution")
    assert status["hits"].sum() == total
    assert set(status["status"]) == {200, 304, 404}
    assert "/missing.html" in set(read(pipeline["reports"] / "broken_links")["endpoint"])


def test_zscore_finds_the_injected_burst(pipeline):
    hours = read(pipeline["reports"] / "anomaly_hours")
    hours["date"] = hours["date"].astype(str)
    top = hours.sort_values("z", ascending=False).iloc[0]
    assert (top["date"], top["hour"], top["kind"]) == ("1995-07-03", 14, "spike")
    assert top["z"] > 3
    assert (hours["z"].abs() > 3).all()                         # only |z| > 3 is flagged
