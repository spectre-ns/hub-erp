"""Command-line suite: the verbs run in-process and the panel's cli-admin findings are the acceptance criteria.

Exit codes: ``cli.main`` returns 0 or 1 (a failed outcome: error findings, DENIED, a failed run); a refusal or a
mistake raises ``CliExit`` (a ``SystemExit`` with ``code`` 1 or 2 and the sentence as its text).
"""

import io
import json
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
def test_compile_exits_1_on_a_grammar_error_cited_by_file_and_line(fresh):
    release_all(Hub(fresh))
    edit(fresh, FINANCE, "## Permissions", "## Permisions")
    rc, out = run_cli(fresh, "compile")
    assert rc == 1
    assert "[error] frames/finance/finance-policy.frame.md:18: grammar" in out
    assert out.index("[error]") < out.index("\nchanges:\n"), "errors are printed first"
    assert "not releasable" in out


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
    assert rows[pid].split()[0] == num.lstrip("#") and " open" in rows[pid] and "v1 (" in rows[pid] and rows[pid].split()[5] == "5"
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
        run_cli(fresh, "review", first, "--as", who, "--approve-all")
    rc, out = run_cli(fresh, "release", second, "--as", "bob")
    assert rc == 0 and "release v2 (" in out and "in effect since" in out
    code, text = refused(fresh, "release", first, "--as", "bob")
    assert code == 1 and "is in force; compile again" in text and "\n" not in text.strip()
    code, text = refused(fresh, "release", second, "--as", "bob")
    assert code == 1 and "is released" in text
    rc, out = run_cli(fresh, "proposals")
    assert "stale (based on v1" in out


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
    err = io.StringIO()
    rc, out = run_cli(fresh, "runs", err=err)
    assert rc == 0 and "run " in out and "could not be read" in err.getvalue() and "finance-policy.frame.md" in err.getvalue()
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
