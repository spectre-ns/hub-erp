#!/bin/sh
# First start: seed the kernel, install the ISO 9001 profile, compile and
# release the first policy release with the two seeded policy owners, and make
# the SOPs documents, so the shell opens on a working hub where every later
# configuration change goes through a change order. A failure is printed once, noted in
# state/.bootstrap-failed, and the shell still starts (with no release in
# effect) so the operator can fix the Frame and run one command.
# Later starts keep the existing state, seed only what a newer version needs
# and the state lacks, and say when the Frames in the hub's frames folder (the
# frames volume, docker-compose.yml) differ from the Frames in the release in force.
#
# The Frames: /hub/frames is the hub's own (a volume; every release writes the
# released texts into it). On the first start, when it is empty and there is no
# state yet, it is filled with the Frames the image ships ($SEED_FRAMES). It is
# never filled again: on a hub with state its Frames are that hub's, and an empty
# folder there means they were not copied in (docs/rollout/frames-volume.md).
# The copy is marked in progress ($SEEDING, written first, removed last): if it
# stops partway (the container exits, set -e), the next start sees the marker
# and copies again, instead of taking the partial copy for the hub's Frames.
set -e
cd "${HUBPROTO_ROOT:-/hub}"
SEED_FRAMES="${HUBPROTO_SEED_FRAMES:-/usr/local/share/hubproto/frames}"
SEEDING=frames/.first-start-copy-in-progress
mkdir -p frames
if [ -f "$SEEDING" ] || { [ -z "$(ls -A frames)" ] && [ ! -f state/kernel.db ]; }; then
  if [ -d "$SEED_FRAMES" ]; then
    if [ -f "$SEEDING" ]; then
      echo "first start: the copy of the image's Frames did not finish last time; copying them again"
    fi
    echo "first start: copying the Frames the image ships ($SEED_FRAMES) into $PWD/frames"
    : > "$SEEDING"
    cp -R "$SEED_FRAMES"/. frames/
    rm -f "$SEEDING"
  fi
elif [ -z "$(ls -A frames)" ]; then
  echo "WARNING: $PWD/frames is empty but this hub has state: its Frames were not copied in, and the image's are not"
  echo "  copied over a hub that has state. Copy the hub's Frames into the frames volume (docs/rollout/frames-volume.md);"
  echo "  until then status reports them missing."
fi
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
