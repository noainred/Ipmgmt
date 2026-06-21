"""gunicorn configuration for the central portal.

Run:  gunicorn -c deploy/gunicorn.conf.py portal.app:app
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
worker_class = "sync"          # the workload is IO-light; sync workers are plenty
timeout = 120                  # ingest batches can be large
graceful_timeout = 30
keepalive = 5

# IMPORTANT: do not preload. portal.app opens its DB connection at import time;
# with preload the connection would be created in the master and shared across
# forked workers (unsafe). With preload_app=False each worker opens its own.
preload_app = False

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
