"""Command-line suite: the verbs run in-process and the panel's cli-admin findings are the acceptance criteria.

Exit codes: ``cli.main`` returns 0 or 1 (a failed outcome: error findings, DENIED, a failed run); a refusal or a
mistake raises ``CliExit`` (a ``SystemExit`` with ``code`` 1 or 2 and the sentence as its text).
"""

import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

import pytest

from conftest import ROOT, SMALL_BILL, dated, release_all
from hubproto import cli
from hubproto.app import Hub


def run_cli(root, *argv, err: io.StringIO | None = None) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err if err is not None else io.StringIO()):
        rc = cli.main(["--root", str(root), *argv])
    return rc, out.getvalue()


def compile_id(root) -> tuple[str, str]:
    """``(#n, id)`` of the proposal a compile makes, from its first line ``proposal #n <id> ...``."""
    rc, out = run_cli(root, "compile")
    assert rc == 0, out
    first = out.splitlines()[0].split()
    return first[1], first[2]


def refused(root, *argv) -> tuple[int, str]:
    """The exit code and sentence of a refusal or a mistake."""
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(SystemExit) as e, redirect_stdout(out), redirect_stderr(err):
        cli.main(["--root", str(root), *argv])
    code = e.value.code if isinstance(e.value.code, int) else 1
    text = getattr(e.value, "message", None) or err.getvalue() or str(e.value)
    assert "Traceback" not in err.getvalue()
    return code, text


def new_root(tmp_path: Path, name: str = "hub") -> Path:
    root = tmp_path / name
    root.mkdir()
    shutil.copytree(ROOT / "frames", root / "frames")
    return root


def edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / "frames" / rel
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


FINANCE = "finance/finance-policy.frame.md"
BILL = {**SMALL_BILL, "po_ref": "PO-1003", "amount": 4000}


@pytest.fixture(scope="module")
def released(tmp_path_factory) -> Path:
    """A hub with the repository Frames released by bob and carol; tests here do not edit its Frames."""
    root = new_root(tmp_path_factory.mktemp("cli"))
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    return root


def ensure_bills(root: Path, n: int = 2) -> None:
    """At least ``n`` bills on the module's hub: the tests that file bills may have run before (serially) or not at all
    in this process (the test run alone, or another pytest-xdist worker took them); bills already there are used."""
    hub = Hub(root)
    for i in range(len(hub.kernel.query(type_="bill")), n):  # under the ap-reviewer limit: posted, in nobody's queue
        po, amount = f"PO-{3100 + i}", 1500 + 10 * i
        hub.kernel.append("po.issued", f"po:{po}", {"supplier": "acme-metals", "amount": amount, "currency": "USD",
                                                    "description": "ensure_bills", "received": True}, by="seed")
        hub.kernel._cache = None
        hub.runner.submit("purchase-to-pay", {"record": dated({**SMALL_BILL, "po_ref": po, "amount": amount})}, owner="alice")


@pytest.fixture
def fresh(tmp_path) -> Path:
    """A hub with Frames and a seeded kernel but no release."""
    root = new_root(tmp_path)
    rc, out = run_cli(root, "init")
    assert rc == 0 and "kernel seeded" in out
    return root


# ------------------------------------------------------------------ roots, help, ids (cli-admin-10, -11, -12, -19)
def test_wrong_root_is_refused_and_creates_nothing(tmp_path):
    empty = tmp_path / "emptydir"
    empty.mkdir()
    code, text = refused(empty, "records")
    assert code == 2 and "no hub at" in text and "state/kernel.db" in text and "init" in text
    assert not (empty / "state").exists()
    code, text = refused(tmp_path / "typo", "why", "--as", "alice", "--action", "read", "--object", "bill", "--folder", "Finance")
    assert code == 2 and not (tmp_path / "typo").exists()


def test_init_creates_the_hub_once(tmp_path):
    root = new_root(tmp_path)
    rc, out = run_cli(root, "init")
    assert rc == 0 and "kernel seeded" in out and "next:" in out
    rc, out = run_cli(root, "init")
    assert rc == 0 and "already seeded" in out


def test_help_lists_verbs_in_order_of_use_with_a_description_for_every_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    order = [text.index(f"\n    {verb} ") for verb in ("init", "seed", "compile", "proposals", "review", "release", "releases", "shell")]
    assert order == sorted(order), "verbs are listed in the order an operator uses them"
    assert "exit codes" in text and "two owners" in text
    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, type(parser._subparsers._group_actions[0])))
    missing = []
    for name, sub in subparsers.choices.items():
        for action in sub._actions:
            if action.dest == "help":
                continue
            if not action.help:
                missing.append(f"{name} {action.dest}")
    assert not missing, missing
    assert all(a.help for a in subparsers._choices_actions), "every verb says what it does"


def test_unknown_ids_and_bad_input_are_one_sentence_exit_2(released, tmp_path):
    for argv, needle in [
        (("run-show", "nope"), "no run starts with 'nope'"),
        (("track", "nope"), "no run starts with"),
        (("proposal", "nope"), "no proposal starts with 'nope'"),
        (("record", "NCR-2026-9999"), "no record 'NCR-2026-9999'"),
        (("upload", "--folder", "Finance/Payables", "--as", "alice", "--file", str(tmp_path / "nope.txt")), "cannot read"),
        (("mail-in", str(tmp_path / "nope.eml")), "cannot read"),
        (("compile", "--scope", "bogus"), "no Frame is named 'bogus'"),
        (("seed", "nope"), "no profile 'nope'"),
        (("records", "--state", "nonsense"), "states are:"),
        (("records", "--type", "nonsense"), "types are:"),
        (("release-diff", "v9"), "no release v9"),
        (("why", "--as", "alice", "--action", "create", "--object", "widgets", "--folder", "Finance"), "objects are:"),
        (("why", "--as", "alice", "--action", "create", "--object", "bill", "--folder", "Nowhere"), "folders are:"),
    ]:
        code, text = refused(released, *argv)
        assert code == 2, (argv, text)
        assert needle in text, (argv, text)


def test_record_numbers_are_case_insensitive_and_runs_resolve_by_prefix(released):
    run_cli(released, "run", "task", "--as", "alice", "--record-json", json.dumps({"title": "[cli] other", "assignee": "bob"}))
    rc, out = run_cli(released, "run", "task", "--as", "alice", "--record-json", json.dumps({"title": "[cli] prefix", "assignee": "bob"}))
    assert rc == 0
    run_id = out.split()[1]
    rc, out = run_cli(released, "runs")
    number = next(line.split()[5] for line in out.splitlines() if line.startswith(run_id))
    rc, out = run_cli(released, "record", number.lower())
    assert rc == 0 and out.startswith(number)
    rc, out = run_cli(released, "run-show", run_id[:4])
    assert rc == 0 and f"run {run_id}" in out
    code, text = refused(released, "run-show", "")
    assert code == 2 and "matches" in text and "give more characters" in text


# ------------------------------------------------------------------ compile, proposals, review, release (cli-admin-1, -2, -3, -8, -17, -18)
def test_compile_exits_1_on_a_sentence_no_reader_read_cited_by_file_and_line(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "## Permissions", "## Permisions")
    rc, out = run_cli(fresh, "compile")
    assert rc == 1 and "[error]" not in out, "the grammar not knowing a line is not an error"
    assert "[question] frames/finance/finance-policy.frame.md:18: not read: ## Permisions" in out
    assert "not releasable yet" in out and "a person answers each question" in out


def test_compile_collapses_rerendered_processes_and_says_what_to_run_next(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "Payables reviewers may read Finance.", "Payables reviewers may read Finance.\nPayables reviewers may create bills in Finance/Payables.")
    rc, out = run_cli(fresh, "compile")
    assert rc == 0
    num = out.split()[1]
    assert "re-rendered, no semantic change" in out and "add process purchase-to-pay" not in out
    assert 'WIDENING add allow role:payables-reviewer create bill in folder:Finance/Payables  — "Payables reviewers may create bills in Finance/Payables." (frames/finance/finance-policy.frame.md:25)' in out
    assert "up to None" not in out
    assert f"next: hubproto review {num} --as bob --approve-all" in out and "then another owner for the widenings (carol)" in out


def test_first_compile_never_says_up_to_none(fresh):
    rc, out = run_cli(fresh, "compile")
    assert rc == 0 and "up to None" not in out and "with no amount limit" in out


def test_proposals_list_and_proposal_show_status_base_changes_and_decisions(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "Payables reviewers may read Finance.", "Payables reviewers may read Finance.\nPayables reviewers may create bills in Finance/Payables.")
    num, pid = compile_id(fresh)
    rc, out = run_cli(fresh, "proposals")
    assert rc == 0
    rows = {l.split()[1]: l for l in out.splitlines()[1:]}
    assert len(rows) == 2
    released_row = next(l for i, l in rows.items() if i != pid)
    assert "released as v1 (" in released_row and "decisions by bob, carol" in released_row
    assert rows[pid].split()[0] == num.lstrip("#") and " open" in rows[pid] and "v1 (" in rows[pid] and rows[pid].split()[5] == "2"  # the rule change and the wording change; the processes only moved
    rc, out = run_cli(fresh, "review", num, "--as", "bob", "--approve-all", "--meaning", "reviewers may file too")
    assert rc == 0 and "needs:" in out
    rc, out = run_cli(fresh, "proposal", pid[:6])
    assert rc == 0 and "bob approve" in out and "reviewers may file too" in out and "pending" in out
    assert "re-rendered, no semantic change" in out
    rc, out = run_cli(fresh, "proposals", "--open")
    assert "decisions by bob" in out and "released" not in out
    code, text = refused(fresh, "review", num, "--as", "bob")
    assert code == 2 and "--approve-all" in text
    code, text = refused(fresh, "review", num, "--as", "bob", "--decide", "zzz=approve")
    assert code == 2 and "no change" in text


def test_review_and_release_refuse_non_owners_and_non_members(released):
    rc, out = run_cli(released, "compile")
    assert rc == 0 and "no changes against the release in force" in out
    num = out.split()[1]
    code, text = refused(released, "review", num, "--as", "dave", "--approve-all")
    assert code == 1 and "not a policy owner" in text
    code, text = refused(released, "review", num, "--as", "nobody", "--approve-all")
    assert code == 2 and "not a member" in text and "alice, bob, carol, dave" in text
    code, text = refused(released, "review", num, "--as", "bob", "--approve-all")
    assert code == 1 and "no changes to review" in text
    code, text = refused(released, "release", num, "--as", "dave")
    assert code == 1 and "not a policy owner" in text


def test_releasing_a_stale_or_released_proposal_is_one_sentence(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "Payables reviewers may read Finance.", "Payables reviewers may read Finance.\nPayables reviewers may create bills in Finance/Payables.")
    first, _ = compile_id(fresh)
    edit(fresh, FINANCE, "Controllers may read Finance.\n", "")
    second, _ = compile_id(fresh)
    for who in ("bob", "carol"):
        run_cli(fresh, "review", second, "--as", who, "--approve-all")
    code, text = refused(fresh, "review", first, "--as", "bob", "--approve-all")
    assert code in (1, 2) and "superseded" in text, "the second compile on the same base superseded the first"
    rc, out = run_cli(fresh, "release", second, "--as", "bob")
    assert rc == 0 and "release v2 (" in out and "in effect since" in out
    code, text = refused(fresh, "release", first, "--as", "bob")
    assert code == 1 and "superseded" in text and "\n" not in text.strip()
    code, text = refused(fresh, "release", second, "--as", "bob")
    assert code == 1 and "is released" in text
    rc, out = run_cli(fresh, "proposals")
    assert "superseded" in out


def test_releases_show_versions_and_release_diff_shows_sentences_by_file_line(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "Controllers may read Finance.", "Payables reviewers may create bills in Finance/Payables.")
    num, pid = compile_id(fresh)
    for who in ("bob", "carol"):
        run_cli(fresh, "review", num, "--as", who, "--approve-all", "--meaning", "agreed")
    rc, out = run_cli(fresh, "release", pid, "--as", "carol")
    assert rc == 0
    rc, out = run_cli(fresh, "releases")
    assert rc == 0
    rows = [l for l in out.splitlines() if l[2:4] in ("v1", "v2")]
    assert rows[0][2:4] == "v1" and rows[1].startswith("* v2") and len(rows[1].split()[2]) == 12
    rc, out = run_cli(fresh, "release-diff", "v2")
    assert rc == 0
    assert "0 sentence(s) added, 0 removed, 1 changed" in out, "a sentence replaced on its line is one change"
    assert "frames/finance/finance-policy.frame.md" in out
    assert "~ L26 -> L26: Controllers may read Finance." in out
    assert "now: Payables reviewers may create bills in Finance/Payables." in out
    assert "carol approve" in out and "agreed" in out
    digest = rows[1].split()[2]
    rc, out2 = run_cli(fresh, "release-diff", digest)
    assert out2 == out


def test_effective_from_and_today_accept_only_iso_dates(released):
    code, text = refused(released, "release", "#1", "--as", "bob", "--effective-from", "next monday")
    assert code == 2 and "YYYY-MM-DD" in text
    code, text = refused(released, "tick", "--today", "tomorrow")
    assert code == 2 and "YYYY-MM-DD" in text
    rc, out = run_cli(released, "tick", "--today", "2000-01-01")
    assert rc == 0 and "tick done" in out


def test_scheduled_release_is_marked(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "Controllers may read Finance.\n", "")
    num, _ = compile_id(fresh)
    for who in ("bob", "carol"):
        run_cli(fresh, "review", num, "--as", who, "--approve-all")
    rc, out = run_cli(fresh, "release", num, "--as", "bob", "--effective-from", "2099-01-01")
    assert rc == 0 and "scheduled; takes effect 2099-01-01T00:00:00+00:00" in out
    rc, out = run_cli(fresh, "releases")
    assert "(scheduled)" in out and out.count("* v1") == 1
    rc, out = run_cli(fresh, "status")
    assert "scheduled: v2" in out


# ------------------------------------------------------------------ why (cli-admin-9, -17)
def test_why_exit_codes_and_plural_objects(released):
    rc, out = run_cli(released, "why", "--as", "alice", "--action", "create", "--object", "bills", "--folder", "Finance/Payables")
    assert rc == 0 and out.startswith("ALLOWED") and "finance-policy line 21" in out
    rc, out = run_cli(released, "why", "--as", "carol", "--action", "create", "--object", "bill", "--folder", "Finance/Payables")
    assert rc == 1 and out.startswith("DENIED") and "Quality managers may not create bills" in out
    code, text = refused(released, "why", "--as", "nobody", "--action", "read", "--object", "bill", "--folder", "Finance")
    assert code == 2 and "not a member" in text


def test_why_without_a_release_is_denied_with_exit_1(fresh):
    rc, out = run_cli(fresh, "why", "--as", "alice", "--action", "read", "--object", "bill", "--folder", "Finance")
    assert rc == 1 and "no policy release is in effect" in out


# ------------------------------------------------------------------ runs (cli-admin-7, first-time-user-1, hostile-14, approver-18)
def test_run_prints_the_failure_reason_and_retry_takes_fields(released):
    rc, out = run_cli(released, "run", "purchase-to-pay", "--as", "alice")
    assert rc == 1
    run_id = out.split()[1]
    assert f"run {run_id} failed at extract-the-bill" in out and "invoice has no supplier" in out
    assert "[error] invoice has no amount" in out and f"next: hubproto retry {run_id} --as alice --field" in out
    rc, out = run_cli(released, "inbox-pending", "--as", "alice")
    assert run_id in out and "retry with:" in out
    rc, out = run_cli(released, "retry", run_id, "--as", "alice", "--field", "supplier=acme-metals", "--field", "amount=1500",
                      "--field", "currency=USD", "--field", f"date={date.today().isoformat()}", "--field", "po_ref=PO-1001")
    assert rc == 0 and "retried status=completed" in out and "state=posted" in out
    code, text = refused(released, "retry", run_id, "--as", "alice")
    assert code == 1 and "is not failed" in text


def test_retry_that_fails_again_exits_1_with_the_reason(released):
    rc, out = run_cli(released, "run", "purchase-to-pay", "--as", "alice",
                      "--record-json", json.dumps({**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1001"}))
    assert rc == 1 and "is not open for posting" in out
    run_id = out.split()[1]
    rc, out = run_cli(released, "retry", run_id, "--as", "alice", "--note", "again")
    assert rc == 1 and "retried status=failed" in out and "is not open for posting" in out
    rc, out = run_cli(released, "cancel", run_id, "--as", "alice", "--reason", "wrong period")
    assert rc == 0 and "state=cancelled" in out


def test_bad_record_json_is_a_sentence(released):
    code, text = refused(released, "run", "task", "--as", "alice", "--record-json", "{bad json")
    assert code == 2 and "does not parse at line 1 column 2" in text
    code, text = refused(released, "run", "task", "--as", "alice", "--record-json", "[1,2]")
    assert code == 2 and "must be an object" in text
    code, text = refused(released, "run", "task", "--as", "alice", "--record-json", '{"title": "x", "due": NaN}')
    assert code == 2 and "NaN" in text


def test_decide_needs_a_meaning_and_quotes_the_seen_digest(released):
    rc, out = run_cli(released, "run", "purchase-to-pay", "--as", "alice", "--record-json", json.dumps(dated(BILL)))
    assert rc == 0 and "waiting for: dave" in out
    run_id = out.split()[1]
    rc, out = run_cli(released, "inbox-pending", "--as", "dave")
    assert run_id in out and "seen digest=" in out and "fields=" in out and "what this Gate accepts" in out
    digest = out.split("seen digest=")[1].split()[0]
    code, text = refused(released, "decide", run_id, "--as", "dave", "--verdict", "approve")
    assert code == 1 and "a signature needs its meaning" in text
    code, text = refused(released, "decide", run_id, "--as", "dave", "--verdict", "approve", "--meaning", "ok", "--seen", "0000000000000000")
    assert code == 1 and "changed since you opened it" in text
    code, text = refused(released, "decide", run_id, "--as", "ap-reviewer", "--verdict", "approve", "--meaning", "ok")
    assert code == 2 and "is a cog" in text
    rc, out = run_cli(released, "decide", run_id, "--as", "dave", "--verdict", "approve", "--meaning", "goods received", "--seen", digest)
    assert rc == 0 and "status=completed" in out and "state=posted" in out


def test_runs_listing_has_a_header_and_12_char_digests(released):
    ensure_bills(released, 1)
    rc, out = run_cli(released, "runs")
    header, *rows = out.splitlines()
    assert header.startswith("run") and "created" in header and "record" in header
    assert rows and all(len(r.split()[6]) == 12 for r in rows if r.split()[3] != "failed")


# ------------------------------------------------------------------ identity and messages (cli-admin-16, hostile-14)
def test_messages_need_a_person_sender_and_a_member_recipient(released):
    code, text = refused(released, "msg", "--as", "alice", "--to", "nobody", "--subject", "x")
    assert code == 2 and "nobody is not a member" in text and "cogs: ap-reviewer" in text
    code, text = refused(released, "msg", "--from", "ap-reviewer", "--to", "bob", "--subject", "x")
    assert code == 2 and "is a cog" in text
    rc, out = run_cli(released, "msg", "--as", "alice", "--to", "bob", "--subject", "hi", "--body", "there")
    assert rc == 0 and "delivered to bob" in out
    rc, out = run_cli(released, "inbox", "--as", "bob")
    assert "from alice: hi" in out
    code, text = refused(released, "doc-new", "--title", "n", "--class", "controlled", "--folder", "Finance", "--as", "nobody", "--file", str(ROOT / "README.md"))
    assert code == 2 and "not a member" in text
    code, text = refused(released, "upload", "--folder", "Nowhere", "--as", "alice", "--file", str(ROOT / "README.md"))
    assert code == 2 and "folders are:" in text


def test_mail_in_to_a_non_member_is_refused(released, tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_text("From: alice@hub.local\nTo: zed@hub.local\nSubject: hi\n\nbody\n")
    code, text = refused(released, "mail-in", str(eml))
    assert code == 2 and "zed is not a member" in text
    eml.write_text("From: supplier@acme.example\nTo: bob@hub.local\nSubject: hi\n\nbody\n")
    rc, out = run_cli(released, "mail-in", str(eml))
    assert rc == 0 and "delivered to bob" in out


def test_link_lists_the_vocabulary_on_a_bad_relation(released):
    ensure_bills(released, 2)
    rc, out = run_cli(released, "records", "--type", "bill")
    numbers = [l.split()[0] for l in out.splitlines()[1:]]
    assert len(numbers) >= 2
    code, text = refused(released, "link", numbers[0], "relates-to", numbers[1].lower(), "--as", "alice")
    assert code == 2 and "link-vocabulary" in text and "concerns" in text and "supersedes" in text
    rc, out = run_cli(released, "link", numbers[0], "references", numbers[1].lower(), "--as", "alice")
    assert rc == 0 and f"{numbers[0]} references {numbers[1]}" in out


# ------------------------------------------------------------------ output hygiene (hostile-15)
def test_control_characters_and_escapes_never_reach_the_terminal(released):
    title = "<b>x</b>\u001b[31mRED\u001b[0m\r\nnewline\ttab" + "y" * 400
    rc, out = run_cli(released, "run", "task", "--as", "alice", "--record-json", json.dumps({"title": title, "assignee": "carol"}))
    assert rc == 0
    run_id = out.split()[1]
    for argv in (("records", "--type", "task"), ("inbox-pending", "--as", "carol"), ("runs",), ("run-show", run_id), ("inbox", "--as", "carol")):
        rc, out = run_cli(released, *argv)
        assert "\x1b" not in out and "\r" not in out and "\t" not in out, argv
        assert "[31m" not in out, argv
    rc, out = run_cli(released, "records", "--type", "task")
    row = next(l for l in out.splitlines() if "RED newline tab" in l)
    assert "…" in row and len(row) < 220, "long values are truncated with an ellipsis"
    number = row.split()[0]
    rc, out = run_cli(released, "record", number)
    assert "\x1b" not in out and "RED newline tab" in out


# ------------------------------------------------------------------ Frames on disk (cli-admin-4, -5)
def test_a_broken_frame_stops_only_the_verbs_that_read_frames(fresh):
    release_all(Hub(fresh))
    path = fresh / "frames" / FINANCE
    text = path.read_text()
    path.write_text("—-" + text[3:])
    rc, out = run_cli(fresh, "runs")
    assert rc == 0 and "run " in out, "a verb that does not read Frames still works"
    rc, out = run_cli(fresh, "records")
    assert rc == 0
    code, text_ = refused(fresh, "compile")
    assert code == 2 and "finance-policy.frame.md: missing frontmatter block" in text_
    path.write_text(text)
    rc, out = run_cli(fresh, "compile")
    assert rc == 0


def test_status_reports_frames_that_differ_from_the_release(fresh):
    release_all(Hub(fresh))
    rc, out = run_cli(fresh, "status", "--check")
    assert rc == 0 and "Frames on disk match" in out and "release in force: v1 (" in out
    edit(fresh, FINANCE, "Controllers may read Finance.\n", "")
    rc, out = run_cli(fresh, "status", "--check")
    assert rc == 1
    assert "changed: frames/finance/finance-policy.frame.md" in out
    assert "hubproto compile && hubproto review <proposal> --as bob --approve-all && hubproto review <proposal> --as carol --approve-all && hubproto release <proposal> --as bob" in out
    assert "until then the shell and every run apply v1" in out
    rc, out = run_cli(fresh, "status")
    assert rc == 0, "without --check the drift is reported, not an error"


def test_status_without_a_release(fresh):
    rc, out = run_cli(fresh, "status", "--check")
    assert rc == 1 and "release in force: none" in out


# ------------------------------------------------------------------ bootstrap and the entrypoint (cli-admin-1, -20)
def test_bootstrap_release_checks_owners_then_releases_once(fresh):
    code, text = refused(fresh, "bootstrap-release", "--as", "dave", "--second", "carol")
    assert code == 2 and "dave is not a policy owner" in text and "bob, carol" in text
    code, text = refused(fresh, "bootstrap-release", "--as", "bob", "--second", "nobody")
    assert code == 2 and "--second nobody" in text and "not a member" in text
    assert not list((fresh / "state" / "proposals").glob("*.json")), "a refused bootstrap compiles nothing"
    rc, out = run_cli(fresh, "bootstrap-release", "--as", "bob", "--second", "carol")
    assert rc == 0 and "release v1 (" in out and "in effect" in out
    rc, out = run_cli(fresh, "bootstrap-release", "--as", "bob", "--second", "carol")
    assert rc == 1 and "already in effect" in out
    rc, out = run_cli(fresh, "bootstrap-release", "--as", "bob", "--second", "carol", "--if-needed")
    assert rc == 0 and "nothing to do" in out


def test_bootstrap_release_stops_on_errors_naming_each_file_once(fresh):
    edit(fresh, "company/org-model.frame.md", "- bob (person): controller, reviewer, policy-owner, management-representative, internal-auditor, staff",
         "- bob (person): reviewer, policy-owner, management-representative, internal-auditor, staff")
    rc, out = run_cli(fresh, "bootstrap-release", "--as", "bob", "--second", "carol")
    assert rc == 1
    assert "bootstrap stopped" in out and "frames/finance/finance-policy.frame.md" in out
    assert out.count("role:controller is routed to but no member holds that role") == 1, "one line per distinct error, with every place"
    line = next(l for l in out.splitlines() if "role:controller is routed to" in l)
    assert line.startswith("  [error] frames/finance/finance-policy.frame.md:") and "(also frames/finance/finance-policy.frame.md:" in line
    assert "there is no release" not in out


# ------------------------------------------------------------------ backup and restore (cli-admin-14)
def test_backup_and_restore_refuse_a_partial_set(released, tmp_path):
    rc, out = run_cli(released, "backup", str(tmp_path / "bk"))
    assert rc == 0 and "kernel.db" in out and "runs (" in out
    assert (tmp_path / "bk" / "backup.json").exists() and (tmp_path / "bk" / "releases" / "index.json").exists()
    code, text = refused(released, "backup", str(tmp_path / "bk"))
    assert code == 2 and "not empty" in text
    shutil.rmtree(tmp_path / "bk" / "runs")
    target = new_root(tmp_path, "restored")
    code, text = refused(target, "restore", str(tmp_path / "bk"))
    assert code == 2 and "missing runs" in text and not (target / "state").exists()
    run_cli(released, "backup", str(tmp_path / "bk2"))
    rc, out = run_cli(target, "restore", str(tmp_path / "bk2"))
    assert rc == 0 and "release in force: v1 (" in out
    rc, a = run_cli(released, "runs")
    rc, b = run_cli(target, "runs")
    assert a == b
    rc, a = run_cli(released, "records")
    rc, b = run_cli(target, "records")
    assert a == b
    code, text = refused(target, "restore", str(tmp_path / "bk2"))
    assert code == 2 and "--force" in text
    rc, out = run_cli(target, "restore", str(tmp_path / "bk2"), "--force")
    assert rc == 0 and "previous state moved to" in out


def _tree(root: Path, skip_aside: bool = True) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
            and not (skip_aside and p.relative_to(root).parts[0].startswith(".before-restore-"))}


def test_the_backup_holds_the_frames_and_a_restore_puts_them_back(fresh, tmp_path):
    """In Docker the Frames are the hub's own volume (issue #5), so the backup carries them with the state."""
    release_all(Hub(fresh))
    frames = fresh / "frames"
    rc, out = run_cli(fresh, "backup", str(tmp_path / "bk"))
    assert rc == 0 and "frames (" in out
    assert json.loads((tmp_path / "bk" / "backup.json").read_text())["parts"]["frames"] == len(_tree(frames))
    assert _tree(tmp_path / "bk" / "frames") == _tree(frames), "every file, the dot-directories (.replaced) too"
    before = _tree(frames)
    (frames / "finance" / "finance-policy.frame.md").unlink()
    (frames / "stray.frame.md").write_text("not this hub's\n")
    rc, out = run_cli(fresh, "restore", str(tmp_path / "bk"), "--force")
    assert rc == 0 and "previous Frames moved to" in out, out
    assert _tree(frames) == before
    aside = [p for p in frames.iterdir() if p.name.startswith(".before-restore-")]
    assert len(aside) == 1 and (aside[0] / "stray.frame.md").exists(), "the Frames replaced are kept, not lost"
    rc, out = run_cli(fresh, "status", "--check")
    assert rc == 0 and "Frames on disk match" in out, out
    rc, out = run_cli(fresh, "backup", str(tmp_path / "bk2"))
    assert rc == 0 and _tree(tmp_path / "bk2" / "frames", skip_aside=False) == before, "what a restore set aside is not backed up"


def test_a_backup_without_frames_restores_and_leaves_the_frames_alone(released, tmp_path):
    rc, _ = run_cli(released, "backup", str(tmp_path / "bk"))
    assert rc == 0
    shutil.rmtree(tmp_path / "bk" / "frames")
    target = new_root(tmp_path, "restored")
    code, text = refused(target, "restore", str(tmp_path / "bk"))
    assert code == 2 and "missing frames" in text, "a backup that lists its Frames must have them"
    manifest = json.loads((tmp_path / "bk" / "backup.json").read_text())
    del manifest["parts"]["frames"]  # a backup made before backups copied the Frames
    (tmp_path / "bk" / "backup.json").write_text(json.dumps(manifest))
    before = _tree(target / "frames")
    rc, out = run_cli(target, "restore", str(tmp_path / "bk"))
    assert rc == 0 and "holds no Frames" in out and "release in force: v1 (" in out
    assert _tree(target / "frames", skip_aside=False) == before


def test_a_backup_with_an_empty_frames_folder_leaves_the_hubs_frames_in_place(released, tmp_path):
    rc, _ = run_cli(released, "backup", str(tmp_path / "bk"))
    assert rc == 0
    shutil.rmtree(tmp_path / "bk" / "frames")
    (tmp_path / "bk" / "frames").mkdir()  # a hub whose Frames were missing when it was backed up
    before = _tree(released / "frames", skip_aside=False)
    rc, out = run_cli(released, "restore", str(tmp_path / "bk"), "--force")
    assert rc == 0 and "WARNING" in out and "holds no Frame files" in out, out
    assert _tree(released / "frames", skip_aside=False) == before, "not set aside, not emptied"
    assert "Frames on disk match the release in force" in out


def test_a_restore_says_when_the_frames_do_not_match_the_restored_release(released, tmp_path):
    rc, _ = run_cli(released, "backup", str(tmp_path / "bk"))
    assert rc == 0
    shutil.rmtree(tmp_path / "bk" / "frames")
    manifest = json.loads((tmp_path / "bk" / "backup.json").read_text())
    del manifest["parts"]["frames"]  # an older backup: state only
    (tmp_path / "bk" / "backup.json").write_text(json.dumps(manifest))
    policy = released / "frames" / "finance" / "finance-policy.frame.md"
    policy.write_text(policy.read_text() + "\nA sentence no release holds.\n")
    rc, out = run_cli(released, "restore", str(tmp_path / "bk"), "--force")
    assert rc == 0 and "holds no Frames" in out, out
    assert "Frames on disk differ from the release in force" in out and "changed: frames/finance/finance-policy.frame.md" in out
    assert "may not be the ones the restored release was made from" in out
    rc, status = run_cli(released, "status", "--check")
    assert rc == 1 and "changed: frames/finance/finance-policy.frame.md" in status, "the same drift status reports"


# ------------------------------------------------------------------ the entrypoint's Frames (issue #5)
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
STUB = """#!/bin/sh
echo "$@" >> "$HUBPROTO_ROOT/calls.log"
case "$1" in
  init) mkdir -p "$HUBPROTO_ROOT/state" && touch "$HUBPROTO_ROOT/state/kernel.db" ;;
esac
exit 0
"""


def _entrypoint(root: Path, seed: Path, tmp_path: Path, check: bool = True) -> tuple[str, list[str]]:
    """Run docker/entrypoint.sh on ``root`` with a stand-in ``hubproto`` that logs its arguments."""
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "hubproto").write_text(STUB)
    (bin_dir / "hubproto").chmod(0o755)
    env = dict(os.environ, HUBPROTO_ROOT=str(root), HUBPROTO_SEED_FRAMES=str(seed), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    done = subprocess.run(["sh", str(ENTRYPOINT), "version"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 or not check, done.stdout + done.stderr
    log = root / "calls.log"
    return done.stdout, log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def seed_frames(tmp_path) -> Path:
    seed = tmp_path / "image-frames"
    shutil.copytree(ROOT / "frames", seed)
    return seed


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_the_first_start_fills_an_empty_frames_folder_from_the_image(tmp_path, seed_frames):
    root = tmp_path / "hub"
    (root / "frames").mkdir(parents=True)  # the new, empty volume
    out, calls = _entrypoint(root, seed_frames, tmp_path)
    assert "first start: copying the Frames the image ships" in out
    assert _tree(root / "frames", skip_aside=False) == _tree(seed_frames, skip_aside=False)
    assert calls[:3] == ["init", "seed iso9001", "bootstrap-release --as bob --second carol --if-needed"] and calls[-1] == "version"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_a_later_start_never_writes_the_images_frames_over_the_hubs(tmp_path, seed_frames):
    root = tmp_path / "hub"
    root.mkdir()  # no frames folder at all (a run without the volume): created and filled
    _entrypoint(root, seed_frames, tmp_path)
    released = root / "frames" / "finance" / "finance-policy.frame.md"
    released.write_text(released.read_text() + "\nA sentence this hub released.\n")
    (seed_frames / "company" / "new-in-the-image.frame.md").write_text("---\nname: x\n---\n")
    hub_frames = _tree(root / "frames", skip_aside=False)
    out, calls = _entrypoint(root, seed_frames, tmp_path)
    assert "copying the Frames" not in out and "WARNING" not in out
    assert _tree(root / "frames", skip_aside=False) == hub_frames
    assert "status --check" in calls


@pytest.mark.skipif(shutil.which("sh") is None or os.geteuid() == 0, reason="needs a POSIX shell and a user who cannot read a mode-000 file")
def test_a_first_start_copy_that_stopped_partway_is_finished_on_the_next_start(tmp_path, seed_frames):
    root = tmp_path / "hub"
    (root / "frames").mkdir(parents=True)
    blocked = seed_frames / "finance" / "finance-policy.frame.md"
    blocked.chmod(0o000)  # the copy fails on it, partway, and the container exits
    try:
        out, calls = _entrypoint(root, seed_frames, tmp_path, check=False)
    finally:
        blocked.chmod(0o644)
    marker = root / "frames" / ".first-start-copy-in-progress"
    assert marker.exists() and calls == [], "stopped before init, the copy marked unfinished"
    assert any((root / "frames").iterdir()), "a partial copy: the folder is not empty"
    out, calls = _entrypoint(root, seed_frames, tmp_path)
    assert "did not finish last time" in out and "WARNING" not in out
    assert not marker.exists()
    assert _tree(root / "frames", skip_aside=False) == _tree(seed_frames, skip_aside=False)
    assert calls[0] == "init"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_a_hub_with_state_and_no_frames_is_warned_not_filled(tmp_path, seed_frames):
    """An existing hub moved to the frames volume without its Frames copied in (docs/rollout/frames-volume.md)."""
    root = tmp_path / "hub"
    (root / "frames").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "state" / "kernel.db").write_bytes(b"")
    (root / "state" / ".bootstrapped").write_text("")
    out, calls = _entrypoint(root, seed_frames, tmp_path)
    assert "WARNING" in out and "frames is empty but this hub has state" in out and "frames-volume.md" in out
    assert not any((root / "frames").iterdir()), "the image's Frames are never copied over a hub that has state"
    assert "status --check" in calls and "seed iso9001" not in calls


# ------------------------------------------------------------------ documents (cli-admin-16)
def test_doc_checkin_needs_the_checkout_by_the_same_person(released, tmp_path):
    src = tmp_path / "sop.md"
    src.write_text("# SOP\n")
    rc, out = run_cli(released, "doc-new", "--title", "[cli] SOP", "--class", "controlled", "--folder", "Quality/Controlled Documents",
                      "--as", "alice", "--file", str(src))
    assert rc == 0
    number = out.split()[0]
    code, text = refused(released, "doc-checkin", number, "--as", "alice", "--file", str(src))
    assert code == 1 and "is not checked out" in text and "doc-checkout" in text
    rc, out = run_cli(released, "doc-checkout", number.lower(), "--as", "alice")
    assert rc == 0 and "checked out by alice" in out
    code, text = refused(released, "doc-checkin", number, "--as", "carol", "--file", str(src))
    assert code == 1 and "checked out by alice, not carol" in text
    src.write_text("# SOP v2\n")
    rc, out = run_cli(released, "doc-checkin", number, "--as", "alice", "--file", str(src), "--note", "revised")
    assert rc == 0 and "version 2" in out


def test_cog_users_verb_takes_the_items_waiting_for_cogs(fresh, monkeypatch):
    from hubproto.app import Hub
    from conftest import dated, SMALL_BILL

    hub = Hub(fresh)
    release_all(hub)
    monkeypatch.setenv("HUBPROTO_COG_USERS", "service")
    hub = Hub(fresh)
    run_id = hub.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    assert hub.runner.load(run_id)["pending"]["assignees"] == ["ap-reviewer"]
    rc, out = run_cli(fresh, "cog-users")
    assert rc == 0 and "ap-reviewer decided run" in out and "1 item(s) taken" in out
    assert Hub(fresh).runner.load(run_id)["status"] == "completed"


def test_models_lists_what_each_cog_is_bundled_with(fresh):
    release_all(Hub(fresh))
    rc, out = run_cli(fresh, "models")
    assert rc == 0 and "cogs:" in out and "quality-assistant" in out and "ollama/gemma4:12b-it-qat" in out
