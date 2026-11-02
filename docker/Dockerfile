# Log Analytics System — one image, several roles
#

FROM eclipse-temurin:11-jdk-jammy

ARG HADOOP_VERSION=3.3.6
ARG SPARK_VERSION=3.5.3

ENV HADOOP_HOME=/opt/hadoop \
    SPARK_HOME=/opt/spark \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    SPARK_CONF_DIR=/opt/spark/conf \
    HDFS_NAMENODE_USER=root \
    HDFS_DATANODE_USER=root \
    PYSPARK_PYTHON=python3 \
    PYSPARK_DRIVER_PYTHON=python3
ENV PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$SPARK_HOME/bin:$SPARK_HOME/sbin

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates procps net-tools less nano python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ---- Hadoop + Spark -----------------------------------------------------------
# archive.apache.org keeps every release but can be very slow, so faster mirrors are
# tried first. A mirror slower than 50 KB/s for 20 s is abandoned and the next one tried.
COPY docker/fetch.sh /usr/local/bin/fetch
RUN sed -i 's/\r$//' /usr/local/bin/fetch && chmod +x /usr/local/bin/fetch

RUN set -eux; \
    fetch "hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz" /tmp/hadoop.tgz; \
    mkdir -p "$HADOOP_HOME"; \
    tar -xzf /tmp/hadoop.tgz -C "$HADOOP_HOME" --strip-components=1; \
    rm /tmp/hadoop.tgz; \
    rm -rf "$HADOOP_HOME"/share/doc

RUN set -eux; \
    fetch "spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3.tgz" /tmp/spark.tgz; \
    mkdir -p "$SPARK_HOME"; \
    tar -xzf /tmp/spark.tgz -C "$SPARK_HOME" --strip-components=1; \
    rm /tmp/spark.tgz

#flask serves the dashboard.
RUN pip3 install --no-cache-dir numpy==1.26.4 flask==3.0.3

# Hadoop's scripts read JAVA_HOME from hadoop-env.sh, not just the environment
RUN echo "export JAVA_HOME=${JAVA_HOME}" >> "$HADOOP_CONF_DIR/hadoop-env.sh" \
 && echo "export HDFS_NAMENODE_USER=root"  >> "$HADOOP_CONF_DIR/hadoop-env.sh" \
 && echo "export HDFS_DATANODE_USER=root"  >> "$HADOOP_CONF_DIR/hadoop-env.sh" \
 && echo "export HDFS_SECONDARYNAMENODE_USER=root" >> "$HADOOP_CONF_DIR/hadoop-env.sh"

COPY docker/entrypoint.sh /entrypoint.sh
# strip Windows line endings, in case the file was saved on Windows
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["client"]
