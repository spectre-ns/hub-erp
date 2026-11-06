"""Wiring: one object that holds the stores, the kernel, the Cog catalog,
the inbox and the runner for a hub rooted at a directory."""

from __future__ import annotations

from pathlib import Path

from . import authz
from .cogs import CogRegistry
from .dms import DMS, BlobStore
from .compile import ProposalStore, compile_proposal, decide_change, finalize, readiness
from .extract import get_extractor
from .frames import FrameStore
from .inbox import Inbox
from .kernel import Kernel
from .release import ReleaseStore
from .runner import Runner

DEFAULT_SCOPES: list[str] | None = None  # None: every Frame whose scope is not "reference"


def default_scopes(store) -> list[str]:
    return [f.name for f in store.frames.values() if f.scope != "reference"]


class Hub:
    def __init__(self, root: Path, frames_dir: Path | None = None):
        self.root = Path(root)
        state = self.root / "state"
        self.frames = FrameStore(frames_dir or self.root / "frames")
        self.releases = ReleaseStore(state / "releases")
        self.proposals = ProposalStore(state / "proposals")
        self.kernel = Kernel(state / "kernel.db")
        self.registry = CogRegistry()
        self.inbox = Inbox(state / "inbox")
        self.dms = DMS(self.kernel, BlobStore(state / "blobs"))
        self.runner = Runner(state / "runs", self.kernel, self.releases, self.registry, self.inbox, self.dms)

    # --- policy lifecycle ---------------------------------------------------
    def compile(self, scopes: list[str] | None = None, extractor=None, now: str | None = None) -> dict:
        proposal = compile_proposal(self.frames, scopes or default_scopes(self.frames), self.registry.ids(), self.releases,
                                    extractor=extractor or get_extractor(), now=now,
                                    record_types=self.kernel.types, config_digests=self.kernel.config_digests)
        self.proposals.save(proposal)
        return proposal

    def review(self, proposal_id: str, by: str, decisions: dict[str, str] | None = None, approve_all: bool = False,
               meaning: str = "") -> dict:
        proposal = self.proposals.load(proposal_id)
        if approve_all:
            decisions = {c["id"]: "approve" for c in proposal["changes"]}
        for change_id, verdict in (decisions or {}).items():
            decide_change(proposal, change_id, verdict, by, meaning)
        self.proposals.save(proposal)
        return proposal

    def release(self, proposal_id: str, by: str, effective_from: str | None = None, now: str | None = None) -> dict:
        proposal = self.proposals.load(proposal_id)
        meta = finalize(proposal, self.releases, by=by, effective_from=effective_from, now=now)
        self.proposals.save(proposal)
        return meta

    def readiness(self, proposal_id: str) -> list[str]:
        return readiness(self.proposals.load(proposal_id))

    def current_bundle(self, at: str | None = None) -> dict | None:
        meta = self.releases.current(at)
        return self.releases.bundle(meta["digest"]) if meta else None

    # --- questions ----------------------------------------------------------
    def why(self, principal: str, action: str, obj: str, folder: str, at: str | None = None) -> dict:
        bundle = self.current_bundle(at)
        if bundle is None:
            return {"allowed": False, "rule": None, "why": "no policy release is in effect"}
        return authz.check(bundle, principal, action, obj, folder)

    # --- messaging ----------------------------------------------------------
    def send(self, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None) -> dict:
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
        return self.inbox.send(sender=sender, to=to, subject=subject, body=body, channel=channel,
                               attachments=attachments, bundle=bundle, registry=self.registry, runner=self.runner,
                               kernel=self.kernel, release=meta["digest"] if meta else "")
