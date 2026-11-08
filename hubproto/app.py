"""Wiring: one object that holds the stores, the kernel, the Cog catalog,
the inbox and the runner for a hub rooted at a directory."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from . import authz
from .cogs import CogRegistry
from .dms import DMS, BlobStore
from .compile import CompileError, ProposalStore, _reviewers, compile_proposal, decide_change, finalize, readiness
from .extract import get_extractor
from .frames import FrameStore, bump_version
from .inbox import Inbox
from .model import ModelGateway
from .workqueue import CogQueue
from .kernel import Kernel
from .release import utcnow, ReleaseStore
from .runner import Runner

DEFAULT_SCOPES: list[str] | None = None  # None: every Frame whose scope is not "reference"


def default_scopes(store) -> list[str]:
    return [f.name for f in store.frames.values() if f.scope != "reference"]


COMPILER_COG = "organization"  # reads SOPs into rules in the Compile the Frame Op when there is no committee


def _is_frame_doc(fields: dict) -> bool:
    from .sopdocs import is_frame_doc

    return is_frame_doc(fields)


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
        from .org_cog import OrganizationCog

        self.registry.register(OrganizationCog())  # the compile-frame committee's organization member
        try:
            meta = self.releases.current()
            self.registry.install_profiles(self.releases.bundle(meta["digest"]) if meta else None)
        except Exception:
            pass
        self.inbox = Inbox(state / "inbox")
        from .settings import SettingsDrafts

        self.settings_drafts = SettingsDrafts(state / "settings" / "drafts")
        self.dms = DMS(self.kernel, BlobStore(state / "blobs"))
        self.runner = Runner(state / "runs", self.kernel, self.releases, self.registry, self.inbox, self.dms)
        # Cogs are users who work from a queue: a Gate that pends for a cog, a mention, a message or a task lands in the
        # cog's queue (see hubproto.workqueue); HUBPROTO_COG_QUEUE says who works it (inline, a worker thread, a service).
        self.lock = threading.RLock()  # the shell serializes on it; the queue worker holds it except while a model thinks
        self.queue = CogQueue(state / "queue.db", hub=self)
        self.runner.queue = self.queue
        self.runner.hub = self  # for the change-order steps (hubproto.eco), which compile and release
        from . import automation as _automation

        self.automation = _automation.JobStore(Path(self.root) / "state" / "automation")
        from . import formlayout as _formlayout

        self.forms = _formlayout.FormStore(Path(self.root) / "state" / "forms")
        from .library import LibraryFolders

        self.library = LibraryFolders(Path(self.root) / "state" / "library")

        self.releases.extra_folders = self.library.paths
        self.releases.extra_rights = self.library.rights
        _formlayout.apply_custom_fields(self)  # released custom fields are fields of their record types
        self.runner.on_completed = [lambda state, rec: _automation.on_run_completed(self, state, rec)]
        self.runner.frames_store = lambda: self.released_frames()  # cogs read the SOPs as released, not the files on disk
        self.runner.frames_for_release = lambda d: self.released_frames(d)
        self._released_cache: dict = {}
        self.dms.machine_names = lambda: set(self.registry.ids()) | {"hub"}
        self.model = ModelGateway()
        self.runner.model = self.model
        try:  # a crash between a decision's Track entry and its saved state: close the Gate and carry on
            self.runner.reconcile()
        except Exception:
            pass

    @property
    def frames(self) -> FrameStore:
        if self._frames is None:
            self._frames = FrameStore(self._frames_dir)
        return self._frames

    @frames.setter
    def frames(self, store: FrameStore) -> None:
        self._frames = store

    # --- policy lifecycle ---------------------------------------------------
    # --- settings: the rules edited on screen (hubproto.settings) ------------------------------------
    def settings_to_change_order(self, user: str, reason: str = "") -> dict:
        """The person's Settings changes written into the SOPs they belong to, as draft revisions on their open
        settings change order (one is started when they have none). Returns {"change_order", "locator", "report"}."""
        d = self.settings_draft(user)
        out = self.rules_to_change_order(user, d["rules"], reason=reason or "Changes made in Settings.")
        self.settings_drafts.discard(user)
        return out

    def rules_to_change_order(self, user: str, new_rules: list[dict], reason: str = "") -> dict:
        """Rule changes (the rules in force -> ``new_rules``) written into the SOP documents they belong to, as draft
        revisions on the person's open settings change order. The Frame is derived from those documents: nothing
        changes until the revisions are approved and the change order is released."""
        from . import eco, sop_settings

        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"])
        planned = sop_settings.plan(self.settings_rules(), new_rules, bundle.get("processes") or {})
        if not planned:
            raise ValueError("that changes nothing")
        co = next((r for r in self.kernel.query(type_="change-order") if r["state"] in eco.OPEN_STATES
                   and r["fields"].get("requested_by") == user and r["fields"].get("from_settings")), None)
        if co is None:
            co = self.kernel.create("change-order", "Quality/Changes",
                                    {"title": f"Policy changes by {user}", "description": reason or "Changes to the policy.",
                                     "requested_by": user, "from_settings": True}, by=user)
        report = sop_settings.apply_plan(self, co["locator"], planned, by=user)
        return {"change_order": co["number"], "locator": co["locator"], "report": report}

    def extractor(self, calls: bool = False):
        """How this hub reads SOPs (hubproto.extract.get_extractor), with its cache and the names a model may use.
        ``calls``: may ask the local model (background jobs, the command line); a page uses only what was read before."""
        from .llm_extract import context_for

        ex = get_extractor(cache_dir=Path(self.root) / "state" / "extract-cache", context=context_for(self), gateway=self.model, calls=calls)
        if getattr(ex, "name", "") != "hybrid":
            return ex
        committee = self.compile_committee()
        if committee:
            from .llm_extract import CommitteeExtractor

            return CommitteeExtractor(committee["members"], committee["quorum"], cache_dir=Path(self.root) / "state" / "extract-cache",
                                      context=context_for(self), gateway=self.model, calls=calls)
        if not __import__("os").environ.get("HUBPROTO_EXTRACTOR_MODEL"):
            ex.model = self.compiler_model() or ex.model
        return ex

    def compile_committee(self) -> dict | None:
        """The compile-frame committee of the release in force: {"members": [(cog, its model)], "quorum"}; each member
        reads with the model its profile names (when the hub can reach it)."""
        from .model import backend_for

        meta = self.releases.current()
        if not meta:
            return None
        b = self.releases.bundle(meta["digest"])
        c = (b.get("committees") or {}).get("compile-frame")
        if not c:
            return None
        members = []
        for name in c.get("members") or []:
            m = ((b.get("cogs") or {}).get(name) or {}).get("model") or ""
            members.append((name, m if m and backend_for(m) not in ("none", "stand-in") else ""))
        return {"members": members, "quorum": int(c.get("quorum") or 1)}

    def compiler_model(self) -> str:
        """The model of the organization cog, which reads SOPs in the Compile the Frame Op when no committee is set: its
        profile names it, like any cog's (changed through the policy). When it names none the hub can reach, the largest
        local model."""
        from .llm_extract import local_model
        from .model import backend_for

        meta = self.releases.current()
        prof = ((self.releases.bundle(meta["digest"]).get("cogs") or {}).get(COMPILER_COG) or {}) if meta else {}
        m = prof.get("model") or ""
        return m if m and backend_for(m) not in ("none", "stand-in") else local_model()

    def sop_source(self) -> bool:
        """True once the SOPs and policies are documents in the library: the Frame is then compiled from them."""
        from . import sopdocs

        return sopdocs.converted(self.kernel)

    def settings_source(self, at: str | None = None) -> bool:
        """True when the release in force came from a settings proposal: settings are then the source of truth."""
        meta = self.releases.current(at)
        if not meta or not meta.get("proposal_id"):
            return False
        try:
            return self.proposals.load(meta["proposal_id"]).get("source") == "settings"
        except Exception:
            return False

    def settings_rules(self) -> list[dict]:
        meta = self.releases.current()
        return self.releases.rules(meta["digest"]) if meta else []

    def settings_draft(self, user: str) -> dict:
        """The person's working copy of the rules; a fresh copy of the release in force when they have no edits."""
        meta = self.releases.current()
        base = meta["digest"] if meta else None
        d = self.settings_drafts.load(user)
        if d is None or (d.get("base") != base and not d.get("edits")):
            d = self.settings_drafts.save(user, {"base": base, "rules": self.settings_rules(), "edits": []})
        d["stale"] = d.get("base") != base
        return d

    def settings_edit(self, user: str, edit: dict) -> dict:
        from .settings import apply_edit

        d = self.settings_draft(user)
        if d["stale"]:
            raise ValueError("the policy changed since you started these edits; discard your draft and start again from the policy in force")
        processes = (self.current_bundle() or {}).get("processes") or {}
        rules, said = apply_edit(d["rules"], edit, user, processes)
        d["rules"] = rules
        d["edits"] = list(d.get("edits") or []) + [{"at": utcnow(), "said": said}]
        return self.settings_drafts.save(user, {k: v for k, v in d.items() if k != "stale"})

    def settings_discard(self, user: str) -> None:
        self.settings_drafts.discard(user)

    def _settings_proposal(self, rules: list[dict], base: str | None, by: str, reason: str = "") -> dict:
        import uuid

        from .guards import consistency_guard, drift_guard, schema_guard

        findings = schema_guard(rules) + consistency_guard(rules, self.registry.ids(), self.kernel.types)
        old_rules = self.releases.rules(base) if base else []
        changes = drift_guard(old_rules, rules)
        for c in changes:
            c["decisions"] = []
        meta = self.releases.current() or {}
        return {"id": uuid.uuid4().hex[:12], "created_at": utcnow(), "extractor": "settings", "source": "settings",
                "proposed_by": by, "reason": reason, "scopes": [], "frames": meta.get("frames") or [],
                "config": self.kernel.config_digests, "record_types": self.kernel.types, "clauses": meta.get("clauses") or {},
                "rules": rules, "notes": [], "findings": [f.as_dict() for f in findings], "changes": changes, "base": base,
                "reviewers": sorted(_reviewers(old_rules) if old_rules else _reviewers(rules)), "status": "open",
                "release": None, "overrides": {}}

    def settings_check(self, user: str) -> dict:
        """What the person's draft would change and what the guards say, without proposing it."""
        d = self.settings_draft(user)
        return self._settings_proposal(d["rules"], d["base"], user)

    def settings_propose(self, user: str, reason: str = "") -> dict:
        """The draft becomes a proposal the policy owners review and release (two owners for widenings). Once the
        SOPs are the source, it becomes SOP edits on a change order instead (settings_to_change_order)."""
        d = self.settings_draft(user)
        if d["stale"]:
            raise ValueError("the policy changed since you started these edits; discard your draft and start again")
        if self.sop_source():
            return self.settings_to_change_order(user, reason)
        p = self._settings_proposal(d["rules"], d["base"], user, reason)
        if not p["changes"]:
            raise ValueError("your draft changes nothing")
        existing = self.proposals.list()
        p["number"] = 1 + max(len(existing), max((int(x.get("number") or 0) for x in existing), default=0))
        self.proposals.save(p)
        self.settings_drafts.discard(user)
        widen = sum(1 for c in p["changes"] if c.get("widening"))
        for owner in p["reviewers"]:
            if owner != user:
                self.inbox.notify(owner, kind="proposal", subject=f"Settings change #{p['number']} from {user}: {len(p['changes'])} change(s)"
                                  + (f", {widen} widening(s)" if widen else ""), body=reason or "See the proposal.")
        return p

    def compile(self, scopes: list[str] | None = None, extractor=None, now: str | None = None,
                overrides: dict[str, str] | None = None, drafts: dict[str, str] | None = None, save: bool = True,
                by: str | None = None, sop_drafts: dict[str, str] | None = None, from_release: bool = False) -> dict:
        """Compile the Frames into a proposal. ``overrides`` are drafts of Frames (name -> text) compiled in place of the
        files, written only when the proposal is released; ``drafts`` names the draft document of each. Drafts already in
        an open proposal on the same base carry forward, so a recompile never drops someone's pending policy change."""
        if self.settings_source():
            raise CompileError("the rules are edited in Settings since the release in force came from there; the Frames are the SOPs "
                               "people and cogs read. Change a rule in Settings (or with a cog's propose tools), not by compiling the Frames.")
        frames = self.released_frames() if from_release else self.frames  # from_release: only the overrides change
        if self.sop_source():
            # The Frame is derived from the SOP documents: the documents in force (or the drafts an ECO carries, in
            # ``sop_drafts``: {frame name: draft locator}) on top of the release in force; the files on disk are not read.
            from . import sopdocs

            frames = self.released_frames()
            derived = sopdocs.source_texts(self, drafts=sop_drafts or {})
            changed = {n: t for n, t in derived.items() if n not in frames.frames or frames.get(n).text != t}
            overrides = {**changed, **(overrides or {})}
            drafts = {**{n: loc for n, loc in (sop_drafts or {}).items() if n in changed}, **(drafts or {})}
        existing = self.proposals.list() if save else []
        base = (self.releases.current(now) or {}).get("digest")
        # One proposal per draft: a recompile of a draft carries forward its own earlier proposal (and the decisions
        # given on it), never someone else's unrelated draft; a compile of the files on disk carries no draft at all.
        my_drafts = {d for d in (drafts or {}).values() if d}

        def same_work(old: dict) -> bool:
            theirs = old.get("overrides") or {}
            if my_drafts:  # the earlier proposal of these very drafts (another person's draft of the same Frame competes)
                return bool({o.get("draft") for o in theirs.values()} & my_drafts)
            if overrides:  # texts with no draft document: the same Frames compiled again
                return bool(set(theirs) & set(overrides)) and not any(o.get("draft") for o in theirs.values())
            return not theirs  # a compile of the files replaces the last compile of the files
        related = [old for old in existing if old.get("status") == "open" and old.get("base") == base and old.get("source") != "settings"
                   and same_work(old)]
        if by and drafts:
            meta = self.releases.current(now)
            bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
            for name, loc in drafts.items():
                d = self.kernel.get(loc) if loc else None
                if d and by != d["fields"].get("owner") and "policy-owner" not in authz.roles_of(bundle, by):
                    raise CompileError(f"{d['number']} is {d['fields'].get('owner')}'s draft: only its author or a policy owner proposes it")
        carried: dict[str, dict] = {}
        for old in related:
            carried.update(old.get("overrides") or {})
        texts = {n: o["text"] for n, o in carried.items()}
        docs = {n: o.get("draft") for n, o in carried.items()}
        for name, text in (overrides or {}).items():
            texts[name] = bump_version(text, frames.get(name).version)
            docs[name] = (drafts or {}).get(name) or docs.get(name)
        store = frames.with_overrides(texts) if texts else frames
        proposal = compile_proposal(store, scopes or default_scopes(store), self.registry.ids(), self.releases,
                                    extractor=extractor or self.extractor(), now=now,
                                    record_types=self.kernel.types, config_digests=self.kernel.config_digests,
                                    resolve_document=self.resolve_document)
        proposal["overrides"] = {n: {"text": t, "draft": docs.get(n), "path": store.get(n).relpath} for n, t in texts.items()}
        # Explicit: what each Frame was compiled from (the SOP document and revision, or the Frame file).
        if self.sop_source():
            from . import sopdocs

            for entry in proposal["frames"]:
                n = entry["name"]
                d = self.kernel.get((sop_drafts or {})[n]) if (sop_drafts or {}).get(n) else sopdocs.in_force(self.kernel, n)
                if d:
                    entry["source"] = {"document": d["number"], "revision": d["fields"].get("revision"), "lineage": d["fields"].get("lineage"),
                                       "content_sha": d["fields"].get("content_sha"), "state": d["state"], "title": d["fields"].get("title")}
        else:
            for entry in proposal["frames"]:
                entry["source"] = {"file": entry.get("path"), "sha256": entry.get("sha256")}
        proposal["proposed_by"] = by
        authors = {}
        for n, o in proposal["overrides"].items():  # who wrote each draft, so a reviewer sees every hand in it
            if o.get("draft") and self.kernel.get(o["draft"]):
                authors[n] = sorted({v.get("by") for v in self.dms.versions(o["draft"]) if v.get("by")})
        proposal["draft_authors"] = authors
        from .model import resolve_model

        for r in proposal["rules"]:  # a cog is bundled with its model: say now if this hub cannot reach it
            if r.get("kind") == "cog":
                res = resolve_model(r.get("model") or "")
                if not res["available"]:
                    src = r.get("source") or {}
                    proposal["findings"].append({"guard": "model", "severity": "warning", "rule_key": "", "frame": src.get("frame", ""),
                                                 "line": int(src.get("line", 0) or 0),
                                                 "detail": f"cog {r['name']} is bundled with {r.get('model') or 'no model'}, which this hub cannot use ({res['note']}); "
                                                           "the cog will be unavailable until it can"})
        if not save:
            return proposal
        proposal["number"] = 1 + max(len(existing), max((int(x.get("number") or 0) for x in existing), default=0))
        # The earlier proposal of the same draft (or the earlier compile of the files) is superseded by this one;
        # decisions already given on the same change carry forward, so a recompile keeps a policy owner's approval.
        for old in related:
            if old.get("base") != proposal["base"]:
                continue
            by_id = {c["id"]: c for c in old["changes"]}
            for c in proposal["changes"]:
                prior = by_id.get(c["id"])
                if prior and prior.get("rule") == c["rule"] and prior.get("decisions"):
                    c["decisions"] = [dict(d, carried_from=old["id"]) for d in prior["decisions"]]
            old["status"] = "superseded"
            old["superseded_by"] = proposal["id"]
            old["superseded_by_number"] = proposal["number"]
            self.proposals.save(old)
        self.proposals.save(proposal)
        for name, o in proposal["overrides"].items():
            if o.get("draft") and self.kernel.get(o["draft"]):
                self.kernel.set_fields(o["draft"], {"proposal": proposal["id"], "proposal_number": proposal["number"]}, by="hub")
        return proposal

    # --- Frames and cog settings as documents ----------------------------------
    FRAME_FOLDER = "Policy/Frames"

    def resolve_document(self, ref: str) -> dict | None:
        """A document by number or locator, pinned to its current version: what a cog profile links to."""
        ref = (ref or "").strip()
        rec = self.kernel.get(ref if ref.startswith("rec:") else f"rec:document:{ref}")
        if rec is None or rec["type"] != "document":
            return None
        versions = self.dms.versions(rec["locator"])
        if not versions:
            return None
        v = versions[-1]
        return {"locator": rec["locator"], "number": rec["number"], "title": rec["fields"].get("title") or "", "version": v["version"],
                "sha256": v.get("sha256") or "", "filename": v.get("filename") or ""}

    def pinned_text(self, pin: dict | None, limit: int = 20000) -> str:
        """The text of a document exactly as a release pinned it."""
        if not pin:
            return ""
        from .dms import extract_text

        try:
            content, v = self.dms.content(pin["locator"], pin.get("version"))
        except Exception:
            return ""
        return extract_text(content, v.get("mime") or "", v.get("filename") or "")[:limit]

    def frame_document(self, name: str, create: bool = True, by: str = "hub") -> dict | None:
        """The controlled document that carries a Frame's text: created on first use, versioned on every release. It is
        filed as the quality document it is: an SOP or work instruction in Quality/Controlled Documents, a policy, scope
        or persona in Policy, titled by its own heading. (Once the SOPs are the source, it is the SOP document itself.)"""
        from . import sopdocs

        if self.sop_source():  # the SOP document in force is the Frame's document
            return sopdocs.in_force(self.kernel, name)
        released = self.released_frames()
        if name not in released.frames:
            return None  # a Frame that is not in the release in force (a draft on disk) has no document: it is not policy
        frame = released.get(name)
        doc_type, folder = sopdocs.kind_of(frame)
        title = sopdocs.title_of(frame)
        found = [d for d in self.kernel.query(type_="document")
                 if sopdocs.is_frame_doc(d["fields"]) and d["fields"].get("frame_name") == name]
        if found:
            rec = found[0]
            if rec["folder"] != folder:  # filed as a quality document (it was in Policy/Frames)
                self.kernel.move(rec["locator"], folder, by=by, reason="an SOP is a quality document; policies stay in Policy")
            if rec["fields"].get("doc_type") != doc_type or rec["fields"].get("title") != title:
                self.kernel.set_fields(rec["locator"], {"doc_type": doc_type, "frame_mirror": True, "title": title}, by=by)
            rec = self.kernel.get(rec["locator"])
            content, _ = self.dms.content(rec["locator"])
            if content.decode("utf-8", "replace") != frame.text:
                self.dms.add_version(rec["locator"], frame.text.encode("utf-8"), by=by, filename=frame.path.name, mime="text/markdown",
                                     note=f"version {frame.version} of the Frame", system=True)
                self.kernel.set_fields(rec["locator"], {"frame_version": frame.version, "revision": frame.version}, by=by)
            return self.kernel.get(rec["locator"])
        if not create:
            return None
        from datetime import date, timedelta

        doc = self.dms.create(title=title, doc_class="controlled", folder=folder, by=by,
                              content=frame.text.encode("utf-8"), filename=frame.path.name, mime="text/markdown", doc_type=doc_type,
                              fields={"frame_name": name, "frame_mirror": True, "frame_path": frame.relpath, "frame_version": frame.version,
                                      "revision": frame.version, "owner": frame.maintainer or by,
                                      "review_date": (date.today() + timedelta(days=365)).isoformat(),
                                      "description": frame.description})
        return self.kernel.transition(doc["locator"], "effective", by=by, reason=f"the Frame as released (version {frame.version})")

    def frame_documents(self, create: bool = True) -> list[dict]:
        """The document of every Frame in the release in force; a document of a Frame no longer (or never) released is retired."""
        from . import sopdocs

        released = self.released_frames()
        if not self.sop_source():
            for d in self.kernel.query(type_="document"):
                f = d["fields"]
                if sopdocs.is_frame_doc(f) and f.get("frame_name") not in released.frames and d["state"] == "effective":
                    self.kernel.transition(d["locator"], "obsolete", by="hub", reason="its Frame is not in the release in force")
        return [d for d in (self.frame_document(n, create=create) for n in sorted(released.frames)) if d]

    def frame_draft(self, name: str, by: str) -> dict:
        """A working draft of a Frame: the current text, edited in the hub and proposed for release. One per person.
        Once the SOPs are documents the Frame is derived from them: it changes through a change order instead."""
        if self.sop_source():
            from . import sopdocs

            doc = sopdocs.in_force(self.kernel, name)
            raise ValueError(f"the {name} Frame is compiled from " + (f"{doc['number']}" if doc else "its SOP document")
                             + ": change that document on a change order")
        doc = self.frame_document(name)
        for d in self.kernel.query(type_="document"):
            f = d["fields"]
            if f.get("doc_type") == "frame-draft" and f.get("frame_name") == name and f.get("owner") == by and d["state"] == "open" and not f.get("withdrawn"):
                return d
        frame = self.frames.get(name)
        draft = self.dms.create(title=f"Draft change to {name}", doc_class="working", folder=self.FRAME_FOLDER, by=by,
                                content=frame.text.encode("utf-8"), filename=frame.path.name, mime="text/markdown", doc_type="frame-draft",
                                fields={"frame_name": name, "frame_path": frame.relpath, "based_on_version": frame.version})
        if doc is not None:
            self.kernel.link(draft["locator"], "references", doc["locator"], by=by)
        return self.kernel.get(draft["locator"])

    def propose_cog_model(self, cog: str, model: str, by: str) -> dict:
        """Change a cog's model as a policy proposal: the Model line of its profile, drafted and compiled."""
        import re as _re

        model = (model or "").strip()
        if not model or "\n" in model:
            raise ValueError("name a model")
        if self.sop_source():
            # The Frame is derived from the SOPs: the Model line is changed in the cog-profiles policy document.
            rules = self.settings_rules()
            if not any(r["kind"] == "cog" and r["name"] == cog for r in rules):
                raise ValueError(f"{cog} has no profile to change")
            new = [({**r, "model": model} if r["kind"] == "cog" and r["name"] == cog else r) for r in rules]
            return self.rules_to_change_order(by, new, reason=f"{cog} uses {model}")
        released = self.released_frames()  # the change is made to the policy in force, not to unreleased edits on disk
        frame = next((f for f in released.frames.values() if f"## Cog: {cog}" in f.text), None)
        if frame is None:
            raise ValueError(f"{cog} has no profile to change")
        lines = frame.text.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.strip() == f"## Cog: {cog}")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        at = next((i for i in range(start + 1, end) if _re.match(r"^Model:", lines[i])), None)
        if at is None:
            lines.insert(start + 1, f"Model: {model}")
        else:
            lines[at] = f"Model: {model}"
        text = "\n".join(lines) + ("\n" if frame.text.endswith("\n") else "")
        d = self.frame_draft(frame.name, by=by)
        self.dms.add_version(d["locator"], text.encode("utf-8"), by=by, filename=frame.path.name, mime="text/markdown", note=f"{cog} uses {model}")
        return self.compile(overrides={frame.name: text}, drafts={frame.name: d["locator"]}, by=by, from_release=True)

    def propose_new_person(self, *, name: str, roles: list[str], by: str) -> dict:
        """A new person as a policy proposal: the member and their roles in the organization model. (In production
        people come from the identity provider; their roles still come from this Frame.)"""
        return self.propose_new_member(kind="person", name=name, roles=roles, by=by)

    def propose_new_cog(self, *, name: str, roles: list[str], model: str, prompt: str, follows: list[str], autonomy: str,
                        duties: list[str], by: str) -> dict:
        """A new cog as one policy proposal: the member in the organization model and its profile, drafted as changes
        to those two Frames. The policy owners review and release it; on release the hub installs it."""
        return self.propose_new_member(kind="cog", name=name, roles=roles, model=model, prompt=prompt, follows=follows,
                                       autonomy=autonomy, duties=duties, by=by)

    def propose_new_member(self, *, kind: str, name: str, roles: list[str], by: str, model: str = "", prompt: str = "",
                           follows: list[str] | None = None, autonomy: str = "propose", duties: list[str] | None = None) -> dict:
        import re as _re

        follows, duties = list(follows or []), list(duties or [])
        if self.sop_source():
            # The Frame is derived from the SOPs: the member goes into the organization model, a cog's profile into
            # the cog profiles, both as revisions on a change order.
            rules = self.settings_rules()
            if any(r["kind"] == "member" and r["principal"] == name for r in rules):
                raise ValueError(f"{name} is already a member")
            new = rules + [{"kind": "member", "principal": name, "principal_type": kind, "roles": list(roles)}]
            if kind == "cog":
                new.append({"kind": "cog", "name": name, "model": model, "prompt": prompt, "prompt_from": "", "follows": follows,
                            "reviews": [], "references": [], "autonomy": autonomy, "duties": duties})
            return self.rules_to_change_order(by, new, reason=f"add {name} ({kind})")
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {}
        name = (name or "").strip().lower()
        if not _re.fullmatch(r"[a-z][a-z0-9-]{1,40}", name):
            raise ValueError("a name is lower case letters, digits and hyphens, e.g. erin or calibration-assistant")
        if name in (bundle.get("members") or {}):
            raise ValueError(f"{name} is already a member")
        unknown = [r for r in roles if r not in (bundle.get("roles") or [])]
        if unknown or not roles:
            raise ValueError("choose at least one role" if not roles else f"no such role: {', '.join(unknown)}")
        bad = [f for f in follows if f not in self.frames.frames]
        if bad:
            raise ValueError(f"no such Frame: {', '.join(bad)}")
        if kind == "cog" and autonomy not in ("read", "propose", "act"):
            raise ValueError("autonomy is read, propose or act")
        org = self.frames.get("org-model")
        cogs_frame = next((f for f in self.frames.frames.values() if "## Cog:" in f.text), None) if kind == "cog" else None
        if kind == "cog" and cogs_frame is None:
            raise ValueError("there is no Frame with cog profiles to add the profile to")
        lines = org.text.splitlines()
        members_at = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "## members")
        last = members_at
        for i in range(members_at + 1, len(lines)):
            if lines[i].startswith("## "):
                break
            if lines[i].startswith("- "):
                last = i
        if kind == "person":
            # people are listed before the cogs
            first_cog = next((i for i in range(members_at + 1, last + 1) if "(cog)" in lines[i]), last + 1)
            lines.insert(first_cog, f"- {name} (person): {', '.join(roles)}")
        else:
            lines.insert(last + 1, f"- {name} (cog): {', '.join(roles)}")
        org_text = "\n".join(lines) + ("\n" if org.text.endswith("\n") else "")
        texts = {"org-model": org_text}
        if kind == "cog":
            prof = [f"## Cog: {name}", f"Model: {model or 'claude-sonnet-5-5'}", f"Prompt: {' '.join((prompt or '').split()) or f'You are {name}.'}",
                    f"Follows: {', '.join(follows)}" if follows else "Follows:", f"Autonomy: {autonomy}"] + [f"Duty: {d.strip()}" for d in duties if d.strip()]
            texts[cogs_frame.name] = cogs_frame.text.rstrip("\n") + "\n\n" + "\n".join(prof) + "\n"
        drafts = {}
        for frame_name, text in texts.items():
            d = self.frame_draft(frame_name, by=by)
            self.dms.add_version(d["locator"], text.encode("utf-8"), by=by, filename=self.frames.get(frame_name).path.name, mime="text/markdown",
                                 note=f"new {kind} {name}")
            drafts[frame_name] = d["locator"]
        return self.compile(overrides=texts, drafts=drafts, by=by)

    def withdraw_frame_draft(self, ref: str, by: str, reason: str = "") -> dict:
        """Withdraw a Frame draft: it leaves every open proposal (which is withdrawn with it, so the next compile reads
        the files and the other drafts) and is no longer carried into compiles. The draft's owner or a policy owner may."""
        rec = self.kernel.require(ref)
        f = rec["fields"]
        if f.get("doc_type") != "frame-draft" or f.get("withdrawn"):
            raise ValueError(f"{rec['number']} is not an open Frame draft")
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
        if by != f.get("owner") and "policy-owner" not in authz.roles_of(bundle, by):
            raise ValueError(f"only the draft's author ({f.get('owner')}) or a policy owner withdraws {rec['number']}")
        touched = []
        for p in self.proposals.list():
            if p.get("status") != "open":
                continue
            mine = [n for n, o in (p.get("overrides") or {}).items() if o.get("draft") == rec["locator"]]
            if mine:
                rest = {n: o for n, o in (p.get("overrides") or {}).items() if n not in mine}
                p["status"] = "withdrawn"
                p["withdrawn"] = {"by": by, "at": utcnow(), "reason": reason or f"draft {rec['number']} withdrawn", "draft": rec["locator"]}
                self.proposals.save(p)
                touched.append(p.get("number"))
                if rest:  # other people's drafts in the same proposal stay proposed, with the decisions given on them
                    self._recompile_drafts(p, rest, note=f"{rec['number']} was withdrawn by {by}")
        self.kernel.set_fields(rec["locator"], {"withdrawn": True, "withdrawn_by": by, "withdrawn_reason": reason, "proposal": None}, by=by)
        return {"draft": rec["number"], "proposals_withdrawn": touched}

    def draft_text(self, draft: dict) -> str:
        content, _ = self.dms.content(draft["locator"])
        return content.decode("utf-8", "replace")

    def review(self, proposal_id: str, by: str, decisions: dict[str, str] | None = None, approve_all: bool = False,
               meaning: str = "") -> dict:
        proposal = self.proposals.load(proposal_id)
        if proposal.get("status") != "open":
            raise CompileError(f"proposal #{proposal.get('number') or proposal_id} is {proposal.get('status')}"
                               + (f": it was replaced by #{proposal['superseded_by_number']}, review that one" if proposal.get("superseded_by_number")
                                  else f": {proposal['stale_reason']}" if proposal.get("stale_reason") else ""))
        if by not in proposal["reviewers"]:
            raise CompileError(f"{by} is not a policy owner and cannot review changes (policy owners: {', '.join(proposal['reviewers']) or 'none'})")
        if approve_all:
            decisions = {c["id"]: "approve" for c in proposal["changes"]}
        for change_id, verdict in (decisions or {}).items():
            decide_change(proposal, change_id, verdict, by, meaning)
        self.proposals.save(proposal)
        return proposal

    def release(self, proposal_id: str, by: str, effective_from: str | None = None, now: str | None = None) -> dict:
        from .compile import change_state

        proposal = self.proposals.load(proposal_id)
        base = proposal.get("base")
        rejected_text = {c["frame"] for c in proposal.get("changes") or [] if c.get("op") == "text" and change_state(c) == "rejected"}
        overrides = {n: o for n, o in (proposal.get("overrides") or {}).items() if n not in rejected_text}
        texts = {}
        src = self.released_frames() if self.sop_source() else self.frames
        for f in proposal.get("frames") or []:
            try:
                texts[f["name"]] = overrides[f["name"]]["text"] if f["name"] in overrides else src.get(f["name"]).text
            except Exception:
                continue
        meta = finalize(proposal, self.releases, by=by, effective_from=effective_from, now=now, frame_texts=texts)
        self.proposals.save(proposal)
        self.registry.install_profiles(self.releases.bundle(meta["digest"]))
        self._released_cache = {}
        if overrides:
            # The drafts become the Frames: written in place, then every Frame document follows its file.
            for name, o in overrides.items():
                # The Frame is derived output: the released text overwrites the file.
                path = self.frames.get(name).path
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(o["text"], encoding="utf-8")
                tmp.replace(path)
            self._frames = None
            for name, o in overrides.items():
                if self.sop_source():
                    continue  # the SOP documents are the source: the change order makes its revisions effective
                self.frame_document(name, by=by)
                draft = self.kernel.get(o["draft"]) if o.get("draft") else None
                if draft and draft["state"] == "open":
                    self.kernel.set_fields(draft["locator"], {"released_in": meta["digest"]}, by=by)
                    self.kernel.transition(draft["locator"], "released", by=by, reason=f"released in policy {meta['digest'][:12]}")
        elif not self.sop_source() and any(_is_frame_doc(d["fields"]) for d in self.kernel.query(type_="document")):
            self.frame_documents(create=False)
        self._rebase_open_proposals(base, proposal, now=now)
        return meta

    # --- keeping the other open proposals releasable ---------------------------
    def _recompile_drafts(self, old: dict, overrides: dict, note: str = "") -> dict | None:
        """Compile these drafts again against the policy in force, as the successor of ``old`` (decisions carried)."""
        texts = {n: o["text"] for n, o in overrides.items()}
        drafts = {n: o.get("draft") for n, o in overrides.items() if o.get("draft")}
        try:
            new = self.compile(overrides=texts, drafts=drafts)
        except Exception as e:
            old.setdefault("notes_for_owners", []).append(f"could not be recompiled: {e}")
            self.proposals.save(old)
            return None
        by_id = {c["id"]: c for c in old.get("changes") or []}
        for c in new["changes"]:
            prior = by_id.get(c["id"])
            if prior and prior.get("rule") == c["rule"] and prior.get("decisions") and not c.get("decisions"):
                c["decisions"] = [dict(d, carried_from=old["id"]) for d in prior["decisions"]]
        new["proposed_by"] = old.get("proposed_by")
        new["recompiled_from"] = {"id": old["id"], "number": old.get("number"), "note": note}
        self.proposals.save(new)
        if old.get("status") == "open":
            old["status"] = "superseded"
        old["superseded_by"], old["superseded_by_number"] = new["id"], new.get("number")
        self.proposals.save(old)
        return new

    def _rebase_open_proposals(self, old_base: str | None, released: dict, now: str | None = None) -> None:
        """After a release, the other open proposals on the same base are compiled again against it, so each can still
        be released on its own. A draft of a Frame the release just changed cannot be rebased silently: its owner
        redoes it on the new text."""
        changed = set((released.get("overrides") or {}).keys())
        for p in self.proposals.list():
            if p.get("status") != "open" or p.get("base") != old_base or p["id"] == released["id"]:
                continue
            if p.get("source") == "settings":
                self._rebase_settings(p)
                continue
            overrides = p.get("overrides") or {}
            if not overrides:
                continue  # a compile of the files: compile again when ready
            clash = sorted(set(overrides) & changed)
            if clash:
                p["status"] = "stale"
                p["stale_reason"] = (f"{', '.join(clash)} changed in the release of #{released.get('number')}; redo the draft on the new text")
                self.proposals.save(p)
                for o in overrides.values():
                    d = self.kernel.get(o["draft"]) if o.get("draft") else None
                    if d:
                        self.inbox.notify(d["fields"].get("owner") or "", kind="proposal", subject=f"Your draft {d['number']} needs redoing",
                                          body=p["stale_reason"], record=d["locator"])
                continue
            self._recompile_drafts(p, overrides, note=f"rebased after the release of #{released.get('number')}")

    def _rebase_settings(self, p: dict) -> dict | None:
        from .rules import rule_key

        current = self.settings_rules()
        removed = {rule_key(c["rule"]) for c in p.get("changes") or [] if c["op"] == "remove"}
        added = [c["rule"] for c in p.get("changes") or [] if c["op"] == "add"]
        keys = {rule_key(r) for r in current}
        rules = [r for r in current if rule_key(r) not in removed] + [r for r in added if rule_key(r) not in keys]
        meta = self.releases.current()
        new = self._settings_proposal(rules, meta["digest"] if meta else None, p.get("proposed_by") or "", p.get("reason") or "")
        if not new["changes"]:
            p["status"] = "superseded"
            p["stale_reason"] = "the release in force already contains these changes"
            self.proposals.save(p)
            return None
        by_id = {c["id"]: c for c in p.get("changes") or []}
        for c in new["changes"]:
            prior = by_id.get(c["id"])
            if prior and prior.get("rule") == c["rule"] and prior.get("decisions"):
                c["decisions"] = [dict(d, carried_from=p["id"]) for d in prior["decisions"]]
        existing = self.proposals.list()
        new["number"] = 1 + max(len(existing), max((int(x.get("number") or 0) for x in existing), default=0))
        new["recompiled_from"] = {"id": p["id"], "number": p.get("number"), "note": "rebased after a release"}
        self.proposals.save(new)
        p["status"], p["superseded_by"], p["superseded_by_number"] = "superseded", new["id"], new["number"]
        self.proposals.save(p)
        return new

    # --- controlled templates --------------------------------------------------
    def import_templates(self, by: str, submit: bool = False, paths=None) -> list[dict]:
        """The Frames' Markdown templates into the library as controlled templates (see hubproto.templates): idempotent,
        keyed by lineage; a changed source updates the open draft or becomes a new revision of the one in force."""
        from . import templates

        return templates.import_templates(self, by=by, submit=submit, paths=paths)

    # --- the Frames as released -----------------------------------------------
    def released_frames(self, digest: str | None = None):
        """The Frame store as a release pinned it: each Frame's text from that release (the release in force by
        default), so a cog judges against the reviewed SOP, not an unreleased edit on disk. Releases made before
        texts were kept read the files."""
        meta = self.releases.current() if digest is None else self.releases.get(digest)
        if not meta:
            return self.frames
        key = (meta["digest"], id(self._frames))
        if key not in self._released_cache:
            texts = self.releases.frame_texts(meta["digest"])
            store = self.frames
            differ = {n: t for n, t in texts.items() if n in store.frames and store.get(n).text != t}
            try:
                self._released_cache[key] = store.with_overrides(differ) if differ else store
            except Exception:
                self._released_cache[key] = store
        return self._released_cache[key]

    def readiness(self, proposal_id: str) -> list[str]:
        return readiness(self.proposals.load(proposal_id))

    def current_bundle(self, at: str | None = None) -> dict | None:
        meta = self.releases.current(at)
        return self.releases.bundle(meta["digest"]) if meta else None

    # --- questions ----------------------------------------------------------
    def why_item(self, principal: str, ref: str) -> dict:
        """Whether a member may decide this item now, and every reason why not: the permission sentence, segregation
        of duties (by named role), whom the Gate waits for, a countersignature's role. For "why can't I approve X?"."""
        rec = self.kernel.require(ref)
        run = self.runner.run_for_record(rec["locator"])
        if not run or run["status"] != "waiting" or not run.get("pending"):
            return {"may": False, "reasons": [f"{rec['number']} is not waiting for a decision ({(run or {}).get('status') or rec['state']})"]}
        bundle = self.releases.bundle(run["release"])
        pending = run["pending"]
        reasons = []
        perm = authz.check(bundle, principal, "approve", run["manifest"]["object"], run["manifest"]["folder"])
        if not perm["allowed"]:
            reasons.append(perm["why"])
        excluded = self.runner.excluded(bundle, run, pending.get("route") or "role:")
        if principal in excluded:
            reasons.append(excluded[principal])
        if pending.get("countersign_of"):
            role = pending.get("countersign_role") or ""
            if role and role not in authz.roles_of(bundle, principal):
                reasons.append(f"it waits for a {role.replace('-', ' ')} to countersign")
            if principal in (pending.get("excluded") or []):
                reasons.append("segregation of duties: they may not countersign it")
        if principal not in (pending.get("assignees") or []):
            reasons.append(f"it waits for {', '.join(pending.get('assignees') or [])} at {pending.get('title', 'its Gate')} "
                           f"(routed by: {(pending.get('route_rule') or {}).get('text', 'the policy')})")
        return {"may": not reasons, "reasons": reasons, "waiting_for": pending.get("assignees") or [], "gate": pending.get("title")}

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

    def chat_tools(self, cog: str, sender: str, run_id: str, text: str, about: str | None = None):
        """The cog's governed tools for one chat turn, recorded as an agent run (so /agent-run shows what it did and a
        person confirms what it proposed): the look tools and, within its Autonomy, the write tools. Every write goes
        through the same checks as anywhere else: what the cog may do and what the person asking may do."""
        from .agent import AgentStore, AgentTools, act_specs, agents_enabled, look_specs

        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {}
        mode = ((bundle.get("cogs") or {}).get(cog) or {}).get("autonomy") or "propose"
        if not agents_enabled():
            mode = "read"
        actions = int(os.environ.get("HUBPROTO_CHAT_ACTIONS") or 5)
        store = AgentStore(self.root / "state" / "agents")
        store.append(run_id, {"type": "started", "cog": cog, "goal": text, "by": sender, "trigger": "chat", "mode": mode, "about": about,
                              "budget": {"actions": actions, "rounds": 8}, "release": meta["digest"] if meta else ""})
        tools = AgentTools(self, cog, run_id, mode, actions, store, requester=sender, goal=text)
        specs = [s for s in look_specs() + act_specs(mode) if s["name"] != "finish"]
        return tools, specs, mode, store

    def chat_prepare(self, sender: str, cog: str, text: str, about: str | None = None, tools_now: list[dict] | None = None,
                     mode: str = "") -> dict:
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
        from .model import cog_availability

        ok, why = cog_availability(bundle, cog)
        if not ok:
            raise ValueError(why)
        if not (text or "").strip():
            raise ValueError("say something")
        rec = self.kernel.get(about) if about else None
        profile = (bundle.get("cogs") or {}).get(cog) or {}
        ctx = RunContext(run_id="chat", release=meta["digest"], bundle=bundle, owner=cog, kernel=self.kernel, runner=self.runner,
                         inbox=self.inbox, record=rec["locator"] if rec else None, dms=self.dms, frames=lambda: self.released_frames(), model=self.model,
                         extras={"cog_profile": profile})
        queue = []
        for r in self.runner.pending_for(cog)[:10]:
            item = self.kernel.get(r["record"]) if r.get("record") else None
            queue.append(f"{item['number'] if item else r['process']}: {(r.get('pending') or {}).get('title', '')}")
        history = [{"role": "assistant" if m["from"] == cog else "user", "content": m["body"]} for m in self.inbox.chat_thread(sender, cog)[-12:]]
        return {"system": chat_prompt(ctx, rec, cog_id=cog, person=sender, queue=queue, tools_now=tools_now, mode=mode), "history": history,
                "model": profile.get("model") or "stand-in", "about": rec["locator"] if rec else None, "cog": cog,
                "sops": sorted(ctx.sop_texts()), "rec": rec}

    def chat_answer(self, prep: dict, sender: str, text: str, call_tool=None, specs: list[dict] | None = None) -> dict:
        """Ask the model (slow), letting it look records up with the record tools as the person chatting.
        ``call_tool(name, args)`` runs one tool; the shell passes one that takes the hub's lock for the call."""
        from .cogs import stand_in_chat
        from .tools import RecordTools, tool_specs

        from .model import ModelUnavailable

        tools = RecordTools(self, sender)
        looked = chat_lookup(call_tool or tools.call, text)
        if looked:
            # Small models often answer "I can't find it" without searching: the hub searches first and shows them what it found.
            prep = {**prep, "system": prep["system"] + "\n\nLOOKED UP FOR THIS QUESTION (search_records, with this person's rights):\n"
                    + json.dumps(looked["result"], default=str, ensure_ascii=False)[:6000]
                    + "\nAnswer from these results; name the record numbers (the messenger turns them into links)."}
        try:
            out = self._chat_call(prep, text, call_tool or tools.call, (lambda: specs) if specs else tool_specs)
        except ModelUnavailable as e:
            raise ValueError(f"{prep['cog']} is not available: {e}") from None
        reply = plain_chat(out["text"] or "")
        if not reply:
            reply = stand_in_chat(prep["cog"], text, prep.get("rec"), prep.get("sops") or [])
        calls = ([{"tool": "search_records", "arguments": looked["arguments"]}] if looked else []) + (out.get("tool_calls") or [])
        return {"text": reply, "model": out.get("served_model") or prep["model"], "stand_in": out["stand_in"], "tool_calls": calls}

    def _chat_call(self, prep: dict, text: str, call_tool, tool_specs) -> dict:
        return self.model.complete_with_tools(model=prep["model"], system=prep["system"], user=text, by=prep["cog"], max_tokens=800,
                                             history=prep["history"], tools=tool_specs(), call_tool=call_tool)

    def chat(self, sender: str, cog: str, text: str, about: str | None = None) -> dict:
        """One informal chat turn with a cog: the person's line and the cog's answer are both kept. Nothing else changes."""
        prep = self.chat_prepare(sender, cog, text, about)
        self.inbox.chat_append(sender=sender, to=cog, body=text.strip(), about=prep["about"])
        ans = self.chat_answer(prep, sender, text.strip())
        return self.inbox.chat_append(sender=cog, to=sender, body=ans["text"], about=prep["about"], model=ans["model"], stand_in=ans["stand_in"],
                                      tools=ans["tool_calls"])

    def send(self, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None,
             record: str | None = None, comment_id: str | None = None) -> dict:
        """A message to a person or a cog, optionally about an item (then it is on the item's chatter and its
        @mentions are acted on: see hubproto.mentions)."""
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {"members": {}}
        return self.inbox.send(sender=sender, to=to, subject=subject, body=body, channel=channel,
                               attachments=attachments, bundle=bundle, registry=self.registry, runner=self.runner,
                               kernel=self.kernel, release=meta["digest"] if meta else "", record=record, hub=self, comment_id=comment_id)


_FIND = ("find", "where", "link", "look up", "lookup", "search", "show me", "which", "locate", "open the", "get me")
_STOP = {"find", "the", "a", "an", "please", "provide", "give", "me", "link", "links", "to", "for", "of", "and", "where", "is", "are", "can",
         "you", "i", "my", "our", "it", "with", "show", "search", "look", "up", "lookup", "locate", "which", "get", "open", "what", "do",
         "we", "have", "any", "in", "on", "about", "called", "named", "there", "that", "this", "us", "your", "help", "need", "want"}
_TYPE_WORDS = {"document": "document", "doc": "document", "file": "document", "sop": "document", "bill": "bill", "invoice": "bill",
               "purchase": "purchase-request", "order": "purchase-order", "ncr": "ncr", "nonconformance": "ncr", "capa": "capa",
               "design": "design", "requirement": "requirement", "task": "task", "receipt": "receipt", "item": "item", "part": "item"}


def chat_lookup(call_tool, text: str) -> dict | None:
    """For a chat line that asks to find something, the search a person would run: the words that name it, narrowed by
    a record type the line mentions; if every word together finds nothing, the longest words one at a time."""
    t = (text or "").lower()
    if not any(k in t for k in _FIND):
        return None
    words = [w for w in re.findall(r"[\w.-]+", t) if w not in _STOP]
    rtype = next((_TYPE_WORDS[w.rstrip("s")] for w in words if w.rstrip("s") in _TYPE_WORDS), "")
    named = [w for w in words if w.rstrip("s") not in _TYPE_WORDS] or words
    tries = [{"query": " ".join(named), "type": rtype}] + [{"query": w, "type": rtype} for w in sorted(named, key=len, reverse=True)[:3] if len(named) > 1]
    if rtype:
        tries.append({"query": " ".join(named), "type": ""})
    for args in tries:
        args = {k: v for k, v in args.items() if v}
        if not args.get("query"):
            continue
        try:
            res = call_tool("search_records", {**args, "limit": 8})
        except Exception:
            return None
        if res.get("count"):
            return {"arguments": args, "result": res}
    return {"arguments": tries[0], "result": {"count": 0, "records": [], "note": "nothing matched these words in records this person may read"}} if named else None


def plain_chat(text: str) -> str:
    """A chat bubble shows plain text: Markdown links become their text (record numbers are linked by the messenger),
    bold and italics lose their stars, headings their hashes."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text or "")
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"(?m)^#{1,6}\s+", "", t)
    return t.strip()

