# Apache Superset with the driver for the Spark Thrift Server (HiveServer2 protocol).
FROM apache/superset:4.1.1

USER root
RUN pip install --no-cache-dir "pyhive[hive_pure_sasl]==0.7.0" "thrift==0.16.0"
COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/bootstrap.sh /app/bootstrap.sh
RUN sed -i 's/\r$//' /app/bootstrap.sh && chmod +x /app/bootstrap.sh
USER superset

ENTRYPOINT ["/app/bootstrap.sh"]
