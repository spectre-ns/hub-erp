# Intelligence Hub ERP prototype: one image, stdlib Python, SQLite and files.
# The hub's mutable state (releases, runs, kernel, blobs, inbox) lives on the
# /hub/state volume and its Frames (written by every release) on the /hub/frames
# volume, so both survive image rebuilds and nothing is written into the source
# tree. The image ships the repository's Frames in /usr/local/share/hubproto/frames;
# the entrypoint copies them into an empty /hub/frames on a hub's first start only.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HUBPROTO_ROOT=/hub
WORKDIR /hub

RUN useradd --create-home --uid 1000 hub
COPY --chown=hub:hub pyproject.toml README.md ./
COPY --chown=hub:hub hubproto ./hubproto
COPY frames /usr/local/share/hubproto/frames
COPY --chown=hub:hub profiles ./profiles
COPY --chown=hub:hub config ./config
COPY --chown=hub:hub examples ./examples
COPY --chown=hub:hub docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN pip install --no-cache-dir -e . && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /hub/state /hub/frames && chown hub:hub /hub/state /hub/frames

USER hub
VOLUME ["/hub/state", "/hub/frames"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["shell"]
