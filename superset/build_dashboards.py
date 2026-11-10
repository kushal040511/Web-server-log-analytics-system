#!/usr/bin/env python3
"""
Build the Log Analytics dashboards in Apache Superset through its REST API.

Creates (or replaces) one database connection to the Spark Thrift Server, a dataset per
Spark table, the charts, and five dashboards:

  1. Traffic overview        2. Errors and performance     3. Anomalies (Z-score)
  4. Bot detection (GMM)     5. Attack detection (ML)

Every chart is a Spark SQL query on the tables in HDFS (sql/bi_tables.sql); nothing is
precomputed in Superset. Run it against a running Superset:

    python3 build_dashboards.py http://localhost:8088 admin admin hive://thrift:10000/weblogs

The container runs it once at start-up (superset/bootstrap.sh). It is idempotent: running it
again deletes and recreates the objects it owns (titles starting with the dashboard number).
"""
import json, sys, time, uuid
import requests

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8088"
USER = sys.argv[2] if len(sys.argv) > 2 else "admin"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "admin"
SPARK_URI = sys.argv[4] if len(sys.argv) > 4 else "hive://thrift:10000/weblogs"
DB_NAME = "Spark SQL (HDFS)"
SCHEMA = "weblogs"

s = requests.Session()
API = URL.rstrip("/") + "/api/v1"


def login():
    for _ in range(60):
        try:
            r = s.post(API + "/security/login", json={"username": USER, "password": PASSWORD,
                                                     "provider": "db", "refresh": True}, timeout=10)
            if r.ok:
                s.headers["Authorization"] = "Bearer " + r.json()["access_token"]
                csrf = s.get(API + "/security/csrf_token/", timeout=10)
                if csrf.ok:
                    s.headers["X-CSRFToken"] = csrf.json()["result"]
                s.headers["Referer"] = URL
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    sys.exit("Superset did not answer at " + URL)


def call(method, path, **kw):
    for attempt in range(8):
        r = s.request(method, API + path, timeout=120, **kw)
        if r.status_code != 429:          # rate limited: wait and try again
            break
        time.sleep(1 + attempt)
    if not r.ok:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else {}


def find(kind, col, value):
    q = json.dumps({"filters": [{"col": col, "opr": "eq", "value": value}], "page_size": 100})
    return [x["id"] for x in call("GET", f"/{kind}/", params={"q": q})["result"]]


# ------------------------------------------------------------------ database + datasets
def database():
    ids = find("database", "database_name", DB_NAME)
    body = {"database_name": DB_NAME, "sqlalchemy_uri": SPARK_URI, "expose_in_sqllab": True,
            "allow_run_async": False, "allow_ctas": False, "allow_dml": False,
            "extra": json.dumps({"metadata_cache_timeout": {}, "engine_params": {},
                                 "schemas_allowed_for_file_upload": []})}
    if ids:
        call("PUT", f"/database/{ids[0]}", json=body)
        return ids[0]
    return call("POST", "/database/", json=body)["id"]


DATASETS = {}


def dataset(name, sql=None):
    """A physical dataset (Spark table) or, with sql, a virtual one."""
    for i in find("dataset", "table_name", name):
        call("DELETE", f"/dataset/{i}")
    body = {"database": DB_ID, "schema": SCHEMA, "table_name": name}
    if sql:
        body["sql"] = sql
    r = s.post(API + "/dataset/", json=body, timeout=300)
    if not r.ok:   # the table is not in HDFS yet (that pipeline stage has not run): skip its charts
        print(f"  [skip] dataset {name}: {r.text[:160]}")
        return None
    DATASETS[name] = r.json()["id"]
    return DATASETS[name]


def m(sql, label):
    """An ad-hoc SQL metric."""
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label,
            "optionName": "metric_" + uuid.uuid4().hex[:10], "hasCustomLabel": True}


def col(sql, label):
    """An ad-hoc SQL column (for x axes and group-bys)."""
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label}


def flt(sql):
    return {"expressionType": "SQL", "clause": "WHERE", "sqlExpression": sql,
            "filterOptionName": "filter_" + uuid.uuid4().hex[:10]}


# ------------------------------------------------------------------ charts
CHARTS = []   # (dashboard number, chart id, width, height)


def chart(dash, title, ds, viz, width=4, height=50, **params):
    if ds not in DATASETS:
        return None
    p = {"datasource": f"{DATASETS[ds]}__table", "viz_type": viz, "adhoc_filters": [],
         "row_limit": 10000, "color_scheme": "supersetColors"}
    p.update(params)
    cid = call("POST", "/chart/", json={"slice_name": title, "viz_type": viz,
                                        "datasource_id": DATASETS[ds], "datasource_type": "table",
                                        "params": json.dumps(p)})["id"]
    CHARTS.append((dash, cid, width, height, title))
    return cid


def big(dash, title, ds, metric, sub, fmt=",d", width=3, height=25, filters=None):
    return chart(dash, title, ds, "big_number_total", width, height, metric=metric, subheader=sub,
                 y_axis_format=fmt, header_font_size=0.4, subheader_font_size=0.15,
                 adhoc_filters=filters or [])


def bar(dash, title, ds, x, metrics, width=6, height=50, groupby=None, sort_x=True, horizontal=False,
        fmt=",d", stack=False, filters=None, legend=None, x_title="", y_title="", order_desc=True,
        sort_by_metric=False, limit=10000, values=None):
    params = dict(x_axis=x, metrics=metrics, groupby=groupby or [], orientation="horizontal" if horizontal else "vertical",
                  y_axis_format=fmt, show_legend=bool(groupby) if legend is None else legend,
                  stack="Stack" if stack else None, x_axis_title=x_title, y_axis_title=y_title,
                  x_axis_title_margin=30 if x_title else 15, y_axis_title_margin=40 if y_title else 15,
                  rich_tooltip=True, show_value=(not groupby) if values is None else values, adhoc_filters=filters or [], row_limit=limit,
                  truncateXAxis=True, xAxisLabelRotation=0, order_desc=order_desc)
    if sort_by_metric:
        params.update(x_axis_sort=metrics[0]["label"], x_axis_sort_asc=False)
    elif sort_x:
        params.update(x_axis_sort_asc=True)
    return chart(dash, title, ds, "echarts_timeseries_bar", width, height, **params)


def line(dash, title, ds, x, metrics, width=12, height=50, grain="P1D", fmt=",d", groupby=None, area=False,
         markers=False, filters=None, y_title=""):
    return chart(dash, title, ds, "echarts_area" if area else "echarts_timeseries_line", width, height,
                 x_axis=x, time_grain_sqla=grain, metrics=metrics, groupby=groupby or [], y_axis_format=fmt,
                 show_legend=bool(groupby) or len(metrics) > 1, rich_tooltip=True, markerEnabled=markers,
                 markerSize=6, adhoc_filters=filters or [], y_axis_title=y_title, y_axis_title_margin=40 if y_title else 15,
                 opacity=0.4, x_axis_time_format="smart_date")


def pie(dash, title, ds, groupby, metric, width=4, height=50, donut=True, filters=None):
    return chart(dash, title, ds, "pie", width, height, groupby=[groupby], metric=metric, donut=donut,
                 show_labels=True, label_type="key_percent", labels_outside=True, show_legend=False,
                 number_format=",d", innerRadius=40, outerRadius=75, adhoc_filters=filters or [],
                 sort_by_metric=True)


def table(dash, title, ds, columns, width=6, height=50, order=None, limit=1000, page=10, filters=None):
    return chart(dash, title, ds, "table", width, height, query_mode="raw", all_columns=columns,
                 order_by_cols=[json.dumps([c, asc]) for c, asc in (order or [])], row_limit=limit,
                 server_page_length=page, include_search=True, table_timestamp_format="%Y-%m-%d",
                 adhoc_filters=filters or [], show_cell_bars=True, color_pn=True)


def pivot(dash, title, ds, rows, cols, metric, width=4, height=50, filters=None, fmt=",d"):
    return chart(dash, title, ds, "pivot_table_v2", width, height, groupbyRows=rows, groupbyColumns=cols,
                 metrics=[metric], aggregateFunction="Sum", valueFormat=fmt, rowTotals=False, colTotals=False,
                 metricsLayout="COLUMNS", conditional_formatting=[], adhoc_filters=filters or [])


# ------------------------------------------------------------------ dashboards
def dashboard(number, title, slug, intro):
    for i in find("dashboard", "slug", slug):
        call("DELETE", f"/dashboard/{i}")
    items = [c for c in CHARTS if c[0] == number]
    pos = {"DASHBOARD_VERSION_KEY": "v2",
           "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
           "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
           "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": title}}}
    # an intro text block, then charts packed into rows of 12 columns
    rows, row, used = [], [], 0
    rows.append([("MARKDOWN", intro, 12, 18)])
    for _, cid, w, h, name in items:
        if used + w > 12:
            rows.append(row); row, used = [], 0
        row.append(("CHART", (cid, name), w, h)); used += w
    if row:
        rows.append(row)
    for ri, r in enumerate(rows):
        rid = f"ROW-{ri}"
        pos["GRID_ID"]["children"].append(rid)
        pos[rid] = {"type": "ROW", "id": rid, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                    "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        for ci, (kind, what, w, h) in enumerate(r):
            iid = f"{kind}-{ri}-{ci}"
            pos[rid]["children"].append(iid)
            if kind == "MARKDOWN":
                pos[iid] = {"type": "MARKDOWN", "id": iid, "children": [], "parents": ["ROOT_ID", "GRID_ID", rid],
                            "meta": {"width": w, "height": h, "code": what}}
            else:
                cid, name = what
                pos[iid] = {"type": "CHART", "id": iid, "children": [], "parents": ["ROOT_ID", "GRID_ID", rid],
                            "meta": {"width": w, "height": h, "chartId": cid, "sliceName": name,
                                     "sliceNameOverride": name.split(". ", 1)[1]}}
    for f in NATIVE_FILTERS.get(number, []):   # a filter with targets applies to those charts only
        t = f.pop("_targets", None)
        if t:
            f["scope"]["excluded"] = [c[1] for c in items if c[1] not in t]
    meta = {"color_scheme": "supersetColors", "refresh_frequency": 0, "timed_refresh_immune_slices": [],
            "expanded_slices": {}, "label_colors": {}, "shared_label_colors": {}, "cross_filters_enabled": True,
            "native_filter_configuration": NATIVE_FILTERS.get(number, [])}
    did = call("POST", "/dashboard/", json={"dashboard_title": title, "slug": slug, "published": True,
                                            "position_json": json.dumps(pos), "json_metadata": json.dumps(meta)})["id"]
    for _, cid, *_ in items:
        call("PUT", f"/chart/{cid}", json={"dashboards": [did]})
    print(f"  dashboard {number}: {title}  ({len(items)} charts)  {URL}/superset/dashboard/{slug}/")
    return did


NATIVE_FILTERS = {}


def select_filter(number, name, ds, column, default=None, targets=None):
    if ds not in DATASETS:
        return
    f = {"id": "NATIVE_FILTER-" + uuid.uuid4().hex[:10], "name": name, "filterType": "filter_select",
         "targets": [{"datasetId": DATASETS[ds], "column": {"name": column}}],
         "controlValues": {"multiSelect": False, "enableEmptyFilter": default is not None,
                           "defaultToFirstItem": False, "searchAllOptions": False, "inverseSelection": False},
         "defaultDataMask": {"filterState": {"value": [default] if default else None},
                             "extraFormData": {"filters": [{"col": column, "op": "IN", "val": [default]}]} if default else {}},
         "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
         "type": "NATIVE_FILTER", "description": "", "chartsInScope": targets or [], "_targets": targets}
    NATIVE_FILTERS.setdefault(number, []).append(f)


def time_filter(number, name):
    NATIVE_FILTERS.setdefault(number, []).append({
        "id": "NATIVE_FILTER-" + uuid.uuid4().hex[:10], "name": name, "filterType": "filter_time",
        "targets": [{}], "controlValues": {}, "defaultDataMask": {"filterState": {"value": "No filter"},
                                                                    "extraFormData": {}},
        "cascadeParentIds": [], "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "type": "NATIVE_FILTER", "description": ""})


# ================================================================== build
login()
DB_ID = database()
print(f"[superset] database '{DB_NAME}' -> {SPARK_URI}")

# remove our old charts (titles are prefixed with the dashboard number)
for prefix in ("1.", "2.", "3.", "4.", "5."):
    q = json.dumps({"filters": [{"col": "slice_name", "opr": "sw", "value": prefix}], "page_size": 100})
    for c in call("GET", "/chart/", params={"q": q})["result"]:
        call("DELETE", f"/chart/{c['id']}")

for t in ["access_logs", "daily", "by_hour", "by_weekday", "top_urls", "by_content_type", "status_distribution",
          "errors_daily", "broken_links", "size_buckets", "size_percentiles", "heaviest_hosts", "anomaly_hours",
          "anomaly_days", "host_clusters", "bot_clusters", "bot_summary", "ml_summary", "ml_comparison",
          "ml_confusion", "ml_robustness", "ml_mcnemar", "ml_crossdataset", "ml_tuning", "ml_nasa_audit",
          "ml_nasa_flagged"]:
    dataset(t)
dataset("hourly_traffic", sql="""SELECT date_trunc('HOUR', ts) AS hour_start, COUNT(*) AS hits,
       COUNT(DISTINCT host) AS visitors
FROM weblogs.access_logs GROUP BY date_trunc('HOUR', ts)""")
dataset("anomaly_hours_labelled", sql="""SELECT concat(cast(date AS string), ' ', lpad(cast(hour AS string), 2, '0'), ':00') AS hour_label,
       date, hour, hits, normal_hits, std_hits, z, kind
FROM weblogs.anomaly_hours""")
dataset("daily_with_z", sql="""WITH s AS (SELECT avg(hits) AS mu, stddev_pop(hits) AS sd FROM weblogs.daily)
SELECT d.date, d.hits, round(s.mu, 0) AS normal_hits, round((d.hits - s.mu) / s.sd, 2) AS z,
       CASE WHEN abs((d.hits - s.mu) / s.sd) > 3 THEN d.hits END AS anomaly_hits
FROM weblogs.daily d CROSS JOIN s""")
dataset("ml_robustness_summary", sql="""SELECT model, round(avg(f1), 4) AS f1_mean, round(stddev_samp(f1), 4) AS f1_std,
       round(avg(precision), 4) AS precision_mean, round(avg(recall), 4) AS recall_mean,
       round(avg(roc_auc), 4) AS roc_auc_mean, round(avg(pr_auc), 4) AS pr_auc_mean
FROM weblogs.ml_robustness GROUP BY model""")
dataset("ml_crossdataset_summary", sql="""SELECT experiment, model, round(avg(f1), 4) AS f1_mean, round(stddev_samp(f1), 4) AS f1_std,
       round(avg(precision), 4) AS precision_mean, round(avg(recall), 4) AS recall_mean,
       round(avg(false_alarms), 0) AS false_alarms_mean
FROM weblogs.ml_crossdataset GROUP BY experiment, model""")
dataset("top_automated_hosts", sql="""SELECT host, n_requests, round(head_ratio * 100, 1) AS head_pct,
       round(image_ratio * 100, 1) AS image_pct, med_gap AS median_gap_s, n_sessions,
       round(error_ratio * 100, 1) AS error_pct, hit_robots
FROM weblogs.host_clusters WHERE segment = 'automated'""")
print(f"[superset] {len(DATASETS)} datasets")

# ---------------------------------------------------------------- 1. Traffic overview
D = 1
big(D, "1. Requests", "daily", m("SUM(hits)", "Requests"), "requests in July + August 1995")
big(D, "1. Unique hosts", "access_logs", m("COUNT(DISTINCT host)", "Hosts"), "distinct client hosts")
big(D, "1. Data served", "access_logs", m("SUM(bytes) / 1073741824", "GB"), "GB sent to clients", fmt=",.2f")
big(D, "1. Days", "daily", m("COUNT(*)", "Days"), "days of logs (Parquet partitions)")
line(D, "1. Requests per day", "daily", "date", [m("SUM(hits)", "Requests"), m("SUM(visitors)", "Unique hosts")],
     width=12, height=55, markers=True)
bar(D, "1. Requests by hour of day (New York time)", "by_hour", "hour", [m("SUM(hits)", "Requests")], width=6,
    x_title="Hour of day", values=False)
bar(D, "1. Requests by weekday", "by_weekday",
    col("CASE dow WHEN 'Monday' THEN '1 Mon' WHEN 'Tuesday' THEN '2 Tue' WHEN 'Wednesday' THEN '3 Wed' "
        "WHEN 'Thursday' THEN '4 Thu' WHEN 'Friday' THEN '5 Fri' WHEN 'Saturday' THEN '6 Sat' ELSE '7 Sun' END", "Weekday"),
    [m("SUM(hits)", "Requests")], width=6)
bar(D, "1. Top 15 URLs", "top_urls", "endpoint", [m("SUM(hits)", "Requests")], width=7, height=60, horizontal=True,
    sort_x=False, sort_by_metric=True, limit=15)
pie(D, "1. Requests by file type", "by_content_type", col("CASE WHEN ext = '' THEN '(none)' ELSE ext END", "Type"),
    m("SUM(hits)", "Requests"), width=5, height=60)

# ---------------------------------------------------------------- 2. Errors and performance
D = 2
big(D, "2. Error rate", "status_distribution", m("SUM(CASE WHEN status >= 400 THEN hits END) / SUM(hits)", "Errors"),
    "share of requests answered 4xx or 5xx", fmt=".2%")
big(D, "2. Not found (404)", "status_distribution", m("SUM(CASE WHEN status = 404 THEN hits END)", "404s"),
    "requests for pages that do not exist")
big(D, "2. Median response", "size_percentiles", m("MAX(p50)", "Bytes"), "bytes (p50); p99 in the table below")
big(D, "2. Server errors (5xx)", "status_distribution", m("COALESCE(SUM(CASE WHEN status >= 500 THEN hits END), 0)", "5xx"),
    "requests the server failed to answer")
pie(D, "2. HTTP status codes", "status_distribution", col("cast(status AS string)", "Status"), m("SUM(hits)", "Requests"),
    width=4, height=55)
line(D, "2. Daily error rate (%)", "errors_daily", "date",
     [m("AVG(pct_404)", "404 %"), m("AVG(pct_5xx)", "5xx %")], width=8, height=55, fmt=".2f", markers=True)
table(D, "2. Most requested broken links (404)", "broken_links", ["endpoint", "hits", "distinct_visitors"], width=6,
      height=60, order=[("hits", False)])
bar(D, "2. Response sizes", "size_buckets",
    col("CASE bucket WHEN '0 (no body)' THEN '1. 0 B (no body)' WHEN '<1 KB' THEN '2. < 1 KB' WHEN '1-10 KB' THEN '3. 1-10 KB' "
        "WHEN '10-100 KB' THEN '4. 10-100 KB' WHEN '100 KB-1 MB' THEN '5. 100 KB-1 MB' ELSE '6. > 1 MB' END", "Size"),
    [m("SUM(hits)", "Requests")], width=6, height=60)
table(D, "2. Response size percentiles (bytes)", "size_percentiles",
      ["p50", "p90", "p95", "p99", "max_bytes", "mean_bytes"], width=6, height=50)
table(D, "2. Heaviest clients (MB downloaded)", "heaviest_hosts", ["host", "hits", "mb"], width=6, height=50,
      order=[("mb", False)], page=8)

# ---------------------------------------------------------------- 3. Anomalies
D = 3
big(D, "3. Unusual hours", "anomaly_hours", m("COUNT(*)", "Hours"), "hours with |z| > 3 for their hour of day")
big(D, "3. Highest z-score", "anomaly_hours", m("MAX(z)", "z"), "standard deviations above normal", fmt=".2f")
big(D, "3. Unusual days", "anomaly_days", m("COUNT(*)", "Days"), "days with |z| > 3")
big(D, "3. Peak hour requests", "anomaly_hours", m("MAX(hits)", "Requests"), "requests in the busiest unusual hour")
line(D, "3. Requests per hour, whole period", "hourly_traffic", "hour_start", [m("SUM(hits)", "Requests per hour")],
     grain="PT1H", width=12, height=55, area=True)
bar(D, "3. Unusual hours: actual vs normal", "anomaly_hours_labelled", "hour_label",
    [m("SUM(hits)", "Actual requests"), m("SUM(normal_hits)", "Normal for that hour")], width=7, height=60,
    fmt=",d", legend=True)
table(D, "3. Unusual hours (Z-score > 3)", "anomaly_hours_labelled",
      ["hour_label", "hits", "normal_hits", "std_hits", "z", "kind"], width=5, height=60, order=[("z", False)], page=12)
line(D, "3. Daily requests and the anomaly day", "daily_with_z", "date",
     [m("SUM(hits)", "Requests"), m("SUM(normal_hits)", "Mean"), m("SUM(anomaly_hits)", "Anomaly (|z| > 3)")],
     width=12, height=50, markers=True)

# ---------------------------------------------------------------- 4. Bot detection
D = 4
big(D, "4. Hosts clustered", "bot_summary", m("MAX(hosts_clustered)", "Hosts"), "hosts with at least 20 requests")
big(D, "4. Automated hosts", "bot_summary", m("MAX(hosts_flagged)", "Hosts"), "GMM component labelled automated")
big(D, "4. Share of requests", "bot_summary", m("MAX(pct_requests_flagged) / 100", "Share"), "of requests from automated hosts", fmt=".2%")
big(D, "4. Lift on known crawlers", "bot_summary", m("MAX(lift_vs_random)", "Lift"), "x more robots.txt requesters than random", fmt=".1f")
pie(D, "4. Hosts by segment", "host_clusters", "segment", m("COUNT(*)", "Hosts"), width=4, height=55)
pie(D, "4. Requests by segment", "host_clusters", "segment", m("SUM(n_requests)", "Requests"), width=4, height=55)
bar(D, "4. Behaviour of the 3 GMM components", "bot_clusters",
    col("concat('component ', cast(prediction AS string), ' - ', role)", "Component"),
    [m("AVG(head_pct)", "HEAD requests %"), m("AVG(image_pct)", "Images %"), m("AVG(median_gap_s)", "Median gap (s)")],
    width=4, height=55, fmt=",.1f", legend=True)
table(D, "4. The 3 GMM components", "bot_clusters",
      ["prediction", "role", "hosts", "pct_hosts", "requests", "pct_requests", "head_pct", "median_gap_s", "image_pct",
       "sessions", "err_pct", "known_crawlers"], width=12, height=35, order=[("prediction", True)])
table(D, "4. Automated hosts (most requests first)", "top_automated_hosts",
      ["host", "n_requests", "head_pct", "image_pct", "median_gap_s", "n_sessions", "error_pct", "hit_robots"],
      width=12, height=60, order=[("n_requests", False)], page=15)

# ---------------------------------------------------------------- 5. Attack detection (ML)
D = 5
OURS = flt("model = 'Factorization Machine (ours)'")
big(D, "5. F1 of our model", "ml_comparison", m("MAX(f1)", "F1"), "Factorization Machine, 261,076 test requests",
    fmt=".4f", filters=[OURS])
big(D, "5. Attacks caught", "ml_comparison", m("MAX(recall)", "Recall"), "recall of the Factorization Machine",
    fmt=".1%", filters=[OURS])
big(D, "5. False alarms", "ml_comparison", m("MAX(false_alarms)", "FP"), "normal requests flagged (of ~253,000)",
    filters=[OURS])
big(D, "5. Features", "ml_summary", m("MAX(features)", "Features"), "character 1-3-grams + 4 counts")
bar(D, "5. F1 of the 8 models (same test set)", "ml_comparison", "model", [m("MAX(f1)", "F1")], width=6, height=60,
    horizontal=True, sort_x=False, sort_by_metric=True, fmt=".4f")
cid = bar(D, "5. Precision and recall", "ml_comparison", "model",
          [m("MAX(precision)", "Precision"), m("MAX(recall)", "Recall")], width=6, height=60, fmt=".3f", legend=True,
          sort_x=False, sort_by_metric=True, horizontal=True, values=False)
pivot(D, "5. Confusion matrix (select a model)", "ml_confusion", ["actual"], ["predicted"],
      m("SUM(requests)", "Requests"), width=4, height=40)
table(D, "5. All 8 models", "ml_comparison",
      ["rank", "model", "accuracy", "precision", "recall", "f1", "false_alarms", "missed_attacks", "train_seconds"],
      width=8, height=40, order=[("rank", True)])
bar(D, "5. Stability: F1 on 5 random splits", "ml_robustness", col("concat('split ', cast(seed AS string))", "Split"),
    [m("MAX(f1)", "F1")], groupby=["model"], width=6, height=55, fmt=".4f")
table(D, "5. Stability summary (mean of 5 splits)", "ml_robustness_summary",
      ["model", "f1_mean", "f1_std", "precision_mean", "recall_mean", "roc_auc_mean", "pr_auc_mean"], width=6,
      height=25, order=[("f1_mean", False)])
table(D, "5. McNemar's test: FM vs each linear model", "ml_mcnemar",
      ["seed", "compared_with", "only_fm_right", "only_other_right", "p_value", "significant"], width=6, height=50,
      order=[("seed", True)], page=10)
bar(D, "5. Across datasets: mean F1 over 5 seeds", "ml_crossdataset_summary", "experiment", [m("MAX(f1_mean)", "F1")],
    groupby=["model"], width=6, height=55, fmt=".3f", sort_x=True)
table(D, "5. Across datasets (mean of 5 seeds)", "ml_crossdataset_summary",
      ["experiment", "model", "f1_mean", "f1_std", "precision_mean", "recall_mean", "false_alarms_mean"], width=6,
      height=55, order=[("experiment", True), ("f1_mean", False)])
table(D, "5. Settings checked on the validation split", "ml_tuning",
      ["model", "setting", "validation_f1", "train_seconds", "chosen"], width=6, height=50,
      order=[("model", True), ("validation_f1", False)], page=12)
pie(D, "5. NASA URLs flagged: what they really are", "ml_nasa_audit", "category", m("SUM(urls)", "URLs"), width=6, height=50)
table(D, "5. NASA URLs flagged by the FM (checked by hand)", "ml_nasa_flagged",
      ["endpoint", "category", "hits", "hosts", "p_attack", "attack_signature"], width=12, height=60,
      order=[("hits", False)], page=10)

conf_chart = [c[1] for c in CHARTS if c[4].startswith("5. Confusion")]
select_filter(5, "Model (confusion matrix)", "ml_confusion", "model", default="Factorization Machine (ours)",
              targets=conf_chart)

print(f"[superset] {len(CHARTS)} charts")
INTRO = {
    1: "### Traffic overview\nNASA Kennedy Space Center web server, July–August 1995. Every chart is a **Spark SQL** query "
       "through the Spark Thrift Server on the Parquet tables in **HDFS** (`weblogs.access_logs` = 3,461,612 cleaned requests, "
       "and the report tables written by `jobs/reports.py`).",
    2: "### Errors and performance\nHTTP status codes, missing pages and response sizes, from the Spark SQL reports "
       "`status_distribution`, `errors_daily`, `broken_links`, `size_buckets`, `size_percentiles` and `heaviest_hosts`.",
    3: "### Anomaly detection (Z-score)\nEach hour is compared with the same hour of day across all 58 days: "
       "z = (requests − mean) / standard deviation; |z| > 3 is flagged (`jobs/anomalies.py`). "
       "11 of the 12 unusual hours fall on the morning of **13 July 1995**, the STS-70 Space Shuttle launch.",
    4: "### Bot detection (Gaussian Mixture Model, Spark MLlib)\nHosts with ≥ 20 requests are described by 9 behaviour "
       "features (HEAD share, pacing, image share, sessions, …) and clustered by a GMM with 3 components; the component "
       "with the most HEAD requests, the slowest pacing and the fewest images is labelled automated. No labels are used; "
       "hosts that requested `/robots.txt` are held out to check it (`jobs/bot_detection.py`).",
    5: "### Attack detection (Spark MLlib)\nSupervised binary classification (normal / attack) of HTTP requests, trained on "
       "1.3 million labelled requests (HttpParams + Fwaf). Our model is a **Factorization Machine**, compared with 7 MLlib "
       "models on the same 261,076 unseen test requests, on 5 random splits (McNemar's test) and across datasets. "
       "Inside one dataset the linear SVM is best; trained on Fwaf and tested on HttpParams, the FM transfers best.",
}
TITLES = {1: ("1 · Traffic overview", "traffic"), 2: ("2 · Errors and performance", "errors"),
          3: ("3 · Anomaly detection (Z-score)", "anomalies"), 4: ("4 · Bot detection (GMM)", "bots"),
          5: ("5 · Attack detection (ML)", "attacks")}
for n, (title, slug) in TITLES.items():
    dashboard(n, title, slug, INTRO[n])
print("[superset] done")
