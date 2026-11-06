# Intelligence Hub ERP prototype: one image, stdlib Python, SQLite and files.
# The hub's mutable state (releases, runs, kernel, blobs, inbox) lives on the
# /hub/state volume so it survives image rebuilds.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HUBPROTO_ROOT=/hub
WORKDIR /hub

RUN useradd --create-home --uid 1000 hub
COPY --chown=hub:hub pyproject.toml README.md ./
COPY --chown=hub:hub hubproto ./hubproto
COPY --chown=hub:hub frames ./frames
COPY --chown=hub:hub profiles ./profiles
COPY --chown=hub:hub config ./config
COPY --chown=hub:hub examples ./examples
COPY --chown=hub:hub docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN pip install --no-cache-dir -e . && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /hub/state && chown hub:hub /hub/state

USER hub
VOLUME ["/hub/state"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["shell"]
