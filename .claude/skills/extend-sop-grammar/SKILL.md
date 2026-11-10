---
name: extend-sop-grammar
description: "Add a new sentence shape (and, when needed, a new rule kind) to the SOP grammar end to end: extract, rules, guards and widenings, release, the Settings sentence and its round trip, the model reading, change control, tests and docs. Use when the hub should read and enforce a kind of SOP sentence it does not know yet."
---

# Add a sentence shape to the SOP grammar

The grammar is the fast path from SOP prose to rules (DEV-NOTES.md, "Sentence grammar"); it is never a gate. A
sentence in a rule section that it cannot read goes to the compile-frame committee, and one nobody read becomes a
blocking `unread-rule` question. So a new shape must read **exactly** what it says, and everything downstream must
know the rule kind it produces. A worked example of the whole path is commit `130bf84` (validation classes:
`git show 130bf84`).

Decide first: does the sentence fit an existing kind (`rules.KINDS`) with new wording, or is it a new kind? A new
wording of an existing kind touches only steps 1, 5 and 8 to 9.

## Steps

1. **Grammar** (`hubproto/extract.py`).
   - Add a compiled pattern beside the others (`_PERMISSION`, `_THRESHOLD`, `_ROUTING`, `_SLA`, `_CRITERIA`, `_SOD`,
     `_DUTY`, `_VCLASS`, ...). Anchor it (`^...\.$`) and keep names in the organization's words (roles plural,
     `the <name> cog`, folders as paths).
   - Match it in `ConventionExtractor._match_sentence(line, n, section, add)` and emit the rule with
     `add("<kind>", n, line, **fields)`; `add` sets the citation (`source`: frame, version, line, text).
   - A new `## Section` must be added to `RULE_SECTIONS`, and, in `ConventionExtractor.extract`, an unparsed line
     there should get an `unread` note saying what the shape is (as `validation` and `duties` do), so it becomes a
     question, never a silent drop.
2. **Rule kind** (`hubproto/rules.py`). Add `KINDS["<kind>"] = {required fields}` with a comment saying what it is,
   and value checks in `validate_rule` (enumerations, ranges). `rule_key` hashes every field but `source`, so keep
   fields canonical (slugs, numbers as numbers, sorted lists).
3. **Guards** (`hubproto/guards.py`).
   - `consistency_guard`: the names the rule uses exist (roles, cogs, processes, folders, groups), and conflicting
     rules are a finding with a sentence.
   - Widenings: `_widens(rule, old_rules)` for an added rule, `_widens_over(old, new)` for a changed one, and the
     removal case in `drift_guard` (removing a restriction widens). Anything that gives a person or cog more
     authority, or loosens a control, is a widening (two policy owners). Unknown kinds default to not widening:
     decide explicitly.
   - `rule_identity`: the fields that say what the rule is about, so an edit pairs into one "change" instead of an
     add and a remove. `_reason(rule, op)`: the words a reviewer sees (the fallback is a dict dump).
4. **Release** (`hubproto/release.py`). `render(rules, record_types)` puts the kind into the bundle (a key of its
   own, or into an existing one); whatever enforces it reads that key. If the rule names members or roles, add it
   to `rule_names` so the Roles and responsibilities checks and `follows_for` see it.
5. **Settings and the round trip** (`hubproto/settings.py`, `hubproto/sop_settings.py`).
   - `settings.sentence(rule, processes)` must write the very sentence the grammar reads back into the same rule:
     this is how Settings edits and direct changes become prose (`prose_drafting`'s deterministic writer, the
     change order's round-trip check).
   - `sop_settings.SECTION["<kind>"]` names the section a new sentence of this kind goes into (`SECTIONED` for a kind
     written as a whole section).
6. **Model reading** (`hubproto/llm_extract.py`). If the committee may read this kind from free prose, add it to
   `MODEL_KINDS` and describe its fields in the `_KINDS` prompt text; `_build_rule` fills defaults. Changing a prompt
   means bumping `PROMPT_VERSION` and `DIFF_PROMPT_VERSION` (they are in the reading cache keys). If not, leave it
   out: `_build_rule` refuses it with a note.
7. **Change control and validation.** `changecontrol.TERM_KINDS` (words the change-control SOP's "Review of
   changes" section may use for it), `changecontrol.UNIQUE_KINDS` if only one rule may hold a slot, and
   `validation.DEFAULT_CLASSES` / `validation.WORDS` for its risk class (unknown kinds count as medium).
8. **Tests** (skill `write-hub-test`), in the suite of the area (`tests/test_compile.py` for the grammar):
   - the sentence compiles to the rule, cited at its line; a near miss in the section gives an `unread` note;
   - the rule reaches the bundle after `release_all`, and the behaviour it configures happens;
   - adding, loosening and removing it are widenings where they should be (`p = hub.compile()`, then
     `c["widening"]`);
   - the round trip: `settings.sentence(rule, procs) == rule["source"]["text"]`, and that sentence extracts back to
     the same `rule_key`;
   - check each test fails without the change.
9. **Docs**: the DEV-NOTES.md "Sentence grammar" block; `frames/reference/writing-policy-frames.frame.md` (the shapes
   for authors, a Frame in no release); the table in `docs/manual/07-policy-and-configuration.md`, 7.1. If the example
   company should state it, add the sentence to its Frame (that changes the first release, so look at tests that
   count rules).

## Pitfalls

- `_match_sentence` tries the shapes in order and most are not tied to a section (a permission sentence is read in
  any rule section). Put a narrower shape before a broader one, gate it on `section ==` when it could collide, and
  run the whole suite: a new pattern can steal sentences from an old one.
- Never make the grammar guess. A sentence that reads two ways is the committee's (and then a person's) to decide.
- `settings.sentence` and the pattern drift apart silently unless the round-trip test exists; `tests/test_settings.py`
  only samples some kinds.
- A kind that changes who may do what but is not marked a widening lets one policy owner widen authority: check
  `_widens` before anything else.
- Model output is data: a model-read rule goes through the same `validate_rule` and guards (`_malformed`), and must
  never carry anything executable.
