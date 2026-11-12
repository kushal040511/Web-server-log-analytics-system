# Superset settings for the Log Analytics System (loaded from /app/pythonpath).
import os

# Local demo cluster only. Change both for anything reachable from a network.
SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "loganalytics-demo-secret-change-me-2026")
SQLALCHEMY_DATABASE_URI = "sqlite:////app/superset_home/superset.db"   # Superset's own metadata only

# Dashboards query Spark through the Thrift Server; long Spark SQL jobs need more than the default.
SUPERSET_WEBSERVER_TIMEOUT = 300
SQLLAB_TIMEOUT = 300
SQLLAB_ASYNC_TIME_LIMIT_SEC = 600

# Cache chart results for an hour: the HDFS tables only change when the pipeline runs again.
DATA_CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600}

FEATURE_FLAGS = {"DASHBOARD_CROSS_FILTERS": True, "DASHBOARD_NATIVE_FILTERS": True, "EMBEDDED_SUPERSET": True}

# Embedded mode, for the LogLens app (port 3000): the app's server asks Superset for a
# short-lived guest token that can only view one dashboard; the browser renders that
# dashboard inside the app. Guests get the read-only Gamma role plus access to the datasets
# of that one dashboard, nothing else. Superset may be framed by the app's page.
GUEST_ROLE_NAME = "Gamma"
GUEST_TOKEN_JWT_EXP_SECONDS = 3600
HTTP_HEADERS = {}
WTF_CSRF_ENABLED = False
TALISMAN_ENABLED = False

# build_dashboards.py creates ~90 objects through the REST API in a few seconds; the default
# rate limit (50 requests per second) would refuse some of them.
RATELIMIT_ENABLED = False
