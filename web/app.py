#!/usr/bin/env python3
"""
Log Analytics — web application.

The report pages read ./output/dashboard.json, which Spark wrote. They have no
database connection and no cluster dependency, so they cannot produce a wrong
answer and they keep working when the cluster is stopped.
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
        "nav": [("index", "Overview", "/"), ("traffic", "Traffic", "/traffic"),
                ("errors", "Errors", "/errors"), ("performance", "Performance", "/performance")],
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


@app.route("/healthz")
def healthz():
    d = load()
    return jsonify({
        "status": "ok",
        "data_present": d is not None,
        "data_path": DATA,
        "generated_at": (d or {}).get("generated_at"),
        "records": (d or {}).get("meta", {}).get("records"),
        "downloadable_tables": len(available_tables()),
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    print(f"[web] Log Analytics on http://0.0.0.0:{port}   (data: {DATA})")
    app.run(host="0.0.0.0", port=port, threaded=True)
