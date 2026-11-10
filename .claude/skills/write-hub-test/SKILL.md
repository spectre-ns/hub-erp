---
name: write-hub-test
description: "Write a test the suite's way: the conftest fixtures and helpers, page tests over HTTP, stand-in models and call= fakes, parallel safety, and a test that can fail. Use whenever you add or change a test under tests/."
---

# Write a hub test

A test here drives the real hub (`hubproto.app.Hub`) on a copy of the repository Frames under `tmp_path`; no mocks
of the kernel, no model. Read DEV-NOTES.md, "Tests in parallel", once.

## Fixtures and helpers (`tests/conftest.py`)

| Name | Kind | What it gives |
|---|---|---|
| `hub` | fixture | a fresh `Hub` on a copy of `frames/`, kernel seeded, **no release** |
| `released` | fixture | `hub` after `release_all(hub)`: the first release, Frame files kept as the source |
| `today` | fixture | the runner's date frozen for the test (due dates); use it instead of `date.today()` around SLAs |
| `release_all(hub, reviewers=("bob", "carol"), keep_frame_files=True)` | helper | compile, approve every change with two policy owners, release |
| `edit_frame(hub, name, old, new, bump=None)` | helper | replace text in a Frame file (asserts `old` is there) and reload the Frames |
| `make_part(k, mpn, ...)` | helper | an approved part with a supplier part and, with `stock=`, a lot in Stores |
| `seen(runner, run_id)` | helper | the digest a person quotes when deciding (`payload_digest`) |
| `dated(bill)`, `SMALL_BILL`, `MID_BILL`, `BIG_BILL` | helpers | bills for purchase-to-pay |

Import helpers with `from conftest import release_all, edit_frame` (tests import from each other the same way).

- `release_all` keeps the Frame files as the source (`keep_frame_files=True`) so a test can edit a Frame, compile,
  review and release. For the converted default (SOPs as documents, everything through change orders) call
  `sopdocs.convert(hub, by="carol")` after it, as the `hub` fixture of `tests/test_change_control.py` does, or pass
  `keep_frame_files=False` (`tests/test_config_closure.py`).
- A suite may define its own `hub` fixture that overrides conftest's (the change-control suites do). Check the top
  of the file you add to.

## Page tests

Two shapes, both serving the hub on port 0 with `serve_in_thread(hub, "127.0.0.1", 0)`:

- `tests/test_shell_views.py`: a **module-scoped** `shell` fixture (released hub, Frame files kept) and the HTTP
  helpers `get(shell, path, **params)` -> `(status, text)`, `post(shell, path, fields)` -> `(status, url, body)`,
  `main_of(html)` (the `<main>` element), `plain(html)` (text only), `start(shell, process, who, **fields)`.
- `tests/test_change_order_pages.py`: a **function-scoped** `shell` fixture (converted hub, alice owning three
  SOPs) and `ok(resp)` (asserts 200, returns `(url, body)`), `refused(resp, status=400)` (asserts the refusal,
  returns the main element), `co_page(shell, co, who)`, plus `new_co`, `carry_revision`, `decide`, `sign`.

Pass the person as a parameter: `get(shell, "/inbox", **{"as": "carol"})`; `post(shell, "/co-new", {"as": "alice",
...})`. A refusal is a page with a sentence (`role="alert"`), not a traceback: assert on that sentence.

## Models: stand-in and fakes

- conftest sets `HUBPROTO_ALLOW_STAND_IN=1`: every cog answers with the deterministic stand-in. Never make a test
  call Ollama or Anthropic.
- Model readings: pass `call=` to `llm_extract.ModelExtractor` or `CommitteeExtractor`, and to
  `prose_drafting.draft_direct_changes`. The fake is `call(model, system, user) -> str` and returns the JSON the
  model would (see `fake()` in `tests/test_llm_extract.py`); give the extractor `cache_dir=tmp_path / "cache"`.
- Change-order seams: `monkeypatch.setattr(changecontrol, "REVIEWER", fn)` (a cog's review text),
  `"MEANING"` (related by meaning), `"PANEL"` (the panel suggestion); signatures are in the comments beside them in
  `hubproto/changecontrol.py`.
- Hosted models are off unless a test turns them on on purpose: `monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")`.

## Parallel safety

The suite runs under pytest-xdist; any test may run on any worker, in any order:

- never depend on another test having run first; a module-scoped hub is set up with what its tests read, and each
  test makes its own records (the view tests tag titles like `"[views] ..."`) and never counts all records;
- bind servers to port 0 and read `server.server_address[1]`, never pick a free port first;
- files under `tmp_path`, environment through `monkeypatch`, the date through `today`; a test that edits
  `config/*.json` points `hubproto.kernel.CONFIG_DIR` at a copy (`tests/test_config_closure.py`); dev-hub tests set
  `HUBPROTO_DEV_HOME` to a temporary directory.

## A test must be able to fail

1. Write the test and run it alone: `python -m pytest -q tests/test_x.py::test_name`.
2. Make it fail on purpose: undo the fix (`git stash` the source change, keep the test) or break the behaviour, and
   run it again. It must fail, with a message that says what is wrong. If it passes, it tests nothing.
3. Restore the fix; it passes. Assert on the outcome a person sees (the record's state, the sentence on the page,
   the release's rule), with a message string where the reason is not obvious.
4. Run the suite with the exit code: `python -m pytest -q -n 4; echo $?` (or `-n auto` on a machine of your own).

## Pitfalls

- `pytest.raises(ValueError, match=...)` on a refusal: match the words of the sentence, so a different refusal
  fails the test.
- After writing a Frame file directly, reload the store (`hub.frames = type(hub.frames)(hub.frames.root)`), or use
  `edit_frame`, which does.
- On a converted hub a compile outside a change order is refused (`closure.ChangeOrderRequired`); drive changes
  through `changecontrol` (or the pages) there.
- The cog queue is `inline` in tests (worked when the call returns), so a test does not sleep for a cog. A test about
  the queue itself sets `hub.queue.mode = "service"` and works it with `hub.queue.drain()` or `work_one()`, or serves
  with `serve_in_thread(..., cog_worker=True)`; such a test waits on a condition with a deadline, never a fixed sleep.
