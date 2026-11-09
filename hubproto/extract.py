"""Rule extraction from Frame prose.

``ConventionExtractor`` is deterministic. It reads the sentence patterns
described in the ``writing-policy-frames`` Frame and needs no model. The
prose is read by the compile-frame committee's cogs, each with its bundled
model (``hubproto.llm_extract``); both share one output shape (see
``rules.py``) and pass through the same Guards.
"""

from __future__ import annotations

import os
import difflib
import re
from dataclasses import dataclass, field

from .frames import Frame
from .rules import CONDITION_VOCABULARY, NUMBER_WORDS

RULE_SECTIONS = (
    "roles", "members", "groups", "folders", "permissions", "thresholds", "routing",
    "approval criteria", "segregation of duties", "duties", "validation",
)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def singular_role(words: str) -> str:
    parts = words.strip().lower().split()
    if parts:
        last = parts[-1]
        if last.endswith("ies") and len(last) > 4:
            parts[-1] = last[:-3] + "y"
        elif last.endswith(("sses", "ches", "shes", "xes")):
            parts[-1] = last[:-2]
        elif last.endswith("s") and not last.endswith("ss"):
            parts[-1] = last[:-1]
    return "-".join(parts)


def number(text: str) -> float:
    return float(text.replace(",", ""))


_ROLE_ITEM = re.compile(r"^- (?P<name>[a-z][a-z0-9-]*)$")
_MEMBER_ITEM = re.compile(
    r"^- (?P<principal>[a-z][a-z0-9-]*) \((?P<ptype>person|cog)\): (?P<roles>[a-z0-9-]+(?:, [a-z0-9-]+)*)$"
)
_FOLDER_ITEM = re.compile(r"^- (?P<path>[A-Z][\w /-]*)$")
# "- quality-board: carol, bob and qa-manager" or "- quality-managers: everyone holding quality-manager" (hubproto.groups)
_GROUP_ITEM = re.compile(r"^- (?P<name>[a-z][a-z0-9-]*): (?P<rest>.+)$")
_PERMISSION = re.compile(
    r"^(?P<subject>[A-Z][\w -]+?) may (?P<neg>not )?(?P<action>[a-z]+)(?: on)? (?P<object>[a-z][a-z -]*?) in (?P<folder>[A-Z][\w /-]+?)"
    r"(?: when (?:an? |the )?(?P<confirmer>[a-z][a-z -]*?) confirms)?\.$"
)
_COG_SUBJECT = re.compile(r"^The (?P<cog>[a-z][\w-]*) cog$")
_GROUP_SUBJECT = re.compile(r"^(?:The|Members of the) (?P<group>[a-z][\w -]*?) group$")
_PERMISSION_READ = re.compile(
    r"^(?P<subject>[A-Z][\w -]+?) may (?P<neg>not )?read (?P<folder>[A-Z][\w /-]+?)\.$"
)
_THRESHOLD = re.compile(
    r"^The (?P<name>[\w -]+? (?:threshold|tolerance|limit|interval)) is (?P<value>[\d,]+(?:\.\d+)?) (?P<unit>USD|EUR|GBP|percent|days)\.$"
)
_ROUTING = re.compile(
    r"^(?:(?P<approvals>Approvals)|At (?P<title>[A-Z][^,]*?)|(?P<overdue>Overdue items)) in (?P<process>[a-z0-9-]+)"
    r"(?: (?P<op>over|above|under|below|at or under|at or over) (?P<amount>[\d,]+(?:\.\d+)?) (?P<cur>[A-Z]{3})"
    r"| with (?P<risk>low|medium|high) risk"
    r"|(?: for (?P<doctype>[a-z][a-z -]*?)s?)?(?: with a (?P<change>minor|major) change)?)?"
    r" (?P<verb>go to|are reported to) (?P<assignee>the [\w -]+? cog|the [\w -]+?|[a-z][\w-]*)\.$"
)
_SLA = re.compile(r"^(?:(?P<approvals>Approvals)|(?P<title>[A-Z][^,]*?)) in (?P<process>[a-z0-9-]+) (?:is|are) due within (?P<days>\d+) days?\.$")
_PROCESS_TERMINAL = re.compile(r"^Closes as (?P<state>[a-z][a-z-]*)\.$")
_PROCESS_FILES_BY_TYPE = re.compile(r"^(?P<what>[A-Z][a-z]+(?: [a-z]+)*) are filed in (?P<folder>[A-Z][\w /-]+?)\.$")
_PROCESS_TYPE = re.compile(r"^The record type is (?P<t>[a-z][a-z-]*)\.$")
_PROCESS_REJECT = re.compile(r"^Rejections return to (?P<title>[A-Z][^.]+?)\.$")
_CRITERIA = re.compile(
    r"^The (?P<cog>[a-z][\w-]*) cog may approve (?P<process>[a-z0-9-]+) (?P<object>[a-z][a-z -]*?)"
    r"(?: under (?P<amount>[\d,]+(?:\.\d+)?) (?P<cur>[A-Z]{3}))?"
    r"(?: when (?P<conds>.+?))?"
    r"(?:, at most (?P<perday>\d+) (?:approvals? )?a day)?"
    r"(?:, at most (?P<permonth>[\d,]+(?:\.\d+)?) (?P<mcur>[A-Z]{3}) a month)?"
    r"(?:, countersigned by (?:an? |the )?(?P<counter>[a-z][a-z -]*?))?\.$"
)
_SOD = re.compile(
    r"^The (?P<a>[\w -]+?) and the (?P<b>[\w -]+?) of a (?P<object>[\w-]+) must be different people\.$"
)
# "Whoever made a direct configuration change on a change-order is not its only approver.": that person may approve
# it with others, never alone (enforced where the object's approvals are counted).
_SOD_ONLY = re.compile(r"^Whoever (?P<did>[a-z][\w -]+?) on an? (?P<object>[\w-]+) is not its only approver\.$")
# "A person signs a cog's work, never another cog", as configuration (change control 7): any item, or one kind of item.
_SOD_COG = re.compile(r"^A cog's work(?: on an? (?P<object>[\w -]+?))? is signed by a person\.$")
_PROCESS_HEAD = re.compile(r"^## Process: (?P<name>[a-z0-9-]+)$")
_MODAL = re.compile(r"\b(may|must|shall|can|cannot|go to|goes to|are reported to|is due|closes as|threshold|tolerance|limit|interval)\b", re.I)
_PROCESS_FOLDER = re.compile(r"^Records are filed in (?P<folder>[A-Z][\w /-]+?)\.$")
_PROCESS_STEP = re.compile(
    r"^(?P<n>\d+)\. \[(?P<role>[a-z][\w-]*)\] (?P<title>[^()]+?)(?: \((?P<cog>[a-z][\w-]*(?:, ?[a-z][\w-]*)*)\))?$"
)
_COG_HEAD = re.compile(r"^## Cog: (?P<name>[a-z0-9][\w-]*)$")
_SCOPE_HEAD = re.compile(r"^## Scope: (?P<name>[a-z0-9][\w-]*)$")
_PERSONA_HEAD = re.compile(r"^## Persona: (?P<name>[a-z0-9][\w-]*)(?: \((?P<of>cog|role)\))?$")
_COMMITTEE_HEAD = re.compile(r"^## Committee: (?P<name>[a-z0-9][\w-]*)$")
_SECTION_KEY = re.compile(r"^(?P<key>Level|Layers on|Compiles|Members|Agree|Group|Chair|Purpose): ?(?P<rest>.*)$")
_LEVELS = ("company", "site", "department", "process")
# A cog profile is the cog's identity: what it does (About), the model it is bundled with and its persona. What it may do,
# what it follows and its duties are derived from the SOPs; its behaviour (prompt, lenses) is in its persona.
_COG_KEY = re.compile(r"^(?P<key>About|Model|Persona|Prompt from|References|Prompt|Follows|Reviews|Autonomy|Duty|Lenses): ?(?P<rest>.*)$")
_COG_MOVED = {
    "prompt": "a cog's behaviour prompt is in its persona (Behaviour:) and what it does is its About: line",
    "lenses": "lenses are in the cog's persona (Lenses:)",
    "autonomy": "autonomy is derived per action from the SOPs (permissions, steps, and 'when a person confirms' sentences)",
    "follows": "what a cog follows is derived from the SOPs that name it or its roles",
    "reviews": "what a cog reads is derived from the SOPs that name it or its roles",
    "duty": "a duty is a sentence in the SOP that owns the work: 'Every day the <cog> cog <does the task>.' under '## Duties'",
}
# A persona is a behaviour document for cogs, shared by any number of them.
_PERSONA_KEY = re.compile(r"^(?P<key>Tone|Stance|Lenses|Reviews|Escalates|Behaviour|Behavior): ?(?P<rest>.*)$")
STANCES = ("reviewer", "executor", "author", "advisor")
TONES = ("formal", "informal")
_PASSES = re.compile(r"^(?P<n>\d+|one|two|three|four|five|six|once|twice) ?(?:adversarial )?(?:pass(?:es)?|times?|reviews?)?\.?$", re.I)
# The IT SOP's validation classes: "Changes to permissions and approval criteria are high risk."; "High-risk changes need
# a test record." (hubproto.validation reads them from the release).
_VCLASS = re.compile(r"^(?:Changes to )?(?P<what>[A-Za-z].+?) are (?P<risk>high|medium|low) risk\.$")
_VNEED = re.compile(r"^(?P<risk>High|Medium|Low)-risk changes need (?:an? )?(?P<need>.+?)\.$")
_DUTY = re.compile(r"^Every (?P<every>day|working day|week|month) the (?P<cog>[a-z][\w-]*) cog (?P<task>[a-z].+\.)$")
# The table every document ends with: who has a part in it (a role, a cog by name, later a group) and what. It says who
# must read the document (people) and what a cog follows.
RESPONSIBILITIES = "roles and responsibilities"
_TABLE_ROW = re.compile(r"^\|(?P<cells>.*)\|$")
_TABLE_RULE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_DOC_REF = re.compile(r"\b[A-Z]{2,}-\d[\w-]*\b")
_OPS = {"over": ">", "above": ">", "under": "<", "below": "<", "at or under": "<=", "at or over": ">="}


@dataclass
class Extraction:
    rules: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)  # where the prose is ambiguous (hubproto.llm_extract): proposal["questions"]


_FIELD_ASSIGNEES = {"the assignee": "assignee", "the owner": "owner", "the record owner": "owner", "the author": "author",
                    "the requester": "requested_by", "the auditor": "auditor", "the chair": "chair",
                    "the evaluator": "evaluator", "the reporter": "reported_by"}


def object_of(title: str) -> str:
    """'File the training record' -> 'training-record'; 'Extract the bill' -> 'bill'."""
    words = title.strip().split()
    if len(words) > 1:
        words = words[1:]
    while words and words[0].lower() in ("the", "a", "an", "this"):
        words = words[1:]
    return slug(" ".join(words)) or slug(title)


def permission_subject(text: str) -> str:
    """'Finance clerks' -> 'role:finance-clerk'; 'The stores cog' -> 'cog:stores' (a permission that names one cog); 'The
    quality-board group' -> 'group:quality-board'. A plural that names a group rather than a role ("Administrators", the
    administrators group) is made a group reference once every Frame is read (``hubproto.groups.resolve_group_refs``)."""
    m = _COG_SUBJECT.match(text.strip())
    if m:
        return "cog:" + m.group("cog")
    m = _GROUP_SUBJECT.match(text.strip())
    if m:
        return "group:" + slug(m.group("group"))
    return "role:" + singular_role(text)


def responsibility_who(cell: str) -> str:
    """'Quality managers' -> 'role:quality-manager'; 'the it-admin cog' -> 'cog:it-admin'; 'the auditors group' ->
    'group:auditors'."""
    words = cell.strip().strip("*_`").strip()
    m = re.match(r"^(?:the |The )?(?P<name>[a-z][\w-]*) cog$", words)
    if m:
        return "cog:" + m.group("name")
    m = re.match(r"^(?:the |The )?(?P<name>[\w -]+?) group$", words)
    if m:
        return "group:" + slug(m.group("name"))
    words = re.sub(r"^(?:the|a|an|every|all|any) ", "", words, flags=re.I)
    return "role:" + singular_role(words)


def responsibility_rule(line: str, frame, n: int) -> dict | None:
    """A row of the Roles and responsibilities table: {"kind": "responsibility", "frame", "who", "text"}. None for the
    header row and the rule under it; {"kind": "unparsed"} for a row that is not two cells."""
    if _TABLE_RULE.match(line):
        return None
    m = _TABLE_ROW.match(line)
    if not m:
        return {"kind": "unparsed"}
    cells = [c.strip() for c in m.group("cells").split("|")]
    if len(cells) != 2 or not cells[0]:
        return {"kind": "unparsed"}
    if cells[0].lower() in ("role", "roles", "who") and cells[1].lower().startswith("responsibilit"):
        return None  # the header
    # The frame keeps two documents' identical rows apart.
    return {"kind": "responsibility", "frame": frame.name, "who": responsibility_who(cells[0]), "text": cells[1],
            "source": {"frame": frame.name, "version": frame.version, "line": n, "text": line, "path": frame.relpath}}


def who_words(who: str) -> str:
    """'role:quality-manager' -> 'Quality manager'; 'cog:stores' -> 'the stores cog' (as a table row names them)."""
    kind, _, name = (who or "").partition(":")
    if kind == "cog":
        return f"the {name} cog"
    if kind == "group":
        return f"the {name.replace('-', ' ')} group"
    return name.replace("-", " ").capitalize()


def add_responsibility_row(text: str, who: str, responsibility: str) -> str:
    """The Frame or SOP text with a row for ``who`` ('role:x', 'cog:x') in its Roles and responsibilities table (the
    section and the table are added at the end when missing); unchanged when the table already lists them. For writers
    of prose (the qa-manager's drafting of a direct change): a new step, route, criterion, duty or create/approve/update
    permission for a role or cog needs its row, or the compile warns."""
    lines = text.splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.strip().lower() == "## " + RESPONSIBILITIES), None)
    row = f"| {who_words(who)} | {' '.join(responsibility.split()).replace('|', '/')} |"
    if head is None:
        body = text.rstrip("\n")
        return body + "\n\n## Roles and responsibilities\n| Role | Responsibilities |\n|---|---|\n" + row + "\n"
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    last = head
    for i in range(head + 1, end):
        ln = lines[i].strip()
        if ln.startswith("|"):
            last = i
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells and not _TABLE_RULE.match(ln) and responsibility_who(cells[0]) == who:
                return text  # listed already
    if last == head:  # a section with no table yet
        lines[head + 1:head + 1] = ["| Role | Responsibilities |", "|---|---|", row]
    else:
        lines.insert(last + 1, row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def responsibilities_of(frame) -> tuple[list[dict], list[dict]]:
    """The rows of a Frame's Roles and responsibilities table, read whatever the Frame's compile mode (a work
    instruction is not compiled, but its table still says who must read it). Returns (rules, notes)."""
    rules, notes, inside = [], [], False
    for n, raw in enumerate(frame.lines, start=frame.body_start):
        line = raw.strip()
        if line.startswith("#"):
            inside = line.startswith("## ") and line[3:].strip().lower() == RESPONSIBILITIES
            continue
        if not inside or not line.startswith("|"):
            continue
        r = responsibility_rule(line, frame, n)
        if r is None:
            continue
        if r["kind"] == "unparsed":
            notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                          "note": "a Roles and responsibilities row reads '| <role, or the <name> cog> | <what they do> |'"})
            continue
        rules.append(r)
    return rules, notes


def _assignee(text: str) -> str:
    text = text.strip()
    if text in _FIELD_ASSIGNEES:
        return "field:" + _FIELD_ASSIGNEES[text]
    if text.startswith("the ") and text.endswith(" cog"):
        return "cog:" + slug(text[4:-4])
    if text.startswith("the "):
        return "role:" + slug(text[4:])
    return "person:" + slug(text)


# --------------------------------------------------------------------------- processes as graphs (change control 10.2)
# Inside a ``## Process:`` section, instead of numbered steps:
#   Statuses: Open (to do), Awaiting disposition (in progress, due within 5 days), Closed (done, completed), ...
#   Starts in Open ("Open the nonconformance"): the staff; needs title.
#   From Open to Awaiting disposition: automatically, after the ncr classifier, the record filer and the risk scorer.
#   From Awaiting disposition to Closed ("Disposition"): the quality manager decides, not the author; needs disposition;
#       then the disposition applier and the capa opener run.
#   From any status except done to Cancelled ("Cancel"): the quality manager.
# (hubproto.process_graph builds the graph; DEV-NOTES.md, "Sentence grammar", is the reference.)
_GRAPH_STATUSES = re.compile(r"^Statuses: (?P<rest>.+)\.$")
_GRAPH_STATUS_ITEM = re.compile(r"(?P<name>[A-Z][^()\[\],]*?)(?: \[(?P<id>[a-z0-9][a-z0-9-]*)\])? \((?P<attrs>[^()]*)\)(?P<sep>, | and |$)")
_GRAPH_FROM = re.compile(r'^From (?P<head>[^:"]+?(?: \("[^"]+"(?: \[[a-z0-9][a-z0-9-]*\])?\))?): (?P<rest>.+)\.$')
_GRAPH_STARTS = re.compile(r'^Starts in (?P<head>[^:"]+?(?: \("[^"]+"(?: \[[a-z0-9][a-z0-9-]*\])?\))?): (?P<rest>.+)\.$')
_GRAPH_LABEL = re.compile(r'^(?P<head>.+?)(?: \("(?P<name>[^"]+)"(?: \[(?P<id>[a-z0-9][a-z0-9-]*)\])?\))?$')
_GRAPH_AUTO = re.compile(r"^automatically(?: when (?P<cond>.+?))?(?:,? after (?P<acts>.+))?$")
_GRAPH_COG = re.compile(r"^the (?P<cog>[a-z][\w -]*?) cog decides(?: within its approval criteria)?, else (?P<fallback>the [\w -]+)$")
# "the design-review committee decides": every member of the committee agrees (consensus), or the item goes back (groups)
_GRAPH_COMMITTEE = re.compile(r"^the (?P<committee>[a-z][\w -]*?) committee decides(?: by consensus)?$")
# "the owner or an approver": the owner, or anyone who may approve the process's object (numbered steps' Cancel and Retry).
_GRAPH_PERSON = re.compile(r"^(?P<who>the [\w -]+?|anyone)(?P<approvers> or an approver)?(?: decides)?(?:, not (?P<not>.+))?$")
_GRAPH_CATEGORY = {"to do": "todo", "in progress": "in_progress", "done": "done"}
_GRAPH_OUTCOMES = ("completed", "rejected", "cancelled", "superseded")
_GRAPH_FIELD_ROLES = {"the assignee": "assignee", "the owner": "owner", "the author": "author", "the requester": "requester",
                      "the auditor": "auditor", "the chair": "chair", "the evaluator": "evaluator", "the reporter": "reporter"}


def _words_list(text: str) -> list[str]:
    """'a, b and c' -> ['a', 'b', 'c']."""
    parts = [p.strip() for p in text.split(", ")]
    last = parts.pop() if parts else ""
    parts += [p.strip() for p in re.split(r" and | or ", last)]
    return [p for p in parts if p]


def _status_ref(process: dict, text: str) -> str | None:
    text = text.strip()
    for s in process.get("statuses") or []:
        if text.lower() in (s["name"].lower(), s["id"]):
            return s["id"]
    return None


def _status_refs(process: dict, text: str) -> tuple[list[str], str]:
    """Statuses named in a list ('A, B and C'); a name containing 'and' is tried whole first."""
    one = _status_ref(process, text)
    if one:
        return [one], ""
    ids = []
    for part in _words_list(text):
        sid = _status_ref(process, part)
        if not sid:
            return [], f"{part!r} is not one of the statuses this process declares"
        ids.append(sid)
    return ids, ""


def _role_of(words: str) -> str:
    words = words.strip()
    if words in _GRAPH_FIELD_ROLES:
        return _GRAPH_FIELD_ROLES[words]
    if words == "anyone":
        return "system"
    return singular_role(words[4:] if words.startswith("the ") else words)


def _graph_gate(text: str) -> tuple[dict, list, str]:
    """(gate, actions written in it, error) for the first clause after the colon."""
    m = _GRAPH_AUTO.match(text)
    if m:
        gate = {"kind": "auto", "when": "actions-succeed"}
        if m.group("cond"):
            key = CONDITION_VOCABULARY.get(m.group("cond").strip())
            if not key:
                return {}, [], f"“{m.group('cond')}” is not a condition the hub can evaluate (see the condition vocabulary)"
            gate["when"] = key
        acts, err = _graph_actions(m.group("acts") or "")
        return gate, acts, err
    m = _GRAPH_COG.match(text)
    if m:
        return {"kind": "cog", "cog": slug(m.group("cog")), "fallback": _role_of(m.group("fallback"))}, [], ""
    m = _GRAPH_COMMITTEE.match(text)
    if m:
        return {"kind": "committee", "committee": slug(m.group("committee"))}, [], ""
    m = _GRAPH_PERSON.match(text)
    if m:
        gate = {"kind": "person", "role": _role_of(m.group("who"))}
        if m.group("approvers"):
            gate["also"] = "approvers"
        if m.group("not"):
            nots = []
            for part in _words_list(m.group("not")):
                w = re.match(r'^whoever decided "(?P<name>[^"]+)"$', part)
                nots.append("@" + w.group("name") if w else _role_of(part))
            gate["not"] = nots
        return gate, [], ""
    return {}, [], f"“{text}” does not say who decides (the <role>, the <cog> cog decides, else the <role>, the <name> committee decides, or automatically)"


def _graph_actions(text: str) -> tuple[list, str]:
    out: list = []
    for part in _words_list(text) if text.strip() else []:
        m = re.match(r"^notify (?P<who>the [\w -]+)$", part)
        if m:
            role = _role_of(m.group("who"))
            out.append({"notify": ("field:" if role in _GRAPH_FIELD_ROLES.values() else "role:") + role})
            continue
        m = re.match(r"^set (?P<field>[a-z][a-z ]*?) to (?P<value>.+)$", part)
        if m:
            v = m.group("value").strip()
            out.append({"set": {m.group("field").strip().replace(" ", "_"): {"true": True, "false": False}.get(v, v)}})
            continue
        if part.lower().startswith("the ") and " with the " in part:
            # Several cogs review together and their findings merge ("the quality assistant with the purchasing assistant").
            out.append({"swarm": [slug(c) for c in re.split(r" with the ", part[4:], flags=re.I)]})
            continue
        m = re.match(r"^the (?P<cog>[\w -]+)$", part, re.I)
        if not m:
            return [], f"“{part}” is not an action (the <cog>, the <cog> with the <cog>, notify the <role>, or set <field> to <value>)"
        out.append(slug(m.group("cog")))
    return out, ""


def _graph_statement(process: dict, line: str, n: int, src) -> str | None:
    """Read one graph sentence into the process. None: not a graph sentence; "": read; otherwise why it does not read."""
    m = _GRAPH_STATUSES.match(line)
    if m:
        if process.get("steps"):
            return "a process is written either as numbered steps or as statuses and transitions, not both"
        rest, pos, statuses = m.group("rest"), 0, []
        for it in _GRAPH_STATUS_ITEM.finditer(rest):
            if it.start() != pos:
                break
            pos = it.end()
            attrs = [a.strip() for a in it.group("attrs").split(",") if a.strip()]
            cat = _GRAPH_CATEGORY.get(attrs[0] if attrs else "")
            if not cat:
                return f"status {it.group('name')!r}: say its category first (to do, in progress or done)"
            st = {"id": it.group("id") or slug(it.group("name")), "name": it.group("name").strip(), "category": cat, "line": n}
            for a in attrs[1:]:
                due = re.match(r"^due within (\d+) days?$", a)
                if due:
                    st["sla_days"] = int(due.group(1))
                elif a in _GRAPH_OUTCOMES and cat == "done":
                    st["outcome"] = a
                else:
                    return f"status {st['name']!r}: {a!r} is not an outcome of a done status or a due date ('due within N days')"
            if cat == "done":
                st.setdefault("outcome", "completed")
            statuses.append(st)
        if pos != len(rest) or not statuses:
            return "a Statuses line lists 'Name (to do|in progress|done[, outcome][, due within N days])' items separated by commas"
        ids = [s["id"] for s in statuses]
        if len(set(ids)) != len(ids):
            return "two statuses have the same id; give one an explicit id in square brackets"
        process["statuses"] = statuses
        process.setdefault("transitions", [])
        process["statuses_source"] = src(n, line)
        return ""
    starts = _GRAPH_STARTS.match(line)
    m = starts or _GRAPH_FROM.match(line)
    if not m:
        if line.startswith(("From ", "Starts in ", "Statuses")):
            return ("this process sentence does not read: 'From <status> to <status> (\"Name\"): <who decides>[; needs <fields>]"
                    "[; only if <condition>][; then <actions> run].'")
        return None
    if not process.get("statuses"):
        return "declare the statuses ('Statuses: ...') before the transitions"
    lab = _GRAPH_LABEL.match(m.group("head"))
    head, name, ident = lab.group("head"), lab.group("name"), lab.group("id")
    t: dict = {"type": "initial" if starts else "directed"}
    if starts:
        to = _status_ref(process, head)
        if not to:
            return f"{head!r} is not one of the statuses this process declares"
        t["to"] = to
    else:
        split = None
        for mm in re.finditer(" to ", head):
            frm, to = head[:mm.start()], _status_ref(process, head[mm.end():])
            if to:
                split = (frm, to)
                break
        if split is None:
            return f"the status after 'to' in {head!r} is not one this process declares"
        frm, t["to"] = split
        g = re.match(r"^any status(?: except (?P<exc>.+))?$", frm)
        if g:
            t["type"] = "global"
            exc = g.group("exc")
            if exc is None:
                t["except"] = []
            elif exc.strip() == "done":
                t["except"] = [s["id"] for s in process["statuses"] if s["category"] == "done"]
            else:
                ids, err = _status_refs(process, exc)
                if err:
                    return err
                t["except"] = ids
        else:
            ids, err = _status_refs(process, frm)
            if err:
                return err
            t["from"] = ids
    clauses = [c.strip() for c in m.group("rest").split("; ")]
    gate, actions, err = _graph_gate(clauses[0])
    if err:
        return err
    if starts and gate.get("kind") != "person":
        return "a process is started by a person (the <role>), not automatically, by a cog or by a committee"
    fields, validators = [], []
    for c in clauses[1:]:
        if c.startswith("needs "):
            fields += [w.replace(" ", "_") for w in _words_list(c[len("needs "):])]
        elif c.startswith("then "):
            acts, err = _graph_actions(re.sub(r" runs?$", "", c[len("then "):]))
            if err:
                return err
            actions += acts
        elif c.startswith("only if "):
            for part in _words_list(c[len("only if "):]):
                v = re.match(r"^(?P<status>.+) was visited$", part)
                if v and _status_ref(process, v.group("status")):
                    validators.append({"status_visited": _status_ref(process, v.group("status"))})
                elif CONDITION_VOCABULARY.get(part):
                    validators.append({"condition": CONDITION_VOCABULARY[part]})
                else:
                    return f"“{part}” is not a check the hub can make ('<status> was visited', or a condition of the vocabulary)"
        else:
            return f"“{c}” does not read (needs <fields>; only if <condition>; then <actions> run)"
    if fields:
        validators.insert(0, {"fields_set": list(fields)})
    if gate.get("kind") == "auto":
        default_name = "Automatically"
    elif starts:
        default_name = "Start"
    else:
        default_name = "To " + next(s["name"] for s in process["statuses"] if s["id"] == t["to"])
    t["name"] = name or default_name
    if ident:
        t["id"] = ident
    elif name:
        t["id"] = slug(name)
    elif starts:
        t["id"] = "start"
    else:
        t["id"] = f"{(t.get('from') or ['any'])[0]}-to-{t['to']}"
    if any(x["id"] == t["id"] for x in process["transitions"]):
        return f"two transitions have the id {t['id']!r}; name them differently or give an id in square brackets"
    t.update({"gate": gate, "validators": validators, "actions": actions, "fields": fields, "line": n, "source": src(n, line)})
    process["transitions"].append(t)
    return ""


def _close_graph(process: dict) -> list[str]:
    """Finish a process written as a graph: its start, its object and its closing status. Returns errors."""
    if not process.get("statuses"):
        return []
    errors = []
    ts = process.setdefault("transitions", [])
    by_name = {t["name"]: t["id"] for t in ts}
    for t in ts:
        g = t.get("gate") or {}
        if g.get("not"):
            resolved = []
            for x in g["not"]:
                if x.startswith("@"):
                    if x[1:] not in by_name:
                        errors.append(f"{t['name']!r}: no transition is named {x[1:]!r}")
                        continue
                    resolved.append(by_name[x[1:]])
                else:
                    resolved.append(x)
            g["not"] = resolved
    init = next((t for t in ts if t["type"] == "initial"), None)
    if init is None:
        init = {"id": "start", "type": "initial", "to": process["statuses"][0]["id"], "name": "Start",
                "gate": {"kind": "person", "role": "system"}, "validators": [], "actions": [], "fields": []}
        ts.insert(0, init)
    if not process.get("object") and init.get("name") not in (None, "", "Start"):
        process["object"] = object_of(init["name"])
    ids = {s["id"] for s in process["statuses"]}
    if process.get("terminal") not in ids:
        process["terminal"] = next((s["id"] for s in process["statuses"] if s["category"] == "done" and s.get("outcome") == "completed"),
                                   process.get("terminal") or "closed")
    return errors


class ConventionExtractor:
    name = "convention"

    def extract(self, frame: Frame) -> Extraction:
        out = Extraction()
        mode = str(frame.frontmatter.get("compile", "convention"))
        if mode == "none":
            # Not compiled; only its Roles and responsibilities table is read (who must read it).
            out.rules, out.notes = responsibilities_of(frame)
            return out
        if mode == "guided":
            out.notes.append({"frame": frame.name, "line": 0, "text": "",
                              "note": "guided mode requested; the convention extractor read this Frame (the compile-frame committee reads prose with models)"})
        section = ""
        process: dict | None = None

        def src(n: int, text: str) -> dict:
            return {"frame": frame.name, "version": frame.version, "line": n, "text": text, "path": frame.relpath}

        def add(kind: str, n: int, text: str, **fields):
            out.rules.append({"kind": kind, **fields, "source": src(n, text)})

        def close_process():
            nonlocal process
            if process is not None:
                for note in _close_graph(process):
                    out.notes.append({"frame": frame.name, "line": process["source"]["line"], "text": process["source"]["text"],
                                      "severity": "error", "note": note})
                out.rules.append(process)
                process = None

        cog_profile: dict | None = None
        cog_key = ""

        def close_cog():
            nonlocal cog_profile, cog_key
            if cog_profile is not None:
                cog_profile["about"] = " ".join(cog_profile["about"]).strip()
                out.rules.append(cog_profile)
                cog_profile = None
                cog_key = ""

        layered: dict | None = None  # the scope, persona or committee section being read
        persona_key = ""

        def close_layered():
            nonlocal layered, persona_key
            if layered is not None:
                if layered["kind"] == "persona":
                    layered["behaviour"] = " ".join(layered["behaviour"]).strip()
                    layered["escalates"] = " ".join(layered["escalates"]).strip()
                    if not layered["stance"]:
                        out.notes.append({"frame": frame.name, "line": layered["source"]["line"], "text": layered["source"]["text"], "severity": "error",
                                          "note": f"persona {layered['name']} says no Stance: (one of {', '.join(STANCES)})"})
                out.rules.append(layered)
                layered = None
                persona_key = ""

        def name_list(rest: str) -> list[str]:
            return [slug(x) for x in re.split(r",| and ", rest.rstrip(".")) if x.strip()]

        skipping = False  # inside a section of a shape that is gone (a role persona): its lines are not read
        for n, raw in enumerate(frame.lines, start=frame.body_start):
            line = raw.strip()
            if not line:
                continue
            m = _SCOPE_HEAD.match(line) or _PERSONA_HEAD.match(line) or _COMMITTEE_HEAD.match(line)
            if m:
                close_process()
                close_cog()
                close_layered()
                section = "layered"
                skipping = False
                if m.re is _COMMITTEE_HEAD:
                    layered = {"kind": "committee", "name": m.group("name"), "group": "", "chair": "", "purpose": "", "members": [],
                               "quorum": 0, "source": src(n, line)}
                elif m.re is _PERSONA_HEAD and m.group("of"):
                    out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                      "note": "a persona is a behaviour document for cogs now ('## Persona: <name>' with Tone:, Stance:, Lenses:, "
                                              "Reviews:, Escalates:, Behaviour:); what a cog or a role reads is derived from the SOPs that name it"})
                    skipping = True
                elif m.re is _PERSONA_HEAD:
                    layered = {"kind": "persona", "name": m.group("name"), "tone": "", "stance": "", "lenses": [], "passes": 0,
                               "escalates": [], "behaviour": [], "source": src(n, line)}
                else:
                    layered = {"kind": "scope", "name": m.group("name"), "level": "", "compiles": [], "layers_on": [], "source": src(n, line)}
                continue
            if skipping and not line.startswith("#"):
                continue
            skipping = False
            if layered is not None and layered["kind"] == "persona" and not line.startswith("#"):
                km = _PERSONA_KEY.match(line)
                if km:
                    persona_key = km.group("key").lower().replace("behavior", "behaviour")
                    rest = km.group("rest").strip()
                    if persona_key == "tone":
                        layered["tone"] = rest.rstrip(".").lower()
                        if layered["tone"] not in TONES:
                            out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                              "note": f"a persona's tone is {' or '.join(TONES)}"})
                    elif persona_key == "stance":
                        layered["stance"] = rest.rstrip(".").lower()
                        if layered["stance"] not in STANCES:
                            out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                              "note": f"a persona's stance is one of {', '.join(STANCES)}"})
                    elif persona_key == "lenses":
                        layered["lenses"] = name_list(rest)
                    elif persona_key == "reviews":
                        pm = _PASSES.match(rest)
                        word = (pm.group("n").lower() if pm else "")
                        passes = int(word) if word.isdigit() else {**NUMBER_WORDS, "one": 1, "once": 1, "twice": 2}.get(word)
                        if passes is None:
                            out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                              "note": "say how many adversarial passes it makes before it signs, e.g. 'Reviews: 3 adversarial passes'"})
                        else:
                            layered["passes"] = passes
                    elif persona_key in ("escalates", "behaviour"):
                        layered[persona_key] = [rest] if rest else []
                elif persona_key in ("escalates", "behaviour"):
                    layered[persona_key].append(line)
                else:
                    out.notes.append({"frame": frame.name, "line": n, "text": line,
                                      "note": "unparsed line in a persona section (Tone:, Stance:, Lenses:, Reviews:, Escalates:, Behaviour:)"})
                continue
            if layered is not None and not line.startswith("## "):
                km = _SECTION_KEY.match(line)
                if km:
                    key, rest = km.group("key").lower(), km.group("rest").strip()
                    if key == "level":
                        layered["level"] = rest.rstrip(".").lower()
                    elif key == "layers on":
                        layered["layers_on"] = name_list(rest)
                    elif key == "compiles":
                        layered["compiles"] = name_list(rest)
                    elif key == "members":
                        layered["members"] = name_list(rest)
                    elif key == "agree":  # kept for earlier texts: a committee decides by consensus, every member agreeing
                        agree = re.match(r"^(\d+)(?: of \d+)?", rest.strip())
                        layered["quorum"] = int(agree.group(1)) if agree else 0
                    elif key == "group" and layered["kind"] == "committee":
                        layered["group"] = slug(re.sub(r"^the |\s+group$", "", rest.rstrip(".").strip().lower()))
                    elif key == "chair" and layered["kind"] == "committee":
                        layered["chair"] = slug(rest.rstrip(".").strip())
                    elif key == "purpose" and layered["kind"] == "committee":
                        layered["purpose"] = rest.strip()
                    else:
                        out.notes.append({"frame": frame.name, "line": n, "text": line,
                                          "note": f"{km.group('key')}: is not a line of a {layered['kind']} section"})
                elif layered["kind"] == "committee" and layered.get("purpose") and not line.startswith("#"):
                    layered["purpose"] += " " + line  # the purpose runs on
                else:
                    out.notes.append({"frame": frame.name, "line": n, "text": line,
                                      "note": "unparsed line in a scope or committee section (Level:, Layers on:, Compiles:; Group:, Chair:, "
                                              "Purpose:, Members:)"})
                continue
            if layered is not None:
                close_layered()
            m = _COG_HEAD.match(line)
            if m:
                close_process()
                close_cog()
                section = "cog"
                cog_profile = {"kind": "cog", "name": m.group("name"), "about": [], "model": "", "persona": "",
                               "prompt_from": "", "references": [], "source": src(n, line)}
                continue
            if section == "cog" and cog_profile is not None and not line.startswith("#"):
                m = _COG_KEY.match(line)
                if m:
                    cog_key = m.group("key").lower()
                    rest = m.group("rest").strip()
                    if cog_key == "model":
                        cog_profile["model"] = rest.rstrip(".")
                    elif cog_key == "about":
                        cog_profile["about"] = [rest] if rest else []
                    elif cog_key == "persona":
                        cog_profile["persona"] = slug(rest.rstrip(".").removeprefix("persona-"))
                        cog_key = ""
                    elif cog_key == "prompt from":
                        refs = _DOC_REF.findall(rest)
                        cog_profile["prompt_from"] = refs[0] if refs else rest.strip().rstrip(".")
                        cog_key = ""
                    elif cog_key == "references":
                        cog_profile["references"] = _DOC_REF.findall(rest) or [x.strip() for x in rest.rstrip(".").split(",") if x.strip()]
                        cog_key = ""
                    else:  # a line that left the profile (Prompt:, Lenses:, Autonomy:, Follows:, Reviews:, Duty:)
                        out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                          "note": f"a cog profile is its identity (About:, Model:, Persona:): {_COG_MOVED[cog_key]}"})
                        cog_key = "moved"
                elif cog_key == "about":
                    cog_profile["about"].append(line)
                elif cog_key == "moved":
                    continue  # the rest of a moved Prompt: paragraph
                else:
                    out.notes.append({"frame": frame.name, "line": n, "text": line,
                                      "note": "unparsed line in a cog section (About:, Model:, Persona:, Prompt from:, References:)"})
                continue
            m = _PROCESS_HEAD.match(line)
            if m:
                close_process()
                close_cog()
                section = "process"
                process = {"kind": "process", "name": m.group("name"), "folder": "", "object": "",
                           "steps": [], "terminal": "closed", "reject_to": None, "source": src(n, line)}
                continue
            if line.startswith("## "):
                close_process()
                close_cog()
                section = line[3:].strip().lower()
                if section not in RULE_SECTIONS and section != RESPONSIBILITIES and not section.startswith("process:"):
                    near = difflib.get_close_matches(section, RULE_SECTIONS, n=1, cutoff=0.75)
                    if near:
                        out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                          "note": f"unknown section heading; did you mean '## {near[0].title()}'? Its sentences would be ignored"})
                    elif section.startswith("process"):
                        out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                          "note": "a process heading reads '## Process: <name>'; this one would be ignored"})
                continue
            if line.startswith("#"):
                close_process()
                close_cog()
                section = ""
                continue
            if section == "process" and process is not None:
                m = _PROCESS_FOLDER.match(line)
                if m:
                    process["folder"] = m.group("folder")
                    continue
                m = _PROCESS_FILES_BY_TYPE.match(line)
                if m and m.group("what") != "Records":
                    # "Policies are filed in Policy.": documents of that type stay in (and are filed in) their own folder.
                    process.setdefault("files_by_type", {})[singular_role(m.group("what").lower())] = m.group("folder")
                    continue
                m = _PROCESS_TERMINAL.match(line)
                if m:
                    process["terminal"] = m.group("state")
                    continue
                m = _PROCESS_TYPE.match(line)
                if m:
                    process["record_type"] = m.group("t")
                    continue
                m = _PROCESS_REJECT.match(line)
                if m:
                    process["reject_to"] = slug(m.group("title"))
                    continue
                graph = _graph_statement(process, line, n, src)
                if graph is not None:
                    if graph:
                        out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error", "note": graph})
                    continue
                m = _PROCESS_STEP.match(line)
                if m:
                    if process.get("statuses"):
                        out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                          "note": "a process is written either as numbered steps or as statuses and transitions, not both"})
                        continue
                    title = m.group("title").strip()
                    cog_names = [c.strip() for c in (m.group("cog") or "").split(",") if c.strip()]
                    step = {"n": int(m.group("n")), "id": slug(title), "title": title,
                            "role": m.group("role"), "cog": cog_names[0] if len(cog_names) == 1 else ("swarm" if cog_names else None),
                            "swarm": cog_names if len(cog_names) > 1 else None,
                            "kind": "cog" if m.group("cog") else "checkpoint",
                            "line": n}
                    if not process["steps"]:
                        process["object"] = object_of(title)
                    process["steps"].append(step)
                    continue
                out.notes.append({"frame": frame.name, "line": n, "text": line,
                                  "note": "unparsed line in a process section"})
                continue

            if section == RESPONSIBILITIES:
                if not line.startswith("|"):
                    continue  # prose around the table
                r = responsibility_rule(line, frame, n)
                if r is not None and r["kind"] == "unparsed":
                    out.notes.append({"frame": frame.name, "line": n, "text": line, "severity": "error",
                                      "note": "a Roles and responsibilities row reads '| <role, or the <name> cog> | <what they do> |'"})
                elif r is not None:
                    out.rules.append(r)
                continue
            if section not in RULE_SECTIONS:
                continue
            matched = self._match_sentence(line, n, section, add)
            if not matched:
                note = {"frame": frame.name, "line": n, "text": line, "note": f"unparsed sentence in the {section!r} section"}
                if section == "validation":
                    note["severity"] = "error"
                    note["note"] = ("a validation sentence reads '<Changes to> <permissions, routing, ...> are high|medium|low risk.' or "
                                    "'High|Medium|Low-risk changes need a test record|a test note|the review of the delta.'; this one names "
                                    "nothing the hub can class")
                elif section == "duties":
                    note["severity"] = "error"
                    note["note"] = "a duty reads 'Every day|week|month the <cog> cog <does the task>.'; this one would be silently dropped"
                elif section == "groups":
                    from .groups import parse_group

                    gm = _GROUP_ITEM.match(line)
                    note["severity"] = "error"
                    note["note"] = ((parse_group(gm.group("rest"))[2] if gm else "")
                                    or "a group reads '- <name>: <member>, <member> and <member>' or '- <name>: everyone holding <role>'")
                elif _MODAL.search(line):
                    note["severity"] = "error"
                    note["note"] = (f"this sentence in the {section!r} section looks like a rule but does not parse "
                                    "(see the writing-policy-frames reference); it would be silently dropped")
                out.notes.append(note)
        close_process()
        close_cog()
        close_layered()
        return out

    def _match_sentence(self, line: str, n: int, section: str, add) -> bool:
        m = _MEMBER_ITEM.match(line)
        if m and section == "members":
            add("member", n, line, principal=m.group("principal"), principal_type=m.group("ptype"),
                roles=[r.strip() for r in m.group("roles").split(",")])
            return True
        m = _GROUP_ITEM.match(line)
        if m and section == "groups":
            from .groups import parse_group

            listed, roles, err = parse_group(m.group("rest"))
            if err:
                return False
            add("group", n, line, name=m.group("name"), members=listed, roles=roles)
            return True
        m = _ROLE_ITEM.match(line)
        if m and section == "roles":
            add("role", n, line, name=m.group("name"))
            return True
        m = _FOLDER_ITEM.match(line)
        if m and section == "folders":
            add("folder", n, line, path=m.group("path").strip())
            return True
        m = _PERMISSION.match(line)
        if m:
            confirmer = m.group("confirmer")
            if confirmer and m.group("neg"):
                return False  # "may not ... when a person confirms" says nothing a rule can hold
            obj = m.group("object").strip()
            fields = {"subject": permission_subject(m.group("subject")), "action": m.group("action"),
                      "object": "*" if obj in ("anything", "everything", "items", "records") else singular_role(obj),
                      "resource": "folder:" + m.group("folder").strip(),
                      "effect": "deny" if m.group("neg") else ("propose" if confirmer else "allow")}
            if confirmer:
                # "when a storekeeper confirms": the cog's action waits for a person in that role (a person: whoever gave the task).
                fields["confirmer"] = "person" if confirmer.strip() == "person" else singular_role(confirmer)
            add("permission", n, line, **fields)
            return True
        m = _PERMISSION_READ.match(line)
        if m:
            add("permission", n, line, subject=permission_subject(m.group("subject")),
                action="read", object="*", resource="folder:" + m.group("folder").strip(),
                effect="deny" if m.group("neg") else "allow")
            return True
        if section == "validation":
            from .validation import NEEDS, kinds_named

            m = _VNEED.match(line)
            if m and NEEDS.get(m.group("need").lower()):
                add("validation_need", n, line, risk=m.group("risk").lower(), evidence=NEEDS[m.group("need").lower()])
                return True
            m = _VCLASS.match(line)
            kinds = sorted(kinds_named(m.group("what"))) if m else []
            if m and kinds:
                add("validation_class", n, line, risk=m.group("risk").lower(), kinds=kinds)
                return True
            return False
        m = _DUTY.match(line)
        if m and section == "duties":
            # A standing goal the duty runner takes up for the cog ("Every day the quality-assistant cog finds ...").
            add("duty", n, line, cog=m.group("cog"), task=m.group("task").strip(), every=m.group("every").replace("working ", ""))
            return True
        m = _THRESHOLD.match(line)
        if m:
            add("threshold", n, line, name=slug(m.group("name")), value=number(m.group("value")),
                unit=m.group("unit"))
            return True
        m = _ROUTING.match(line)
        if m:
            conds = []
            if m.group("op"):
                conds.append({"field": "amount", "op": _OPS[m.group("op")], "value": number(m.group("amount")),
                              "currency": m.group("cur")})
            if m.group("risk"):
                conds.append({"field": "risk", "op": "==", "value": m.group("risk")})
            if m.group("doctype"):
                conds.append({"field": "doc_type", "op": "==", "value": singular_role(m.group("doctype") + "s")})
            if m.group("change"):
                conds.append({"field": "change_type", "op": "==", "value": m.group("change")})
            if m.group("overdue"):
                conds.append({"field": "overdue", "op": "==", "value": True})
            cond = None if not conds else (conds[0] if len(conds) == 1 else {"all": conds})
            step = "*" if m.group("overdue") else ("approve" if m.group("approvals") else slug(m.group("title")))
            add("routing", n, line, process=m.group("process"), step=step, condition=cond,
                assignee=_assignee(m.group("assignee")), mode="notify" if m.group("verb") == "are reported to" else "assign")
            return True
        m = _SLA.match(line)
        if m:
            add("sla", n, line, process=m.group("process"),
                step="approve" if m.group("approvals") else slug(m.group("title")), days=int(m.group("days")))
            return True
        m = _CRITERIA.match(line)
        if m:
            conds, lenses = [], {}
            if m.group("conds"):
                for phrase in m.group("conds").split(" and "):
                    adv = re.match(r"^(\d+|two|three|four|five|six) adversarial reviews pass$", phrase.strip())
                    if adv:
                        conds.append("adversarial_reviews_pass")
                        lenses["adversarial_reviews"] = int(adv.group(1)) if adv.group(1).isdigit() else NUMBER_WORDS[adv.group(1)]
                        continue
                    conds.append(CONDITION_VOCABULARY.get(phrase.strip(), "unknown:" + phrase.strip()))
            add("approval_criteria", n, line, approver="cog:" + m.group("cog"), process=m.group("process"),
                max_amount=number(m.group("amount")) if m.group("amount") else None,
                currency=m.group("cur"), conditions=conds,
                countersign=singular_role(m.group("counter")) if m.group("counter") else None,
                **lenses,
                **({"max_per_day": int(m.group("perday"))} if m.group("perday") else {}),
                **({"max_amount_per_month": number(m.group("permonth")), "currency": m.group("cur") or m.group("mcur")} if m.group("permonth") else {}))
            return True
        m = _SOD.match(line)
        if m:
            add("sod", n, line, object=slug(m.group("object")), roles=[slug(m.group("a")), slug(m.group("b"))])
            return True
        m = _SOD_COG.match(line)
        if m and section == "segregation of duties":
            add("sod", n, line, object=singular_role(m.group("object")) if m.group("object") else "*", roles=[],
                rule="cog-work-signed-by-person")
            return True
        m = _SOD_ONLY.match(line)
        if m:
            add("sod", n, line, object=slug(m.group("object")), roles=[slug(m.group("did")), "approver"], only=True)
            return True
        return False


def get_extractor(cache_dir=None, context: dict | None = None, gateway=None, calls: bool = False):
    """How SOPs are read: "convention" (the sentence shapes only: tests and offline use) or "hybrid" (those, then the
    compile-frame committee's cogs read the prose, each with its own bundled model, behind the guards and approvals;
    hubproto.llm_extract). Hybrid by default when the committee's cogs can have a model here: an Ollama, or hosted
    models switched on with a key. There is no global model override: compilation is done by the committee's cogs."""
    from .llm_extract import GrammarReader, ModelExtractor
    from .model import hosted_allowed, ollama_url

    mode = (os.environ.get("HUBPROTO_EXTRACTOR") or "").strip().lower()
    if mode not in ("convention", "hybrid"):  # unset, or a value no longer offered (``claude``): the default
        mode = "hybrid" if ollama_url() or (hosted_allowed() and os.environ.get("ANTHROPIC_API_KEY")) else "convention"
    if mode == "hybrid":
        return ModelExtractor(cache_dir=cache_dir, context=context, gateway=gateway, calls=calls)
    return GrammarReader()
