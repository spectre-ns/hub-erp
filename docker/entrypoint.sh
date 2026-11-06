#!/bin/sh
# First start: seed the kernel, install the ISO 9001 profile, compile and
# release the first policy release with the two seeded policy owners, so the
# shell opens on a working hub. Later starts keep the existing state.
set -e
cd /hub
if [ ! -f state/.bootstrapped ]; then
  echo "bootstrapping hub state in /hub/state"
  hubproto init
  hubproto seed iso9001 || true
  hubproto bootstrap-release --as bob --second carol
  touch state/.bootstrapped
fi
case "$1" in
  shell) exec hubproto shell --host 0.0.0.0 --port 8080 ;;
  *) exec hubproto "$@" ;;
esac
