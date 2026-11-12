-- Tables the BI dashboards (Apache Superset) read through the Spark Thrift Server.
-- Each table is a pointer to Parquet files the Spark jobs already wrote to HDFS:
-- nothing is copied, Superset's queries run as Spark SQL jobs on the cluster.
--   make bi-tables   (beeline -f sql/bi_tables.sql --hivevar BASE=hdfs://namenode:8020/data/weblogs)

CREATE DATABASE IF NOT EXISTS weblogs;
USE weblogs;

-- the curated zone: one row per request, partitioned by date
DROP TABLE IF EXISTS access_logs;
CREATE TABLE access_logs USING parquet LOCATION '${hivevar:BASE}/curated';
MSCK REPAIR TABLE access_logs;

-- the 11 Spark SQL reports, the Z-score anomalies and the bot-detection output
DROP TABLE IF EXISTS by_hour;
CREATE TABLE by_hour USING parquet LOCATION '${hivevar:BASE}/reports/by_hour';
DROP TABLE IF EXISTS daily;
CREATE TABLE daily USING parquet LOCATION '${hivevar:BASE}/reports/daily';
DROP TABLE IF EXISTS by_weekday;
CREATE TABLE by_weekday USING parquet LOCATION '${hivevar:BASE}/reports/by_weekday';
DROP TABLE IF EXISTS top_urls;
CREATE TABLE top_urls USING parquet LOCATION '${hivevar:BASE}/reports/top_urls';
DROP TABLE IF EXISTS by_content_type;
CREATE TABLE by_content_type USING parquet LOCATION '${hivevar:BASE}/reports/by_content_type';
DROP TABLE IF EXISTS status_distribution;
CREATE TABLE status_distribution USING parquet LOCATION '${hivevar:BASE}/reports/status_distribution';
DROP TABLE IF EXISTS errors_daily;
CREATE TABLE errors_daily USING parquet LOCATION '${hivevar:BASE}/reports/errors_daily';
DROP TABLE IF EXISTS broken_links;
CREATE TABLE broken_links USING parquet LOCATION '${hivevar:BASE}/reports/broken_links';
DROP TABLE IF EXISTS size_percentiles;
CREATE TABLE size_percentiles USING parquet LOCATION '${hivevar:BASE}/reports/size_percentiles';
DROP TABLE IF EXISTS size_buckets;
CREATE TABLE size_buckets USING parquet LOCATION '${hivevar:BASE}/reports/size_buckets';
DROP TABLE IF EXISTS heaviest_hosts;
CREATE TABLE heaviest_hosts USING parquet LOCATION '${hivevar:BASE}/reports/heaviest_hosts';
DROP TABLE IF EXISTS anomaly_hours;
CREATE TABLE anomaly_hours USING parquet LOCATION '${hivevar:BASE}/reports/anomaly_hours';
DROP TABLE IF EXISTS anomaly_days;
CREATE TABLE anomaly_days USING parquet LOCATION '${hivevar:BASE}/reports/anomaly_days';
DROP TABLE IF EXISTS host_features;
CREATE TABLE host_features USING parquet LOCATION '${hivevar:BASE}/reports/host_features';
DROP TABLE IF EXISTS host_clusters;
CREATE TABLE host_clusters USING parquet LOCATION '${hivevar:BASE}/reports/host_clusters';
DROP TABLE IF EXISTS bot_clusters;
CREATE TABLE bot_clusters USING parquet LOCATION '${hivevar:BASE}/reports/bot_clusters';
DROP TABLE IF EXISTS bot_summary;
CREATE TABLE bot_summary USING parquet LOCATION '${hivevar:BASE}/reports/bot_summary';

-- machine-learning results (jobs/publish_ml_tables.py)
DROP TABLE IF EXISTS ml_summary;
CREATE TABLE ml_summary USING parquet LOCATION '${hivevar:BASE}/reports/ml_summary';
DROP TABLE IF EXISTS ml_comparison;
CREATE TABLE ml_comparison USING parquet LOCATION '${hivevar:BASE}/reports/ml_comparison';
DROP TABLE IF EXISTS ml_confusion;
CREATE TABLE ml_confusion USING parquet LOCATION '${hivevar:BASE}/reports/ml_confusion';
DROP TABLE IF EXISTS ml_robustness;
CREATE TABLE ml_robustness USING parquet LOCATION '${hivevar:BASE}/reports/ml_robustness';
DROP TABLE IF EXISTS ml_mcnemar;
CREATE TABLE ml_mcnemar USING parquet LOCATION '${hivevar:BASE}/reports/ml_mcnemar';
DROP TABLE IF EXISTS ml_crossdataset;
CREATE TABLE ml_crossdataset USING parquet LOCATION '${hivevar:BASE}/reports/ml_crossdataset';
DROP TABLE IF EXISTS ml_tuning;
CREATE TABLE ml_tuning USING parquet LOCATION '${hivevar:BASE}/reports/ml_tuning';
DROP TABLE IF EXISTS ml_nasa_audit;
CREATE TABLE ml_nasa_audit USING parquet LOCATION '${hivevar:BASE}/reports/ml_nasa_audit';
DROP TABLE IF EXISTS ml_nasa_flagged;
CREATE TABLE ml_nasa_flagged USING parquet LOCATION '${hivevar:BASE}/reports/ml_nasa_flagged';

SHOW TABLES;
