# Script worker (issue #34): configuration scripts behind the /invoke envelope seam, on a Unix socket.
# CPython's standard library only: no pip packages, no hub code but the worker's four files. Each run is a fresh
# `python -I -S -B` subprocess in an empty temporary directory, under hard limits, a kernel filter and an audit-hook
# tripwire (hubproto/script_sandbox.py). docker-compose.yml runs it with no network, a read-only root, every
# capability dropped, no-new-privileges, the seccomp profile beside this file, an unprivileged user and limits.
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HUBPROTO_SCRIPT_SOCKET=/run/script-worker/worker.sock
WORKDIR /worker
COPY hubproto/envelope.py hubproto/scriptcheck.py hubproto/scriptworker.py hubproto/script_sandbox.py /worker/hubproto/
# Nothing for a run to use: no setuid programs, no pip, no shells (the last RUN; CMD and HEALTHCHECK use no shell).
RUN printf '"""script worker package"""\n' > /worker/hubproto/__init__.py \
    && python -m compileall -q /worker/hubproto \
    && useradd --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --uid 10001 worker \
    && mkdir -p /run/script-worker && chown worker:worker /run/script-worker \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
    && rm -rf /usr/local/lib/python3*/site-packages/pip* /usr/local/bin/pip* /usr/local/lib/python3*/ensurepip \
    && rm -f /bin/sh /bin/dash /bin/bash /usr/bin/sh /usr/bin/dash /usr/bin/bash
USER 10001:10001
# Healthy only while the hub could use the socket: there, a socket, the worker's, mode 0666, its directory searchable.
# (A connect would be the plainest check, but the container's seccomp profile has no connect; the worker also exits by
# itself when the socket goes wrong, so the restart policy brings a fresh one.)
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-E", "-s", "-c", "import os, sys; sys.path.insert(0, '/worker'); from hubproto.scriptworker import socket_problem; why = socket_problem(os.environ['HUBPROTO_SCRIPT_SOCKET']); print(why or 'ok'); sys.exit(1 if why else 0)"]
# -E -s: no PYTHON* variables, no user site (-I would also drop /worker from sys.path, which -m needs).
CMD ["python", "-E", "-s", "-m", "hubproto.scriptworker"]
