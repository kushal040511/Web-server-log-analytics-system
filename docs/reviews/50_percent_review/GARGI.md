# Gargi — Spark SQL: reports, anomaly detection and website

My big data tool is **Spark SQL**. I query Kushal's clean Parquet data with plain SQL: 11
reports, a Z-score anomaly detection algorithm, and an export that feeds our 4-page website
with hand-drawn SVG charts and CSV/JSON downloads.

| My file | What it does |
|---|---|
| `jobs/reports.py` | 11 Spark SQL reports in four groups: traffic, content, errors, performance; each is saved to the HDFS reports zone |
| `jobs/anomalies.py` | Z-score anomaly detection in Spark SQL: flags hours and days with far more (or less) traffic than normal |
| `jobs/export_dashboard.py` | Reads the HDFS report tables, writes `output/dashboard.json` and every table as CSV and JSON |
| `web/app.py` | Flask server: the four pages, the data API, the download buttons |
| `web/templates/` | `base.html` plus Overview, Traffic, Errors and Performance pages |
| `web/static/js/charts.js` | SVG charts drawn by hand — no charting library |
| `web/static/js/app.js`, `web/static/css/` | Searchable, sortable tables; date-range filter; light and dark theme |
| `output/` | The exported data the website reads |

The 11 reports: `by_hour`, `daily`, `by_weekday`, `top_urls`, `by_content_type`,
`status_distribution`, `errors_daily`, `broken_links`, `size_percentiles`, `size_buckets`,
`heaviest_hosts`.

## Z-score anomaly detection, in 4 steps

1. Count the requests for every date and hour (1,369 hour slots).
2. For each hour of the day, find the normal level: the mean and standard deviation across all 58 days.
3. Score every hour: z = (requests − mean) ÷ standard deviation.
4. Flag every hour with |z| > 3 (a spike above, a drop below).

## Demo (about 4 minutes)

1. `make reports`, then `make report T=top_urls N=10` — a report read back from HDFS.
2. `make anomalies` — prints the 12 flagged hours.
3. `make dashboard` — then open http://localhost:8050.
4. Overview, then Traffic (show the "Unusual hours" table), Errors and Performance — point at the shaded late-July gap.
5. Search the broken-links table and download it as CSV.

To practise without Docker, run only the website: see `REQUIREMENTS_GARGI.md`, "Quickest".

## What to say — the findings

| Finding | Number |
|---|---|
| Requests analysed | 3,461,612 from 137,978 hosts, 61 GB served |
| Successful (200) | 89.6% |
| Not found (404) | 20,899 requests across 3,544 URLs (0.6%) |
| Most-visited page | `/ksc.html`, 79,993 hits |
| Worst broken link | `/pub/winvn/readme.txt`, 2,004 hits |
| Busiest hour / day | 15:00 (230,665 hits) / Thursday (667,737) |
| Typical response size | median 4,179 bytes; 99th percentile 283,389 bytes |
| Images | GIFs are 1,989,529 hits and 22.5 GB of the 61 GB |
| Unusual hours (z > 3) | 12, of which 11 on 13 July; strongest 13 July 9–10 AM: 14,926 requests vs a normal 3,080 (z = 5.9) |

- Every number on the site comes from a Spark SQL query; the pages only display it.
- The anomaly algorithm found the Space Shuttle STS-70 launch (13 July, 9:41 AM) from the numbers alone.
  13 July is also the only unusual day (z = 3.62).
- The report pages keep working with the cluster stopped, because they read the exported JSON.

## Likely questions

- Why Spark SQL? — SQL says what we want; Spark plans how to run it in parallel on the cluster.
- Why |z| > 3? — Under a normal distribution only about 0.3% of values fall that far from the mean.
- Why compare with the same hour? — A busy afternoon is normal; only an unusually busy afternoon is an anomaly.
- Why is traffic flat in late July? — Four days are missing from the dataset, not a crash.
- Why only 300 rows in the top-URL table? — A report is capped; true totals come from the full data.

Close: "That is the first half. Next we add the deep-learning model on top of this pipeline."
