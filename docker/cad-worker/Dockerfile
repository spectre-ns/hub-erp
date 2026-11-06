# CAD worker: FreeCAD and KiCad behind the /invoke envelope seam.
# CAD_TOOLS=full installs both tools from Debian (several GB); CAD_TOOLS=none
# builds a small image that answers with synthetic exports (HUBPROTO_CAD_FAKE=1).
FROM debian:trixie-slim
ARG CAD_TOOLS=full
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && if [ "$CAD_TOOLS" = "full" ]; then \
         apt-get install -y --no-install-recommends freecad kicad kicad-libraries || \
         apt-get install -y --no-install-recommends freecad kicad; \
       fi \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /worker
COPY hubproto/envelope.py hubproto/worker.py /worker/hubproto/
RUN printf '"""worker package"""\n' > /worker/hubproto/__init__.py
RUN useradd --create-home --uid 1000 worker && chown -R worker:worker /worker
USER worker
EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/healthz').read()" || exit 1
CMD ["python3", "-m", "hubproto.worker", "--host", "0.0.0.0", "--port", "9000"]
