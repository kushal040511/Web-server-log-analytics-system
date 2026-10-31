# Gargi — dashboard export and results website

I built the output side: a Spark job that exports the report tables, and a Flask website
with four report pages, hand-drawn SVG charts and CSV/JSON downloads.

| My file | What it does |
|---|---|
| `jobs/export_dashboard.py` | Reads the HDFS report tables, writes `output/dashboard.json` and every table as CSV and JSON |
| `web/app.py` | Flask server: the four pages, the data API, the download buttons |
| `web/templates/` | `base.html` plus Overview, Traffic, Errors and Performance pages |
| `web/static/js/charts.js` | SVG charts drawn by hand — no charting library |
| `web/static/js/app.js`, `web/static/css/` | Searchable, sortable tables; date-range filter; light and dark theme |
| `output/` | The exported data the website reads |

## Demo (about 3 minutes)

1. `make dashboard` — then open http://localhost:8050.
2. Overview, then Traffic, Errors and Performance — point at the shaded late-July gap.
3. Search the broken-links table and download it as CSV.

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

- Every number on the site comes from a Spark SQL query; the pages only display it.
- The report pages keep working with the cluster stopped, because they read the exported JSON.

## Likely questions

- Why is traffic flat in late July? — Four days are missing from the dataset, not a crash.
- Why only 300 rows in the top-URL table? — A report is capped; true totals come from the full data.

Close: "That is the first half. Next we add the analysis layer on top of these reports."
