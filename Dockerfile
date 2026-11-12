# Intelligence Hub ERP prototype: one image, stdlib Python, SQLite and files.
# The hub's mutable state (releases, runs, kernel, blobs, inbox) lives on the
# /hub/state volume and its Frames (written by every release) on the /hub/frames
# volume, so both survive image rebuilds and nothing is written into the source
# tree. The image ships the repository's Frames in /usr/local/share/hubproto/frames;
# the entrypoint copies them into an empty /hub/frames on a hub's first start only.
FROM python:3.14-slim

# HUBPROTO_DEPLOYED: a hub run from this image is deployed: it runs a configuration script only in the script worker
# (HUBPROTO_SCRIPT_WORKER_URL), never in a local subprocess (issue #34). Unset it only for a development hub.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HUBPROTO_ROOT=/hub HUBPROTO_DEPLOYED=1
WORKDIR /hub

RUN useradd --create-home --uid 1000 hub
COPY --chown=hub:hub pyproject.toml README.md ./
COPY --chown=hub:hub hubproto ./hubproto
COPY frames /usr/local/share/hubproto/frames
COPY --chown=hub:hub profiles ./profiles
COPY --chown=hub:hub config ./config
COPY --chown=hub:hub examples ./examples
# The configuration document's JSON Schema: a script's output is held to it (hubproto.schemacheck).
COPY --chown=hub:hub docs/design/config-json.schema.json ./docs/design/config-json.schema.json
COPY --chown=hub:hub docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN pip install --no-cache-dir -e . && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /hub/state /hub/frames && chown hub:hub /hub/state /hub/frames

USER hub
VOLUME ["/hub/state", "/hub/frames"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["shell"]
