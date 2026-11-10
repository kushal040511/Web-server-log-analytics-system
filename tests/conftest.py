"""
End-to-end test fixtures.

The real jobs (jobs/*.py) are run unchanged, as separate processes, on a small
synthetic log with known edge cases. Local Spark replaces the cluster and local
folders replace HDFS; every path is a command-line argument of the job, so no
code is patched.
"""
import os, sys, random, subprocess, shutil, json
from datetime import datetime, timedelta
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(ROOT, "jobs")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

# Same session settings as conf/spark-defaults.conf, minus the cluster addresses.
SPARK_ARGS = ("--master local[2] --driver-memory 2g "
              "--conf spark.sql.session.timeZone=America/New_York "
              "--conf spark.sql.shuffle.partitions=4 "
              "--conf spark.ui.enabled=false pyspark-shell")

# Hand-written lines with a known correct parse.
KNOWN = [
    # (line, expected or None for a reject)
    ('199.72.81.55 - - [01/Jul/1995:00:00:01 -0400] "GET /history/apollo/ HTTP/1.0" 200 6245',
     dict(host="199.72.81.55", method="GET", endpoint="/history/apollo/", protocol="HTTP/1.0",
          status=200, bytes=6245, hour=0, date="1995-07-01", ext="")),
    ('unicomp6.unicomp.net - - [01/Jul/1995:00:00:06 -0400] "GET /shuttle/countdown/ HTTP/1.0" 304 -',
     dict(host="unicomp6.unicomp.net", status=304, bytes=0, hour=0)),          # "-" bytes means 0
    ('burger.letters.com - - [01/Jul/1995:23:59:59 -0400] "GET /images/NASA-logosmall.GIF HTTP/1.0" 200 786',
     dict(hour=23, date="1995-07-01", ext="gif")),                            # local time, not UTC
    ('pipe1.nyc.pipeline.com - - [01/Jul/1995:00:22:43 -0400] "GET /shuttle/missions/sts-71/movies/sts-71-mir-dock.mpg" 200 946425',
     dict(endpoint="/shuttle/missions/sts-71/movies/sts-71-mir-dock.mpg", protocol="", ext="mpg")),  # no protocol
    ('alyssa.p', None),                                                         # the real NASA reject
]


def synthetic_log(path, n=6000, seed=1):
    """KNOWN lines + n random requests over 21 days, with one burst hour."""
    rnd = random.Random(seed)
    urls = ["/", "/history/apollo/", "/shuttle/countdown/", "/images/KSC-logosmall.gif",
            "/images/NASA-logosmall.gif", "/shuttle/missions/sts-71/mission-sts-71.html",
            "/cgi-bin/imagemap/countdown?99,176", "/htbin/wais.pl?select+transcript", "/missing.html"]
    hosts = [f"host{i}.example.com" for i in range(60)] + ["199.72.81.55"]
    start = datetime(1995, 7, 1)
    lines = [k for k, _ in KNOWN]
    for _ in range(n):
        t = start + timedelta(seconds=rnd.randrange(21 * 86400))
        url = rnd.choice(urls)
        status = 404 if url == "/missing.html" else rnd.choice([200] * 8 + [304])
        size = "-" if status in (304, 404) else str(rnd.randrange(100, 60000))
        lines.append(f'{rnd.choice(hosts)} - - [{t:%d/%b/%Y:%H:%M:%S} -0400] "GET {url} HTTP/1.0" {status} {size}')
    for _ in range(600):                      # burst: 3 Jul 1995, 14:00-14:59
        t = datetime(1995, 7, 3, 14) + timedelta(seconds=rnd.randrange(3600))
        lines.append(f'{rnd.choice(hosts)} - - [{t:%d/%b/%Y:%H:%M:%S} -0400] "GET / HTTP/1.0" 200 7280')
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def run_job(name, *args, env=None):
    e = dict(os.environ, PYSPARK_PYTHON=sys.executable, PYSPARK_DRIVER_PYTHON=sys.executable,
             PYSPARK_SUBMIT_ARGS=SPARK_ARGS, **(env or {}))
    r = subprocess.run([sys.executable, os.path.join(JOBS, name), *map(str, args)],
                       capture_output=True, text=True, env=e, timeout=1800)
    if r.returncode != 0:
        raise AssertionError(f"{name} failed\n--- stdout\n{r.stdout[-4000:]}\n--- stderr\n{r.stderr[-4000:]}")
    return r.stdout


@pytest.fixture(scope="session")
def pipeline(tmp_path_factory):
    """raw -> etl -> reports -> anomalies, exactly as `make skeleton`."""
    d = tmp_path_factory.mktemp("lake")
    raw, curated, reports, out = d / "raw", d / "curated", d / "reports", d / "output"
    raw.mkdir(); out.mkdir()
    n_lines = synthetic_log(raw / "access_log_test")
    logs = {"etl": run_job("etl.py", raw, curated),
            "reports": run_job("reports.py", curated, reports),
            "anomalies": run_job("anomalies.py", curated, reports)}
    return dict(dir=d, raw=raw, curated=curated, reports=reports, out=out, n_lines=n_lines, logs=logs)


@pytest.fixture(scope="session")
def ml(pipeline):
    """ml_preprocess -> ml_train (FM + 2 baselines) on the small labelled fixture."""
    d = pipeline["dir"]
    prepared = d / "prepared"
    logs = {"prep": run_job("ml_preprocess.py", os.path.join(FIXTURES, "attacks"), prepared),
            "tune": run_job("ml_tune.py", prepared, pipeline["out"]),
            "train": run_job("ml_train.py", prepared, pipeline["curated"], pipeline["reports"], pipeline["out"],
                             env={"ONLY": "Factorization,Logistic,SVM", "FM_ITERS": "30"}),
            "cross": run_job("ml_crossdataset.py", os.path.join(FIXTURES, "attacks"), pipeline["out"],
                             env={"SEEDS": "1,2", "FM_ITERS": "30"}),
            "robust": run_job("ml_robustness.py", os.path.join(FIXTURES, "attacks"), pipeline["out"],
                              env={"SEEDS": "1,2", "FM_ITERS": "30"})}
    with open(pipeline["out"] / "ml" / "ml_results.json") as f:
        res = json.load(f)
    return dict(prepared=prepared, results=res, logs=logs, out=pipeline["out"])


@pytest.fixture(scope="session")
def published(ml, pipeline):
    """make ml-audit + make ml-publish on the test lake."""
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "audit_nasa_flags.py"),
                    str(ml["out"] / "ml" / "ml_results.json")], check=True, capture_output=True)
    log = run_job("publish_ml_tables.py", ml["out"] / "ml", pipeline["reports"])
    return log
