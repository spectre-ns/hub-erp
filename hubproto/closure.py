"""No configuration change without a change order (change control v2, principle 3).

Once the SOPs are documents (``sopdocs.converted``) the configuration in force changes only when the release
automation brings an approved change order (CO) into force. Every other way to change it is refused here, with a
sentence that names the change-order route. ``docs/design/config-paths.md`` lists every path and what happens on it.

The guards sit where configuration comes into force or is proposed for it, so that every caller (the command line,
the shell, a cog's tools) gets the same answer:

* ``Hub.compile(save=True)`` makes a proposal for release: refused unless it compiles the drafts a change order carries
  (``sop_drafts``, which is how ``eco.stage`` compiles). A compile that is not saved (a preview) is allowed.
* ``Hub.review`` decides changes of a proposal: refused unless a change order staged it (the policy owners still sign
  the widenings of a staged proposal).
* ``Hub.release`` brings a proposal into force: refused unless a change order staged it, the release automation acts
  for that change order (``by == "change-order:<CO>"``) and the change order is approved.
* ``Hub.frame_draft`` (editing a Frame in the hub) is refused: the Frame is derived from the SOP documents.

Exempt: everything before the conversion, and the release automation acting for an approved change order. The
conversion follows the first release at once (``after_release``), so on a new hub the only change released without a
change order is the first release itself; a hub that keeps the Frame files as the source instead says so
explicitly (``sopdocs.keep_frame_files``: the pre-conversion mode, for work on the Frames and their test suites).
"""

from __future__ import annotations

from .compile import CompileError

CO_ROUTE = "Configuration changes only through a change order: open one under Quality > Change orders"
APPROVED_STATES = ("approved", "released")  # a change order whose configuration may come into force


class ChangeOrderRequired(CompileError, ValueError):
    """A configuration change refused because it does not come through a change order. A ``CompileError`` (the command
    line exits 1 with the sentence; the shell shows it) and a ``ValueError`` (callers that refuse on ValueError, such
    as a cog's tools and the People page, say it as they say any refusal)."""


def refusal(why: str) -> ChangeOrderRequired:
    """The refusal: what was refused and why, then the change-order route."""
    why = why.strip().rstrip(".")
    return ChangeOrderRequired(f"{why}. {CO_ROUTE}.")


def closed(hub) -> bool:
    """True once configuration changes only through change orders: the SOPs are documents."""
    return hub.sop_source()


def _label(proposal: dict) -> str:
    return f"proposal #{proposal.get('number') or proposal.get('id')}"


def change_order_of(hub, proposal: dict) -> dict | None:
    """The change order that staged this proposal (None when no change order did)."""
    number = proposal.get("eco")
    if not number:
        return None
    return next((r for r in hub.kernel.query(type_="change-order") if r["number"] == number), None)


def check_compile(hub, *, save: bool, sop_drafts: dict | None) -> None:
    """A compile saved as a proposal for release, once the SOPs are documents, compiles a change order's drafts."""
    if not save or sop_drafts or not closed(hub):
        return
    raise refusal("The SOPs are documents: the Frame is compiled from them only for a change order, which stages it from "
                  "the revisions it carries. Nothing was compiled")


def check_review(hub, proposal: dict, by: str) -> None:
    """Deciding a proposal's changes, once the SOPs are documents, is only for a proposal a change order staged."""
    if not closed(hub) or proposal.get("eco"):
        return
    raise refusal(f"{_label(proposal)} was not staged by a change order, so it can never be released now that the SOPs are "
                  "documents; nothing was recorded")


def check_release(hub, proposal: dict, by: str) -> None:
    """Bringing a proposal into force, once the SOPs are documents: only the release automation of the approved change
    order that staged it."""
    if not closed(hub):
        return
    number = proposal.get("eco")
    if not number:
        raise refusal(f"{_label(proposal)} was not staged by a change order: it cannot come into force now that the SOPs are "
                      "documents")
    if by != f"change-order:{number}":
        raise refusal(f"{_label(proposal)} is staged by {number}: it comes into force with its revisions when {number} is "
                      "approved and released, not on its own")
    co = change_order_of(hub, proposal)
    if co is None:
        raise refusal(f"{_label(proposal)} names {number}, which is not a change order of this hub")
    if co["state"] not in APPROVED_STATES:
        raise refusal(f"{number} is {co['state'].replace('-', ' ')}: its configuration comes into force only once it is approved")


def release_blocker(hub, proposal: dict) -> str:
    """Why a person cannot release this proposal (empty when the closure does not stop it): for the proposal page."""
    try:
        check_release(hub, proposal, by="")
    except ChangeOrderRequired as e:
        return str(e)
    return ""


def check_co_approved(co: dict, what: str) -> None:
    """A step of the release automation acts only for an approved change order."""
    if co["state"] not in APPROVED_STATES:
        raise refusal(f"{co['number']} is {co['state'].replace('-', ' ')}: {what} come(s) into force only once it is approved")


def frame_draft_refusal(name: str, doc: dict | None) -> ChangeOrderRequired:
    src = doc["number"] if doc else "its SOP document"
    return refusal(f"The {name} Frame is compiled from {src}: revise that document on a change order")


def stale_reason(released: dict) -> str:
    """Why an open proposal no change order staged is stale after a release, once the SOPs are documents."""
    return (f"#{released.get('number')} was released after it was compiled, and now that the SOPs are documents a proposal "
            f"that no change order staged cannot be released. {CO_ROUTE}")


def on_conversion(hub) -> None:
    """When the SOPs become documents: proposals compiled from the Frame files can no longer be released (they are
    marked stale, with the change-order route), and the record types in force are kept as they are now."""
    from . import recordtypes

    for p in hub.proposals.list():
        if p.get("status") == "open" and not p.get("eco"):
            hub._mark_stale(p, "the SOPs became documents, so a proposal compiled from the Frame files cannot be released. " + CO_ROUTE)
    recordtypes.ensure(hub)
    from .sopdocs import _kept_path

    _kept_path(hub).unlink(missing_ok=True)  # converting ends the opt-out of keeping the Frame files


def after_release(hub, base: str | None, by: str) -> dict | None:
    """After a release: the first release of a hub converts the SOPs into documents at once (``sopdocs.
    convert_after_release``), so no later change is released without a change order. None for any later release."""
    if base is not None:
        return None
    from . import sopdocs

    out = sopdocs.convert_after_release(hub, by=by)
    hub.last_conversion = out
    return out
