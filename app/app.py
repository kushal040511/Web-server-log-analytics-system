#!/usr/bin/env python3
"""
LogLens: the application frontend of the Log Analytics System (port 3000).

The app is the shell: navigation, a home page with live headline numbers, the attack
checker and the pipeline page. Every chart is an Apache Superset dashboard embedded with a
guest token (Superset embedded mode); every number is a Spark query on HDFS; the attack
checker runs the saved Spark ML model. The app draws no charts itself.

    docker compose up -d app        ->  http://localhost:3000
"""
import os, time, collections
from flask import Flask, render_template, jsonify, request, abort
import engine

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

PAGES = {   # slug: (title, Superset dashboard slug, what the page answers)
    "traffic": ("Traffic", "traffic", "How much traffic did the server get, when, and for which pages?"),
    "errors": ("Errors & performance", "errors", "Which requests failed, which pages are missing, how big are the responses?"),
    "anomalies": ("Anomaly detection", "anomalies", "Which hours were far busier than normal for that hour of day (Z-score > 3)?"),
    "bots": ("Bot detection", "bots", "Which clients behave like crawlers? Gaussian Mixture Model on 9 behaviour features."),
    "attacks": ("Model performance", "attacks", "How well does the Factorization Machine detect web attacks, against 7 other Spark MLlib models?"),
}
NAV = [("home", "Dashboard", "/", "grid"), ("check", "Check a request", "/check", "shield"),
       ("traffic", "Traffic", "/traffic", "activity"), ("errors", "Errors & performance", "/errors", "alert"),
       ("anomalies", "Anomaly detection", "/anomalies", "zap"), ("bots", "Bot detection", "/bots", "cpu"),
       ("attacks", "Model performance", "/attacks", "target"), ("pipeline", "Data & pipeline", "/pipeline", "database"),
       ("about", "About the project", "/about", "info")]
EXAMPLES = [
    ("Normal search", "/htbin/wais.pl?apollo+13+mission"),
    ("Normal page", "/shuttle/missions/sts-71/mission-sts-71.html"),
    ("SQL injection", "/products.php?id=1' UNION SELECT username, password FROM users--"),
    ("Cross-site scripting", "/search?q=<script>alert(document.cookie)</script>"),
    ("Path traversal", "/download.php?file=../../../../etc/passwd"),
    ("Command injection", "/ping.php?host=127.0.0.1; cat /etc/shadow"),
]
RECENT = collections.deque(maxlen=8)


@app.context_processor
def inject():
    return {"nav": NAV, "status": engine.STATUS, "superset_public": engine.SUPERSET_PUBLIC}


@app.template_filter("num")
def num(v, digits=0):
    if v is None:
        return "–"
    return f"{v:,.{digits}f}"


@app.route("/")
def home():
    return render_template("home.html", page="home", k=engine.KPIS, recent=list(RECENT), examples=EXAMPLES)


@app.route("/<slug>")
def dashboard_page(slug):
    if slug not in PAGES:
        abort(404)
    title, dash, question = PAGES[slug]
    return render_template("dashboard.html", page=slug, title=title, dash=dash, question=question)


@app.route("/check", methods=["GET", "POST"])
def check():
    result, error = None, None
    text = request.form.get("request", "") if request.method == "POST" else request.args.get("q", "")
    if text:
        try:
            result = engine.score(text)
            RECENT.appendleft({**result, "at": time.strftime("%H:%M:%S")})
        except Exception as e:
            error = str(e)
    return render_template("check.html", page="check", text=text, result=result, error=error,
                           examples=EXAMPLES, recent=list(RECENT), k=engine.KPIS)


@app.route("/pipeline")
def pipeline():
    return render_template("pipeline.html", page="pipeline", zones=engine.hdfs_summary(), k=engine.KPIS)


@app.route("/about")
def about():
    return render_template("about.html", page="about")


# ------------------------------------------------------------------ API
@app.route("/api/guest-token/<slug>")
def api_guest_token(slug):
    allowed = {p[1] for p in PAGES.values()} | {"home"}
    if slug not in allowed:
        abort(404)
    try:
        return jsonify(engine.guest_token(slug))
    except Exception as e:
        return jsonify({"error": f"Superset is not ready: {type(e).__name__}", "fix": "run: make bi"}), 503


@app.route("/api/check", methods=["POST"])
def api_check():
    text = (request.get_json(silent=True) or {}).get("request", "")
    try:
        return jsonify(engine.score(text))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/status")
def api_status():
    return jsonify({"status": engine.STATUS, "kpis": engine.KPIS})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    engine.warm_up()
    return jsonify({"ok": True})


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    engine.warm_up()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)), threaded=True)
