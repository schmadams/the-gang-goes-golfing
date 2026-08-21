# target path: Dockerfile (new file -- replaces Dockerfile.backend and
# Dockerfile.frontend, which can be deleted from the repo)
#
# One container, one Railway service, running both processes:
#   - uvicorn (the FastAPI backend) on 127.0.0.1:8000 -- loopback only,
#     never reachable from outside the container. Nothing needs to reach
#     it except the frontend process sitting right next to it.
#   - gunicorn (the Dash frontend) on 0.0.0.0:$PORT -- the one thing
#     Railway actually routes public traffic to.
#
# frontend/src/config.py's API_BASE_URL already defaults to
# http://127.0.0.1:8000 (that's always been the local-dev default, since
# `uv run invoke dev_all` runs both processes on one machine too) -- which
# is exactly correct here as well, so no API_BASE_URL environment
# variable needs setting in Railway at all. One service, one set of env
# vars: t3g_sbdb_URL, t3g_sbdb_KEY, UK_GOLF_API_KEY, SECRET_KEY.
#
# Named exactly "Dockerfile" (not Dockerfile.backend/.frontend) so
# Railway's build auto-detection picks it up on its own -- no "Builder"
# dropdown or "Dockerfile Path" field to configure by hand.
FROM python:3.11-slim

WORKDIR /app

# hatchling's [tool.hatch.build.targets.wheel] packages = ["backend"]
# needs backend/ present on disk to build a valid wheel, so it has to be
# copied in before `pip install .` runs.
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .

COPY frontend ./frontend

ENV DASH_DEBUG=false

# app.py's own imports (layouts.navbar, pages.*, config, etc.) are
# relative to frontend/src -- same directory `uv run python
# frontend/src/app.py` runs from locally, gunicorn just needs to start
# from the same place. backend was already pip-installed above as a real
# package, so it's importable from here regardless of working directory.
WORKDIR /app/frontend/src

# uvicorn is backgrounded (&) and gunicorn takes over as the container's
# main process via exec -- Docker's stop signal (SIGTERM) goes to
# gunicorn, which shuts down cleanly; uvicorn goes with the container when
# it stops. Good enough for a low-traffic app like this one -- a proper
# process supervisor (tini, supervisord) would be more textbook-correct
# but is unnecessary complexity for two processes.
CMD uvicorn backend.main:app --host 127.0.0.1 --port 8000 & \
    exec gunicorn app:server --bind 0.0.0.0:${PORT:-8050} --workers 2 --threads 4 --timeout 60