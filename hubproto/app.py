"""Wiring: one object that holds the stores, the kernel, the Cog catalog,
the inbox and the runner for a hub rooted at a directory."""

from __future__ import annotations

import os
from pathlib import Path

from . import authz
from .cogs import CogRegistry
from .dms import DMS, BlobStore
from .compile import CompileError, ProposalStore, compile_proposal, decide_change, finalize, readiness
from .extract import get_extractor
from .frames import FrameStore
from .inbox import Inbox
from .model import ModelGateway
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
        self._frames_dir = frames_dir or self.root / "frames"
        self._frames: FrameStore | None = None  # loaded on first use, so a broken Frame stops only the verbs that read Frames
        self.releases = ReleaseStore(state / "releases")
        self.proposals = ProposalStore(state / "proposals")
        self.kernel = Kernel(state / "kernel.db")
        self.registry = CogRegistry()
        self.inbox = Inbox(state / "inbox")
        self.dms = DMS(self.kernel, BlobStore(state / "blobs"))
        self.runner = Runner(state / "runs", self.kernel, self.releases, self.registry, self.inbox, self.dms)
        # Cogs are users: a Gate pends for a cog, and the cog takes its turn either at once (inline) or from the
        # cog-users service (`hubproto cog-users --loop 5`, HUBPROTO_COG_USERS=service).
        self.runner.cog_users_inline = os.environ.get("HUBPROTO_COG_USERS", "inline") != "service"
        self.runner.frames_store = lambda: self.frames
        self.dms.machine_names = lambda: set(self.registry.ids()) | {"hub"}
        self.model = ModelGateway()
        self.runner.model = self.model

    @property
    def frames(self) -> FrameStore:
        if self._frames is None:
            self._frames = FrameStore(self._frames_dir)
        return self._frames

    @frames.setter
    def frames(self, store: FrameStore) -> None:
        self._frames = store

    # --- policy lifecycle ---------------------------------------------------
    def compile(self, scopes: list[str] | None = None, extractor=None, now: str | None = None) -> dict:
        proposal = compile_proposal(self.frames, scopes or default_scopes(self.frames), self.registry.ids(), self.releases,
                                    extractor=extractor or get_extractor(), now=now,
                                    record_types=self.kernel.types, config_digests=self.kernel.config_digests)
        existing = self.proposals.list()
        proposal["number"] = 1 + max(len(existing), max((int(x.get("number") or 0) for x in existing), default=0))
        # An open proposal on the same base is superseded by this one; decisions already given on the same change
        # (same change id, same rule) carry forward, so a recompile does not throw away a policy owner's approval.
        for old in existing:
            if old.get("status") != "open" or old.get("base") != proposal["base"]:
                continue
            by_id = {c["id"]: c for c in old["changes"]}
            for c in proposal["changes"]:
                prior = by_id.get(c["id"])
                if prior and prior.get("rule") == c["rule"] and prior.get("decisions"):
                    c["decisions"] = [dict(d, carried_from=old["id"]) for d in prior["decisions"]]
            old["status"] = "superseded"
            old["superseded_by"] = proposal["id"]
            self.proposals.save(old)
        self.proposals.save(proposal)
        return proposal

    def review(self, proposal_id: str, by: str, decisions: dict[str, str] | None = None, approve_all: bool = False,
               meaning: str = "") -> dict:
        proposal = self.proposals.load(proposal_id)
        if proposal.get("status") != "open":
            raise CompileError(f"proposal {proposal_id} is {proposal.get('status')}; compile a new one")
        if by not in proposal["reviewers"]:
            raise CompileError(f"{by} is not a policy owner and cannot review changes (policy owners: {', '.join(proposal['reviewers']) or 'none'})")
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
    def chat_cogs(self) -> list[str]:
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
        return [n for n, m in sorted(bundle.get("members", {}).items()) if m.get("type") == "cog" and n in self.registry.ids()
                and "chat" in getattr(self.registry.get(n), "tasks", [])]

    def chat_prepare(self, sender: str, cog: str, text: str, about: str | None = None) -> dict:
        """Everything a chat turn needs before the model is asked (fast; kernel reads): the system prompt and the history.
        Returns {"system", "history", "model", "about", "cog"}; raises ValueError for a bad request."""
        from .cogs import RunContext, chat_prompt

        meta = self.releases.current()
        if not meta:
            raise ValueError("no policy release is in effect")
        bundle = self.releases.bundle(meta["digest"])
        members = bundle.get("members") or {}
        if members.get(sender, {}).get("type") != "person":
            raise ValueError(f"{sender} is not a person in the organization")
        if cog not in self.chat_cogs():
            raise ValueError(f"{cog} is not a cog you can chat with")
        if not (text or "").strip():
            raise ValueError("say something")
        rec = self.kernel.get(about) if about else None
        profile = (bundle.get("cogs") or {}).get(cog) or {}
        ctx = RunContext(run_id="chat", release=meta["digest"], bundle=bundle, owner=cog, kernel=self.kernel, runner=self.runner,
                         inbox=self.inbox, record=rec["locator"] if rec else None, dms=self.dms, frames=lambda: self.frames, model=self.model,
                         extras={"cog_profile": profile})
        queue = []
        for r in self.runner.pending_for(cog)[:10]:
            item = self.kernel.get(r["record"]) if r.get("record") else None
            queue.append(f"{item['number'] if item else r['process']}: {(r.get('pending') or {}).get('title', '')}")
        history = [{"role": "assistant" if m["from"] == cog else "user", "content": m["body"]} for m in self.inbox.chat_thread(sender, cog)[-12:]]
        return {"system": chat_prompt(ctx, rec, cog_id=cog, person=sender, queue=queue), "history": history,
                "model": profile.get("model") or "stand-in", "about": rec["locator"] if rec else None, "cog": cog,
                "sops": sorted(ctx.sop_texts()), "rec": rec}

    def chat_answer(self, prep: dict, sender: str, text: str) -> dict:
        """Ask the model (slow; touches no shared state but the gateway's call log)."""
        from .cogs import stand_in_chat

        out = self.model.complete(model=prep["model"], system=prep["system"], user=text, by=prep["cog"], max_tokens=600, history=prep["history"])
        reply = (out["text"] or "").strip()
        if out["stand_in"] or not reply:
            reply = stand_in_chat(prep["cog"], text, prep.get("rec"), prep.get("sops") or [])
        return {"text": reply, "model": out.get("served_model") or prep["model"], "stand_in": out["stand_in"]}

    def chat(self, sender: str, cog: str, text: str, about: str | None = None) -> dict:
        """One informal chat turn with a cog: the person's line and the cog's answer are both kept. Nothing else changes."""
        prep = self.chat_prepare(sender, cog, text, about)
        self.inbox.chat_append(sender=sender, to=cog, body=text.strip(), about=prep["about"])
        ans = self.chat_answer(prep, sender, text.strip())
        return self.inbox.chat_append(sender=cog, to=sender, body=ans["text"], about=prep["about"], model=ans["model"], stand_in=ans["stand_in"])

    def send(self, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None,
             record: str | None = None, comment_id: str | None = None) -> dict:
        """A message to a person or a cog, optionally about an item (then it is on the item's chatter and its
        @mentions are acted on: see hubproto.mentions)."""
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
        return self.inbox.send(sender=sender, to=to, subject=subject, body=body, channel=channel,
                               attachments=attachments, bundle=bundle, registry=self.registry, runner=self.runner,
                               kernel=self.kernel, release=meta["digest"] if meta else "", record=record, hub=self, comment_id=comment_id)
