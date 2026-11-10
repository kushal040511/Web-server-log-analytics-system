"""The BI layer: ML tables for the dashboards, the Spark Thrift Server tables, and the dashboard definitions."""
import os, re, subprocess, sys, time, socket, shutil
import pandas as pd
import pytest
from conftest import run_job, ROOT

SQL = os.path.join(ROOT, "sql", "bi_tables.sql")
BUILD = os.path.join(ROOT, "superset", "build_dashboards.py")


def sql_tables():
    return re.findall(r"CREATE TABLE (\w+) USING parquet", open(SQL).read())


@pytest.fixture(scope="session")
def published(ml, pipeline):
    """make ml-audit + make ml-publish on the test lake."""
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "audit_nasa_flags.py"),
                    str(ml["out"] / "ml" / "ml_results.json")], check=True, capture_output=True)
    log = run_job("publish_ml_tables.py", ml["out"] / "ml", pipeline["reports"])
    return log


def test_ml_tables_published(published, pipeline, ml):
    rep = pipeline["reports"]
    comp = pd.read_parquet(rep / "ml_comparison")
    conf = pd.read_parquet(rep / "ml_confusion")
    assert len(comp) == len(ml["results"]["results"]) == 3
    assert list(comp.sort_values("rank")["f1"]) == sorted(comp["f1"], reverse=True)
    assert len(conf) == 4 * len(comp) and set(conf["cell"]) == {"TN", "FP", "FN", "TP"}
    for model, g in conf.groupby("model"):                     # the 4 cells add up to the test set
        assert g["requests"].sum() == ml["results"]["test"]
        for actual, a in g.groupby("actual"):
            assert abs(a["pct_of_actual"].sum() - 100) < 0.1
    rob = pd.read_parquet(rep / "ml_robustness")
    assert len(rob) == 6 and len(pd.read_parquet(rep / "ml_mcnemar")) == 4
    assert len(pd.read_parquet(rep / "ml_crossdataset")) == 12
    tune = pd.read_parquet(rep / "ml_tuning")
    assert tune["chosen"].sum() == 3                           # one chosen setting per model
    assert pd.read_parquet(rep / "ml_summary")["classes"][0] == 2


def test_dashboards_only_use_registered_tables():
    """Every physical dataset and every weblogs.* table in a virtual dataset is in sql/bi_tables.sql."""
    src = open(BUILD).read()
    tables = set(sql_tables())
    block = re.search(r"for t in \[(.*?)\]:\s*\n\s*dataset\(t\)", src, re.S).group(1)
    physical = set(re.findall(r'"(\w+)"', block))
    virtual = set(re.findall(r"weblogs\.(\w+)", src))
    assert physical <= tables, physical - tables
    assert virtual <= tables, virtual - tables
    used = set(re.findall(r'(?:big|bar|line|pie|table|pivot|chart)\(D, "[^"]+", "(\w+)"', src))
    defined = physical | set(re.findall(r'dataset\("(\w+)", sql=', src))
    assert used <= defined, used - defined


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def thrift(published, pipeline, tmp_path_factory):
    """A real Spark Thrift Server (local mode) with sql/bi_tables.sql applied, as `make bi-tables` does."""
    import pyspark
    home = os.path.dirname(pyspark.__file__)
    work = tmp_path_factory.mktemp("thrift")
    port = free_port()
    env = dict(os.environ, SPARK_HOME=home, PYSPARK_PYTHON=sys.executable)
    srv = subprocess.Popen(
        [os.path.join(home, "bin", "spark-submit"), "--class", "org.apache.spark.sql.hive.thriftserver.HiveThriftServer2",
         "--master", "local[2]", "--driver-memory", "1g", "--conf", "spark.ui.enabled=false",
         "--conf", "spark.sql.session.timeZone=America/New_York",
         "--conf", f"spark.hadoop.javax.jdo.option.ConnectionURL=jdbc:derby:;databaseName={work}/metastore_db;create=true",
         "--conf", f"spark.sql.warehouse.dir={work}/warehouse",
         "--hiveconf", f"hive.server2.thrift.port={port}", "spark-internal"],
        cwd=work, env=env, stdout=open(work / "thrift.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()
            break
        except OSError:
            time.sleep(1)
    else:
        srv.kill()
        pytest.fail("Spark Thrift Server did not start: " + open(work / "thrift.log").read()[-2000:])

    def beeline(*args):
        return subprocess.run([os.path.join(home, "bin", "beeline"), "-u", f"jdbc:hive2://127.0.0.1:{port}",
                               "--silent=true", "--outputformat=csv2", *args],
                              env=env, capture_output=True, text=True, timeout=600)

    reg = beeline("--force=true", "--hivevar", f"BASE=file://{pipeline['dir']}", "-f", SQL)
    yield dict(beeline=beeline, register=reg)
    srv.terminate()
    srv.wait(30)


def test_thrift_server_serves_the_tables(thrift, pipeline):
    out = thrift["beeline"]("-e", "SHOW TABLES IN weblogs").stdout
    registered = set(re.findall(r"weblogs,(\w+),", out))
    expected = {"access_logs", "daily", "by_hour", "top_urls", "status_distribution", "anomaly_hours",
                "ml_comparison", "ml_confusion", "ml_robustness", "ml_tuning"}
    assert expected <= registered, expected - registered
    n = pd.read_parquet(pipeline["curated"]).shape[0]
    count = thrift["beeline"]("-e", "SELECT COUNT(*) FROM weblogs.access_logs").stdout.split()
    assert int(count[-1]) == n                                   # partitions were discovered (MSCK REPAIR)


def test_dashboard_queries_run_on_spark(thrift):
    """The virtual datasets of the dashboards are valid Spark SQL on the registered tables."""
    src = open(BUILD).read()
    for name, sql in re.findall(r'dataset\("(\w+)", sql="""(.*?)"""\)', src, re.S):
        if not re.search(r"weblogs\.(ml_crossdataset|ml_robustness|host_clusters|daily|access_logs|anomaly_hours)\b", sql):
            continue
        r = thrift["beeline"]("-e", f"SELECT COUNT(*) FROM ({sql}) q")
        if "host_clusters" in sql:                               # bot tables are not in this lake
            continue
        assert r.returncode == 0 and "Error" not in r.stderr, (name, r.stderr[-800:])
