#!/bin/sh
# First start: seed the kernel, install the ISO 9001 profile, compile and
# release the first policy release with the two seeded policy owners, and make
# the SOPs documents, so the shell opens on a working hub where every later
# configuration change goes through a change order. A failure is printed once, noted in
# state/.bootstrap-failed, and the shell still starts (with no release in
# effect) so the operator can fix the Frame and run one command.
# Later starts keep the existing state, seed only what a newer version needs
# and the state lacks, and say when the Frames in the image differ from the
# Frames in the release in force.
set -e
cd /hub
if [ ! -f state/.bootstrapped ]; then
  echo "bootstrapping hub state in /hub/state"
  hubproto init
  if ! hubproto seed iso9001; then
    echo "seed iso9001 failed (see above); fix it and run: docker compose exec hub hubproto seed iso9001"
  fi
  if hubproto bootstrap-release --as bob --second carol --if-needed; then
    touch state/.bootstrapped
    rm -f state/.bootstrap-failed
  else
    echo "the bootstrap stopped (no release in effect, or the SOPs not yet documents): fix what is named above, then run:" | tee state/.bootstrap-failed
    echo "  docker compose exec hub hubproto bootstrap-release --as bob --second carol" | tee -a state/.bootstrap-failed
    echo "the shell starts anyway; it answers every page with 'no policy release is in effect' until then"
  fi
else
  # Seed what a newer version needs and the state lacks (stock locations, part categories); idempotent.
  hubproto init || echo "hubproto init failed (see above); the shell starts anyway"
  # Exit 1 means the Frames or configuration files on disk differ from the release in force (or no release is in
  # effect); the output says how to bring them into force (once the SOPs are documents: on a change order).
  if ! hubproto status --check; then
    echo "the shell serves the release in force, not the files above, until they are brought into force as status says"
  fi
fi
case "$1" in
  shell) exec hubproto shell --host 0.0.0.0 --port 8080 ;;
  *) exec hubproto "$@" ;;
esac
