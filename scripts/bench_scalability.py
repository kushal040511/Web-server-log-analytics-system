#!/usr/bin/env python3
"""
Scalability benchmark: how does the run time of the ETL and the 11 Spark SQL reports
grow with the amount of log data and with the number of CPU cores?

The real jobs (jobs/etl.py, jobs/reports.py) run unchanged in Spark local mode on
25%, 50%, 100% and 200% of the NASA logs (200% = the two months written twice), with
1 core and with 2 cores. Each setting runs REPEATS times; the median is reported.

    python3 scripts/bench_scalability.py data/ output/bench

Writes output/bench/scalability.json. Needs pyspark 3.5 and Java 11 on the machine.
"""
import json, os, subprocess, sys, time, shutil, statistics, glob

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "output/bench"
SIZES = [float(x) for x in os.environ.get("SIZES", "0.25,0.5,1,2").split(",")]
CORES = [int(x) for x in os.environ.get("CORES", "1,2").split(",")]
REPEATS = int(os.environ.get("REPEATS", 2))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(OUT, "work")


def make_input(frac):
    """The first `frac` of the log lines (frac > 1 repeats the whole log)."""
    d = os.path.join(WORK, f"raw_{frac}")
    if os.path.isdir(d):
        return d
    os.makedirs(d)
    logs = sorted(glob.glob(os.path.join(DATA, "access_log_*")))
    lines = sum(1 for p in logs for _ in open(p, "rb"))
    want = int(lines * frac)
    with open(os.path.join(d, "access_log"), "wb") as w:
        written = 0
        while written < want:
            for p in logs:
                for line in open(p, "rb"):
                    if written >= want:
                        break
                    w.write(line); written += 1
    return d


def run(job, cores, *args):
    env = dict(os.environ, PYSPARK_PYTHON=sys.executable, PYSPARK_DRIVER_PYTHON=sys.executable,
               PYSPARK_SUBMIT_ARGS=f"--master local[{cores}] --driver-memory 3g "
                                   "--conf spark.sql.session.timeZone=America/New_York "
                                   "--conf spark.sql.shuffle.partitions=16 --conf spark.ui.enabled=false pyspark-shell")
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "jobs", job), *args], env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{job} failed:\n{r.stderr[-3000:]}")
    return time.time() - t0, r.stdout


def main():
    os.makedirs(WORK, exist_ok=True)
    results = []
    for frac in SIZES:
        raw = make_input(frac)
        size_mb = sum(os.path.getsize(p) for p in glob.glob(f"{raw}/*")) / 2 ** 20
        for cores in CORES:
            etl_t, rep_t, lines = [], [], None
            for _ in range(REPEATS):
                cur, rep = os.path.join(WORK, "curated"), os.path.join(WORK, "reports")
                shutil.rmtree(cur, ignore_errors=True); shutil.rmtree(rep, ignore_errors=True)
                t, out = run("etl.py", cores, raw, cur); etl_t.append(t)
                lines = json.loads(out[out.index("{"):out.index("}") + 1])["raw_lines"]
                t, _ = run("reports.py", cores, cur, rep); rep_t.append(t)
            e, r = statistics.median(etl_t), statistics.median(rep_t)
            row = {"fraction": frac, "lines": lines, "size_mb": round(size_mb, 1), "cores": cores,
                   "etl_seconds": round(e, 1), "reports_seconds": round(r, 1), "total_seconds": round(e + r, 1),
                   "lines_per_second": round(lines / (e + r)), "runs": REPEATS}
            results.append(row)
            print(f"{frac:>5.2f}x  {lines:>9,} lines  {size_mb:>6.0f} MB  {cores} core(s)   ETL {e:6.1f}s   "
                  f"reports {r:6.1f}s   {row['lines_per_second']:>8,} lines/s", flush=True)
    for frac in SIZES:   # speed-up from 1 to 2 cores at each size
        rs = {r["cores"]: r for r in results if r["fraction"] == frac}
        if 1 in rs and 2 in rs:
            rs[2]["speedup_vs_1_core"] = round(rs[1]["total_seconds"] / rs[2]["total_seconds"], 2)
    info = {"machine": {"cpus": os.cpu_count(), "mode": "Spark local mode, one machine"},
            "note": "median of the runs; time includes Spark start-up (~5-8 s per job)", "results": results}
    json.dump(info, open(os.path.join(OUT, "scalability.json"), "w"), indent=1)
    shutil.rmtree(WORK, ignore_errors=True)
    print("saved", os.path.join(OUT, "scalability.json"))


if __name__ == "__main__":
    main()
