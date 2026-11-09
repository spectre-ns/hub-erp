"""Rule extraction from Frame prose.

Two extractors share one output shape (see ``rules.py``):

* ``ConventionExtractor`` is deterministic. It reads the sentence patterns
  described in the ``writing-policy-frames`` Frame. It needs no model and is
  the default.
* ``ClaudeExtractor`` asks Claude to produce the same statements from
  free-form prose. It is optional (``HUBPROTO_EXTRACTOR=claude``) and its
  output passes through the same Guards, so a mis-extracted rule is caught by
  the Source-Grounding Guard before any person reviews it.
"""

from __future__ import annotations

import json
import os
import difflib
import re
from dataclasses import dataclass, field

from .frames import Frame
from .rules import CONDITION_VOCABULARY, NUMBER_WORDS

RULE_SECTIONS = (
    "roles", "members", "folders", "permissions", "thresholds", "routing",
    "approval criteria", "segregation of duties", "duties",
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
_PERMISSION = re.compile(
    r"^(?P<subject>[A-Z][\w -]+?) may (?P<neg>not )?(?P<action>[a-z]+)(?: on)? (?P<object>[a-z][a-z -]*?) in (?P<folder>[A-Z][\w /-]+?)"
    r"(?: when (?:an? |the )?(?P<confirmer>[a-z][a-z -]*?) confirms)?\.$"
)
_COG_SUBJECT = re.compile(r"^The (?P<cog>[a-z][\w-]*) cog$")
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
_SECTION_KEY = re.compile(r"^(?P<key>Level|Layers on|Compiles|Members|Agree): ?(?P<rest>.*)$")
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
_DUTY = re.compile(r"^Every (?P<every>day|working day|week|month) the (?P<cog>[a-z][\w-]*) cog (?P<task>[a-z].+\.)$")
_FOR = re.compile(r"^For: (?P<who>.+)$")
_DOC_REF = re.compile(r"\b[A-Z]{2,}-\d[\w-]*\b")
_OPS = {"over": ">", "above": ">", "under": "<", "below": "<", "at or under": "<=", "at or over": ">="}


@dataclass
class Extraction:
    rules: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


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
    """'Finance clerks' -> 'role:finance-clerk'; 'The stores cog' -> 'cog:stores' (a permission that names one cog)."""
    m = _COG_SUBJECT.match(text.strip())
    return "cog:" + m.group("cog") if m else "role:" + singular_role(text)


def audience_rule(line: str, frame, n: int) -> dict | None:
    """A work instruction's "For: ..." line: the roles it is written for, which makes it their required reading. Role
    names in backticks are taken as written; otherwise each listed name ("internal auditors and the quality manager")
    is read as a role. Names that are not roles (the chair) are dropped when the release is rendered."""
    m = _FOR.match(line)
    if not m:
        return None
    who = m.group("who").strip()
    named = re.findall(r"`([a-z][a-z0-9-]*)`", who)
    if not named:
        for part in re.split(r",| and ", re.sub(r"\([^)]*\)", "", who).rstrip(".")):
            words = [w for w in part.strip().lower().split() if w not in ("the", "a", "an", "every", "all", "any")]
            if words and len(words) <= 4:
                named.append(singular_role(" ".join(words)))
    if not named:
        return None
    return {"kind": "audience", "roles": sorted(set(named)),
            "source": {"frame": frame.name, "version": frame.version, "line": n, "text": line, "path": frame.relpath}}


def _assignee(text: str) -> str:
    text = text.strip()
    if text in _FIELD_ASSIGNEES:
        return "field:" + _FIELD_ASSIGNEES[text]
    if text.startswith("the ") and text.endswith(" cog"):
        return "cog:" + slug(text[4:-4])
    if text.startswith("the "):
        return "role:" + slug(text[4:])
    return "person:" + slug(text)


class ConventionExtractor:
    name = "convention"

    def extract(self, frame: Frame) -> Extraction:
        out = Extraction()
        mode = str(frame.frontmatter.get("compile", "convention"))
        if mode == "none":
            # Not compiled; only who it is for (a work instruction's "For:" line) is read, for required reading.
            for n, raw in enumerate(frame.lines, start=frame.body_start):
                aud = audience_rule(raw.strip(), frame, n)
                if aud:
                    out.rules.append(aud)
                    break
            return out
        if mode == "guided":
            out.notes.append({"frame": frame.name, "line": 0, "text": "",
                              "note": "guided mode requested; the convention extractor read this Frame (set HUBPROTO_EXTRACTOR=claude for model extraction)"})
        section = ""
        process: dict | None = None

        def src(n: int, text: str) -> dict:
            return {"frame": frame.name, "version": frame.version, "line": n, "text": text, "path": frame.relpath}

        def add(kind: str, n: int, text: str, **fields):
            out.rules.append({"kind": kind, **fields, "source": src(n, text)})

        def close_process():
            nonlocal process
            if process is not None:
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
            if section == "" and not line.startswith("#"):
                aud = audience_rule(line, frame, n)
                if aud:
                    out.rules.append(aud)
                    continue
            m = _SCOPE_HEAD.match(line) or _PERSONA_HEAD.match(line) or _COMMITTEE_HEAD.match(line)
            if m:
                close_process()
                close_cog()
                close_layered()
                section = "layered"
                skipping = False
                if m.re is _COMMITTEE_HEAD:
                    layered = {"kind": "committee", "name": m.group("name"), "members": [], "quorum": 0, "source": src(n, line)}
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
                    elif key == "agree":
                        agree = re.match(r"^(\d+)(?: of \d+)?", rest.strip())
                        layered["quorum"] = int(agree.group(1)) if agree else 0
                else:
                    out.notes.append({"frame": frame.name, "line": n, "text": line,
                                      "note": "unparsed line in a scope or committee section (Level:, Layers on:, Compiles:, Members:, Agree:)"})
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
                if section not in RULE_SECTIONS and not section.startswith("process:"):
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
                m = _PROCESS_STEP.match(line)
                if m:
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

            if section not in RULE_SECTIONS:
                continue
            matched = self._match_sentence(line, n, section, add)
            if not matched:
                note = {"frame": frame.name, "line": n, "text": line, "note": f"unparsed sentence in the {section!r} section"}
                if section == "duties":
                    note["severity"] = "error"
                    note["note"] = "a duty reads 'Every day|week|month the <cog> cog <does the task>.'; this one would be silently dropped"
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
        m = _SOD_ONLY.match(line)
        if m:
            add("sod", n, line, object=slug(m.group("object")), roles=[slug(m.group("did")), "approver"], only=True)
            return True
        return False


_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "fields_json": {"type": "string"},
                    "line": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["kind", "fields_json", "line", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rules"],
    "additionalProperties": False,
}

_PROMPT = """You extract governance rules from a policy document written in Markdown.
Return every rule the document states, as JSON matching the schema. For each rule:
- kind: one of role, member, folder, permission, threshold, routing, approval_criteria, sod, process
- fields_json: a JSON object string with the rule's fields, using exactly these shapes:
  role {{"name"}}; member {{"principal","principal_type","roles"}}; folder {{"path"}};
  permission {{"subject":"role:<slug>","action","object","resource":"folder:<Path>","effect":"allow"|"deny"}};
  threshold {{"name","value","unit"}}; routing {{"process","step":"approve","condition":null or
  {{"field":"amount","op","value","currency"}} or {{"field":"risk","op":"==","value"}},"assignee":"role:x"|"cog:x"|"person:x"}};
  approval_criteria {{"approver":"cog:<slug>","process","max_amount","currency","conditions":[ids]}} where ids are from {vocab};
  sod {{"object","roles":[a,b]}}; process {{"name","folder","object","steps":[{{"n","id","title","role","cog","kind":"cog"|"checkpoint"}}]}}
- line: the 1-based line number of the sentence the rule comes from (lines are numbered below)
- text: that line, copied verbatim and stripped of surrounding whitespace
Only state rules the document states. Do not invent roles, amounts or conditions.

Document "{name}" (version {version}):
{numbered}
"""


class ClaudeExtractor:
    """Model-backed extraction through the Anthropic SDK (optional)."""

    name = "claude"

    def __init__(self, model: str | None = None):
        import anthropic  # imported lazily so the prototype runs without the SDK

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("HUBPROTO_MODEL", "claude-opus-5-5")

    def extract(self, frame: Frame) -> Extraction:
        numbered = "\n".join(f"{i:4d}: {line}" for i, line in enumerate(frame.lines, start=frame.body_start))
        prompt = _PROMPT.format(name=frame.name, version=frame.version, numbered=numbered,
                                vocab=sorted(CONDITION_VOCABULARY.values()))
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            raise RuntimeError("extraction refused by the model")
        text = next(b.text for b in message.content if b.type == "text")
        data = json.loads(text)
        out = Extraction()
        for item in data["rules"]:
            try:
                fields = json.loads(item["fields_json"])
            except json.JSONDecodeError:
                out.notes.append({"frame": frame.name, "line": item["line"], "text": item["text"],
                                  "note": "model returned unparsable fields"})
                continue
            out.rules.append({"kind": item["kind"], **fields,
                              "source": {"frame": frame.name, "version": frame.version,
                                         "line": item["line"], "text": item["text"].strip()}})
        return out


def get_extractor(cache_dir=None, context: dict | None = None, gateway=None, calls: bool = False):
    """How SOPs are read: "convention" (the sentence shapes only) or "hybrid" (those, then a local model for the prose,
    behind the guards and approvals; hubproto.llm_extract). Hybrid by default when the hub has an Ollama. ("claude",
    a hosted model for everything, only when hosted models are switched on.)"""
    from .model import hosted_allowed, ollama_url

    mode = os.environ.get("HUBPROTO_EXTRACTOR") or ("hybrid" if ollama_url() else "convention")
    if mode == "claude" and hosted_allowed():
        return ClaudeExtractor()
    if mode in ("hybrid", "claude"):
        from .llm_extract import ModelExtractor

        return ModelExtractor(cache_dir=cache_dir, context=context, gateway=gateway, calls=calls)
    return ConventionExtractor()
