#!/usr/bin/env python3
"""
Log Analytics — web application.

Two halves, deliberately separated:

  The report pages read ./output/dashboard.json, which Spark wrote. They have no
  database connection and no cluster dependency, so they cannot produce a wrong
  answer and they keep working when the cluster is stopped.

  The /sql page is the exception: it opens a real SparkSession against the
  cluster and runs your query over the curated Parquet. That needs the cluster
  up, and the page says so plainly when it isn't.

The SparkSession is created lazily on the first query and then reused, so the
second query is fast. Creating it eagerly would hold a driver's worth of cluster
memory for a page nobody may visit.
"""
import json, os, re, io, csv, threading, time
from datetime import datetime, timedelta, timezone
from flask import (Flask, render_template, jsonify, Response, request,
                   send_from_directory, abort)

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.environ.get("OUTPUT_DIR", "/workspace/output")
DATA = os.environ.get("DASHBOARD_JSON", os.path.join(OUTPUT, "dashboard.json"))
TABLES = os.path.join(OUTPUT, "tables")
CURATED = os.environ.get("CURATED", "hdfs://namenode:8020/data/weblogs/curated")

app = Flask(__name__)

# ./web is bind-mounted into the container, so a template or stylesheet edited on
# the host should show up on the next reload. Without these two, Jinja caches the
# compiled template and the browser caches the CSS, and you end up debugging a
# page that no longer exists on disk.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# --------------------------------------------------------------- data access
_cache = {"mtime": 0, "data": None}


def load():
    """Re-read dashboard.json whenever Spark has rewritten it."""
    if not os.path.exists(DATA):
        return None
    m = os.path.getmtime(DATA)
    if _cache["data"] is None or m != _cache["mtime"]:
        with open(DATA) as f:
            _cache["data"] = json.load(f)
        _cache["mtime"] = m
    return _cache["data"]


@app.context_processor
def inject():
    d = load()
    return {
        "has_data": d is not None,
        "meta": (d or {}).get("meta", {}),
        "generated_at": (d or {}).get("generated_at", ""),
        "has_bots": bool((d or {}).get("bots")),
        "nav": [("index", "Overview", "/"), ("logs", "Log explorer", "/logs"),
                ("traffic", "Traffic", "/traffic"),
                ("errors", "Errors", "/errors"), ("performance", "Performance", "/performance"),
                ("bots", "Bot detection", "/bots"), ("ml", "Attack detection", "/ml"),
                ("sql", "SQL", "/sql"),
                ("about", "About", "/about")],
    }


@app.template_filter("longdate")
def longdate(s):
    """1995-07-01 -> 1 July 1995, for prose. Anything unparseable passes through."""
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError):
        return s
    return f"{d.day} {d:%B %Y}"


@app.after_request
def no_cache(resp):
    # Spark rewrites the data underneath us; never let a browser hold a stale copy
    if request.path.startswith("/api/") or request.path.endswith(".json"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# -------------------------------------------------------------------- pages
def page(name, title, sub):
    if load() is None:
        return render_template("nodata.html", page=name, title=title)
    return render_template(f"{name}.html", page=name, title=title, sub=sub)


@app.route("/")
def index():
    return page("index", "Overview", "Everything at a glance")


@app.route("/traffic")
def traffic():
    return page("traffic", "Traffic", "When people came, and what they asked for")


@app.route("/errors")
def errors():
    return page("errors", "Errors", "What failed, and which links to fix first")


@app.route("/performance")
def performance():
    return page("performance", "Performance", "Response weight and the heaviest clients")


@app.route("/bots")
def bots():
    return page("bots", "Bot detection", "Separating automated traffic from human browsing")


@app.route("/ml")
def ml():
    return page("ml", "Attack detection", "Machine learning: Factorization Machine vs 7 Spark MLlib models")


ML_DIR = os.path.join(OUTPUT, "ml")


def ml_file(name):
    """Results written by jobs/ml_train.py and jobs/ml_crossdataset.py (make ml)."""
    p = os.path.join(ML_DIR, name)
    if not os.path.exists(p):
        return jsonify({"error": "no ML results yet", "fix": "run: make ml"}), 404
    with open(p) as f:
        return Response(f.read(), mimetype="application/json")


@app.route("/api/ml")
def api_ml():
    return ml_file("ml_results.json")


@app.route("/api/ml/cross")
def api_ml_cross():
    return ml_file("ml_crossdataset.json")


@app.route("/api/ml/robust")
def api_ml_robust():
    return ml_file("ml_robustness.json")


@app.route("/api/ml/tuning")
def api_ml_tuning():
    return ml_file("ml_tuning.json")


@app.route("/api/ml/audit")
def api_ml_audit():
    return ml_file("nasa_flag_audit.json")


@app.route("/about")
def about():
    return render_template("about.html", page="about", title="About",
                           sub="How this system is built", curated=CURATED)


@app.route("/logs")
def logs():
    return render_template("logs.html", page="logs", title="Log explorer",
                           sub="Search and replay the raw access log, line by line",
                           log_dir=LOG_DIR)


@app.route("/sql")
def sql():
    return render_template("sql.html", page="sql", title="SQL",
                           sub="Query the curated data directly", curated=CURATED)


# ---------------------------------------------------------------------- API
@app.route("/api/data")
@app.route("/dashboard.json")
def api_data():
    d = load()
    if d is None:
        return jsonify({"error": "no data yet",
                        "fix": "run: make skeleton && make dashboard"}), 404
    return Response(json.dumps(d), mimetype="application/json")


SAFE_NAME = re.compile(r"^[a-z0-9_]+$")


@app.route("/api/table/<name>")
def api_table(name):
    if not SAFE_NAME.match(name):
        abort(400)
    p = os.path.join(TABLES, f"{name}.json")
    if not os.path.exists(p):
        return jsonify({"error": f"no table '{name}'",
                        "available": available_tables()}), 404
    with open(p) as f:
        return Response(f.read(), mimetype="application/json")


@app.route("/download/<name>.<fmt>")
def download(name, fmt):
    if not SAFE_NAME.match(name) or fmt not in ("csv", "json"):
        abort(400)
    p = os.path.join(TABLES, f"{name}.{fmt}")
    if not os.path.exists(p):
        abort(404)
    return send_from_directory(TABLES, f"{name}.{fmt}", as_attachment=True,
                               download_name=f"{name}.{fmt}")


def available_tables():
    if not os.path.isdir(TABLES):
        return []
    return sorted({f.rsplit(".", 1)[0] for f in os.listdir(TABLES)})


@app.route("/api/tables")
def api_tables():
    return jsonify(available_tables())


# ------------------------------------------------------- raw log explorer
# Search and replay read the original NCSA files directly, not Spark's output.
# The files are in time order, so any moment in two months of logs is a binary
# search away, and a search is a bounded forward scan from there. Each request
# reads at most SCAN_BUDGET bytes and hands back a cursor, so one broad query
# can never tie the web process up for minutes.
LOG_DIR = os.environ.get("LOG_DIR", os.path.join(os.path.dirname(OUTPUT.rstrip("/")), "data"))
SCAN_BUDGET = 48 * 1024 * 1024
LOG_RE = re.compile(rb'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) [^\]]*\] '
                    rb'"([^"]*)" (\d{3}) (\S+)')
MONTHS = {m: i for i, m in enumerate(
    [b"Jan", b"Feb", b"Mar", b"Apr", b"May", b"Jun", b"Jul", b"Aug", b"Sep", b"Oct", b"Nov", b"Dec"], 1)}
_logs = {"key": None, "files": []}


def parse_line(raw):
    """One NCSA Common Log Format line -> dict, or None for the malformed ones."""
    m = LOG_RE.match(raw)
    if not m:
        return None
    host, dd, mon, yyyy, hh, mi, ss, req, status, size = m.groups()
    parts = req.decode("latin-1").split(" ")
    method, path = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
    return {"ts": datetime(int(yyyy), MONTHS.get(mon, 1), int(dd), int(hh), int(mi), int(ss)),
            "host": host.decode("latin-1"), "method": method, "path": path,
            "status": int(status), "bytes": int(size) if size.isdigit() else 0}


def _first_ts(fh, pos):
    """Timestamp and offset of the first parseable line starting at or after pos."""
    fh.seek(pos)
    if pos:
        fh.readline()          # land on a line boundary
    while True:
        off, line = fh.tell(), fh.readline()
        if not line:
            return None, off
        r = parse_line(line)
        if r:
            return r["ts"], off


def log_files():
    """[(path, first_ts, last_ts, size)] oldest first; recomputed only when the files change."""
    if not os.path.isdir(LOG_DIR):
        return []
    paths = sorted(os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.startswith("access_log"))
    key = tuple((p, os.path.getsize(p), os.path.getmtime(p)) for p in paths)
    if key != _logs["key"]:
        files = []
        for p in paths:
            size = os.path.getsize(p)
            with open(p, "rb") as fh:
                first, _ = _first_ts(fh, 0)
                last, _ = _first_ts(fh, max(0, size - 8192))
                # the tail read gives the first line of the last 8 KB; walk to the end
                fh.seek(max(0, size - 8192)); fh.readline()
                for line in fh:
                    r = parse_line(line)
                    if r:
                        last = r["ts"]
            if first:
                files.append((p, first, last, size))
        _logs["files"], _logs["key"] = sorted(files, key=lambda f: f[1]), key
    return _logs["files"]


def locate(target):
    """(file index, byte offset) of the first line at or after target."""
    files = log_files()
    for fi, (p, first, last, size) in enumerate(files):
        if last < target:
            continue
        with open(p, "rb") as fh:
            lo, hi = 0, size
            while hi - lo > 16384:
                mid = (lo + hi) // 2
                ts, _ = _first_ts(fh, mid)
                if ts is None or ts >= target:
                    hi = mid
                else:
                    lo = mid
            fh.seek(lo)
            if lo:
                fh.readline()
            while True:
                off, line = fh.tell(), fh.readline()
                if not line:
                    break
                r = parse_line(line)
                if r and r["ts"] >= target:
                    return fi, off
    return len(files), 0


def parse_when(s, end=False):
    """'1995-07-13', '1995-07-13T09:30' or '1995-07-13T09:30:15' -> datetime."""
    if not s:
        return None
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, f)
        except ValueError:
            continue
        # An end bound covers the whole unit it names: "to=...T23:59" includes 23:59:59.
        if end and f == "%Y-%m-%d":
            return d.replace(hour=23, minute=59, second=59)
        if end and f == "%Y-%m-%dT%H:%M":
            return d.replace(second=59)
        return d
    return None


def bad_request(error, fix):
    return jsonify({"error": error, "detail": "", "fix": fix}), 400


def int_arg(name, default, lo, hi):
    """Clamped integer query parameter; None when it is present but not a number."""
    raw = request.args.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return None


def row_out(r):
    return [r["ts"].strftime("%Y-%m-%dT%H:%M:%S"), r["host"], r["method"], r["path"], r["status"], r["bytes"]]


def no_logs():
    return jsonify({"error": "No raw log files found.",
                    "detail": f"Looked in {LOG_DIR} for access_log_* files.",
                    "fix": "Download them with: make data"}), 404


@app.route("/api/logs/meta")
def api_logs_meta():
    files = log_files()
    if not files:
        return no_logs()
    return jsonify({"files": [{"name": os.path.basename(p), "from": f.strftime("%Y-%m-%dT%H:%M:%S"),
                               "to": l.strftime("%Y-%m-%dT%H:%M:%S"), "mb": round(s / 1048576, 1)}
                              for p, f, l, s in files]})


@app.route("/api/logs/search")
def api_logs_search():
    files = log_files()
    if not files:
        return no_logs()
    a = request.args
    q = a.get("q", "").strip().lower().encode("latin-1", "ignore")
    host = a.get("host", "").strip().lower()
    path_q = a.get("path", "").strip().lower()
    method = a.get("method", "").strip().upper()
    status = a.get("status", "").strip().lower()
    limit = int_arg("limit", 100, 1, 500)
    if limit is None:
        return bad_request("limit must be a whole number.", "Use a limit between 1 and 500, e.g. limit=100.")
    end = parse_when(a.get("to"), end=True)
    if a.get("to") and end is None:
        return bad_request(f"Could not read the end time '{a.get('to')}'.",
                           "Write times as YYYY-MM-DDTHH:MM, e.g. 1995-07-13T23:59.")
    if a.get("from") and not a.get("cursor") and parse_when(a.get("from")) is None:
        return bad_request(f"Could not read the start time '{a.get('from')}'.",
                           "Write times as YYYY-MM-DDTHH:MM, e.g. 1995-07-13T00:00.")

    if a.get("cursor"):
        try:
            fi, off = (int(x) for x in a["cursor"].split(":"))
            if fi < 0 or off < 0:
                raise ValueError
        except ValueError:
            return bad_request("The search cursor is not valid.",
                               "Start the search again without a cursor.")
    else:
        start = parse_when(a.get("from"))
        fi, off = locate(start) if start else (0, 0)

    def status_ok(code):
        if not status:
            return True
        if status.endswith("xx"):
            return str(code)[0] == status[0]
        return str(code) == status

    started, scanned, lines, rows, last_ts, done = time.time(), 0, 0, [], None, False
    last_line = None
    while fi < len(files) and len(rows) < limit and scanned < SCAN_BUDGET:
        with open(files[fi][0], "rb") as fh:
            fh.seek(off)
            while len(rows) < limit and scanned < SCAN_BUDGET:
                line = fh.readline()
                if not line:
                    fi, off = fi + 1, 0
                    break
                scanned += len(line); lines += 1
                off = fh.tell()
                last_line = line
                if q and q not in line.lower():
                    # cheap reject before the regex, but still honour the end bound now and then
                    if end and lines % 5000 == 0:
                        r = parse_line(line)
                        if r and r["ts"] > end:
                            done = True
                            break
                    continue
                r = parse_line(line)
                if not r:
                    continue
                last_ts = r["ts"]
                if end and r["ts"] > end:
                    done = True
                    break
                if host and host not in r["host"].lower():
                    continue
                if path_q and path_q not in r["path"].lower():
                    continue
                if method and r["method"] != method:
                    continue
                if not status_ok(r["status"]):
                    continue
                rows.append(row_out(r))
        if done:
            break

    exhausted = done or fi >= len(files)
    # A text filter rejects most lines before they are parsed, so last_ts can lag far
    # behind (or be None). The last line read is the true position of the scan.
    if not done and last_line is not None:
        r = parse_line(last_line)
        if r and (last_ts is None or r["ts"] > last_ts):
            last_ts = r["ts"]
    return jsonify({
        "rows": rows, "cursor": None if exhausted else f"{fi}:{off}",
        "scanned_lines": lines, "scanned_mb": round(scanned / 1048576, 1),
        "reached": last_ts.strftime("%Y-%m-%dT%H:%M:%S") if last_ts else None,
        "seconds": round(time.time() - started, 2),
    })


@app.route("/api/logs/window")
def api_logs_window():
    """Every line in [start, start + seconds) — what the live replay plays back."""
    files = log_files()
    if not files:
        return no_logs()
    start = parse_when(request.args.get("start"))
    if not start:
        return jsonify({"error": "start is required, e.g. 1995-07-13T09:00"}), 400
    seconds = int_arg("seconds", 60, 1, 3600)
    if seconds is None:
        return bad_request("seconds must be a whole number.", "Use a window between 1 and 3600 seconds.")
    limit = 20000
    end = start + timedelta(seconds=seconds)
    fi, off = locate(start)
    rows, truncated = [], False
    while fi < len(files):
        with open(files[fi][0], "rb") as fh:
            fh.seek(off)
            for line in fh:
                r = parse_line(line)
                if not r:
                    continue
                if r["ts"] >= end:
                    fi = len(files)
                    break
                rows.append(row_out(r))
                if len(rows) >= limit:
                    truncated, fi = True, len(files)
                    break
            else:
                fi, off = fi + 1, 0
    return jsonify({"rows": rows, "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": end.strftime("%Y-%m-%dT%H:%M:%S"), "truncated": truncated})


# ------------------------------------------------------------- live SQL
_spark = {"session": None, "error": None}
_spark_lock = threading.Lock()
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|grant|revoke|"
    r"refresh|cache|uncache|set|reset|add\s+jar|load\s+data)\b", re.I)


def get_spark():
    """Create the SparkSession on first use, then reuse it."""
    with _spark_lock:
        if _spark["session"] is not None:
            return _spark["session"], None
        try:
            from pyspark.sql import SparkSession
            s = (SparkSession.builder
                 .appName("weblog-sql-console")
                 .config("spark.sql.shuffle.partitions", "8")
                 .config("spark.driver.memory", "1g")
                 .config("spark.executor.memory", "1g")
                 # This session lives as long as the web container, so it must
                 # never hold the whole worker: one 1-core executor leaves room
                 # for a pipeline job's 2-core executor (see SPARK_WORKER_CORES).
                 .config("spark.executor.cores", "1")
                 .config("spark.cores.max", "1")
                 .getOrCreate())
            s.sparkContext.setLogLevel("ERROR")
            s.read.parquet(CURATED).createOrReplaceTempView("access_logs")
            _spark["session"] = s
            return s, None
        except Exception as e:
            _spark["error"] = f"{type(e).__name__}: {e}"
            return None, _spark["error"]


@app.route("/api/query", methods=["POST"])
def api_query():
    q = (request.get_json(silent=True) or {}).get("sql", "").strip().rstrip(";")
    if not q:
        return jsonify({"error": "empty query"}), 400
    # Read-only console. This is a convenience guard, not a security boundary —
    # the cluster has no auth and is not exposed beyond localhost.
    if FORBIDDEN.search(q):
        return jsonify({"error": "This console is read-only. "
                                 "Only SELECT / WITH / SHOW / DESCRIBE are allowed."}), 400
    if not re.match(r"^\s*(select|with|show|describe|desc|explain)\b", q, re.I):
        return jsonify({"error": "Query must start with SELECT, WITH, SHOW, DESCRIBE or EXPLAIN."}), 400

    spark, err = get_spark()
    if spark is None:
        return jsonify({
            "error": "Could not reach the Spark cluster.",
            "detail": err,
            "fix": "The SQL console needs the cluster running: make up",
        }), 503

    started = time.time()
    try:
        df = spark.sql(q).limit(500)
        cols = df.columns
        rows = [[(str(v) if v is not None else None) for v in r] for r in df.collect()]
    except Exception as e:
        msg = str(e).split("\n")[0][:400]
        return jsonify({"error": "Query failed", "detail": msg}), 400
    return jsonify({
        "columns": cols, "rows": rows, "row_count": len(rows),
        "truncated": len(rows) == 500,
        "seconds": round(time.time() - started, 2),
    })


@app.route("/healthz")
def healthz():
    d = load()
    return jsonify({
        "status": "ok",
        "data_present": d is not None,
        "data_path": DATA,
        "generated_at": (d or {}).get("generated_at"),
        "records": (d or {}).get("meta", {}).get("records"),
        "bots_present": bool((d or {}).get("bots")),
        "ml_results_present": os.path.exists(os.path.join(ML_DIR, "ml_results.json")),
        "downloadable_tables": len(available_tables()),
        "spark_session": _spark["session"] is not None,
        "raw_log_files": len(log_files()),
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    print(f"[web] Log Analytics on http://0.0.0.0:{port}   (data: {DATA})")
    app.run(host="0.0.0.0", port=port, threaded=True)
