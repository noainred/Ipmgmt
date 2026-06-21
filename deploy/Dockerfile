# Central portal image. Build from the repo root:
#   docker build -f deploy/Dockerfile -t ipmgmt-portal .
# (The collector is NOT containerised -- it runs as a systemd service on each
#  datacenter host because it needs raw network access for nmap.)
FROM python:3.12-slim

WORKDIR /app

COPY deploy/requirements-portal.txt deploy/requirements-portal.txt
RUN pip install --no-cache-dir -r deploy/requirements-portal.txt

COPY common ./common
COPY portal ./portal
COPY deploy/gunicorn.conf.py ./deploy/gunicorn.conf.py

# Non-root runtime user.
RUN useradd --create-home --uid 10001 portal
USER portal

EXPOSE 8000

# DATABASE_URL, INGEST_API_KEY, ADMIN_TOKEN are provided at runtime (compose/env).
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "portal.app:app"]
