"""Reading SOP pictures into text (issue #31, parts 1 and 2).

Part 1: the model gateway takes pictures (bytes and a media type) for a model that reads them, as the Anthropic API's
image blocks and Ollama's ``images`` field; a text-only model is refused with a sentence; what is recorded of a call
names each picture by its sha256, never its bytes. Part 2: at compile, the compile-frame committee's senior cogs read
each picture put in or replaced; their reading is parsed, guarded and compared with the prose and the Process: section;
a person confirms it, corrects it or says the picture states nothing; the confirmed reading is part of the Frame text in
force, after the picture's line, and an unchanged picture keeps it and asks nothing. No test calls a model: the stand-in
and ``call=`` fakes answer."""

import base64
import hashlib
import json
import shutil

import pytest

from hubproto import changecontrol as cc
from hubproto import eco, model, office, picture_reading, sopdocs
from hubproto.llm_extract import CommitteeExtractor, baseline_for, context_for, with_unread_rules
from test_change_control import accept_co_warnings, decide_all, gate, hub, new_co  # noqa: F401
from test_change_order_pages import shell  # noqa: F401
from test_shell_views import plain, post
from test_sop_pictures import ANCHOR, DOCX, PNG, PNG2, _frame, package, parts, picture_questions, picture_revision, ref, review, word_picture

SYSTEM, USER = "You read pictures.", "What does it state?"
SENIORS = (("senior-a", "claude-opus-5-5"), ("senior-b", "claude-opus-5-5"))
SAID = "Approvals in journal-reversal go to the controller."
READING = {"states_nothing": False, "sentences": [SAID], "process": "journal-reversal",
           "steps": [{"title": "Request the journal reversal", "role": "finance-clerk"}, {"title": "Approve the reversal", "role": "controller"},
                     {"title": "Post the reversal", "role": "system"}],
           "rules": [{"kind": "routing", "fields": {"process": "journal-reversal", "step": "approve", "condition": None, "assignee": "role:controller",
                                                     "mode": "assign"}, "quote": SAID}]}


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    """No test here reaches a real Ollama, whatever the shell says; Ollama's answers about models are forgotten."""
    monkeypatch.delenv("HUBPROTO_OLLAMA_URL", raising=False)
    monkeypatch.setattr(model, "_CAPS", {})


def png(n: int = 1) -> dict:
    return {"data": PNG if n == 1 else PNG2, "media_type": "image/png"}


class Seniors:
    """A ``call=`` fake for the compile-frame committee: a picture call (``images``) answers ``reading``; a prose call
    answers that the diff changes no rule. ``seen`` holds the sha256 of every picture it was sent."""

    def __init__(self, reading: dict | None = None, per_member: dict | None = None):
        self.reading = reading if reading is not None else READING
        self.per_member = per_member or {}
        self.seen: list[list[str]] = []

    def __call__(self, model_name, system, user, images=None):
        if images:
            self.seen.append([hashlib.sha256(i["data"]).hexdigest() for i in images])
            member = next((m for m in self.per_member if f"You are the {m} cog" in system), None)
            return json.dumps(self.per_member.get(member, self.reading))
        return json.dumps({"changes": [], "questions": []})


def committee(hub, tmp_path, call, members=SENIORS, calls=True, gateway=None):
    return CommitteeExtractor(list(members), len(members), cache_dir=tmp_path / "cache", context=context_for(hub), call=call, gateway=gateway,
                              calls=calls, baseline=lambda: baseline_for(hub), chair="carol",
                              pictures=lambda name, r: sopdocs.picture_images(hub, name, r))


def seniors_read(hub, monkeypatch, tmp_path, fake) -> None:
    """The hub's compile-frame committee is two senior cogs reading with ``fake``."""
    monkeypatch.setattr(hub, "extractor", lambda calls=False: committee(hub, tmp_path, fake, calls=calls))


def compiled_text(hub, co, name="finance-policy") -> str:
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    return p["overrides"][name]["text"]


def release_it(hub, co):
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co)
    accept_co_warnings(hub, co)
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co)["status"] == "completed"
    return hub.releases.frame_texts(hub.releases.current()["digest"])["finance-policy"]


# ---------------------------------------------------------------- part 1: the gateway
def test_an_image_call_goes_through_the_stand_in_and_is_recorded_by_its_hash():
    gw = model.ModelGateway()
    out = gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[png()])
    sha = hashlib.sha256(PNG).hexdigest()
    assert out["stand_in"] and f"picture sha256:{sha}" in out["text"], "the stand-in names the picture it was given"
    assert out["images"] == [{"sha256": sha, "media_type": "image/png", "bytes": len(PNG)}]
    assert out["prompt_digest"] == model.prompt_digest(SYSTEM, USER, [{"sha256": sha}])
    assert out["prompt_digest"] != gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER)["prompt_digest"], "the picture is in the digest"
    assert out["prompt_digest"] != gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[png(2)])["prompt_digest"]
    assert gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER)["prompt_digest"] == \
        __import__("hubproto.rules", fromlist=["sha256_of"]).sha256_of({"system": SYSTEM, "user": USER})[:16], "a text call's digest is as before"
    recorded = json.dumps(list(gw.calls))
    assert base64.b64encode(PNG).decode() not in recorded and "a routing flowchart" not in recorded, "never the picture's bytes"


def test_a_text_only_model_is_refused_with_a_sentence_never_sent_without_the_picture():
    gw = model.ModelGateway()
    with pytest.raises(model.ImagesNotSupported, match="ollama/qwen3:14b reads text only, so it cannot be asked to read a picture"):
        gw.complete(model="ollama/qwen3:14b", system=SYSTEM, user=USER, images=[png()])
    assert not gw.calls, "nothing was sent or recorded"
    assert model.supports_images("claude-opus-5-5") and model.supports_images("ollama/qwen2.5vl:7b") and not model.supports_images("ollama/qwen3:14b")
    assert not model.supports_images("ollama/gemma3:1b"), "a family with text-only sizes is never guessed to read pictures"
    with pytest.raises(ValueError, match="a model is sent only image/png"):
        gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[{"data": PNG, "media_type": "image/tiff"}])
    with pytest.raises(ValueError, match="holds no bytes"):
        gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[{"data": b"", "media_type": "image/png"}])


def test_the_anthropic_api_gets_image_blocks_and_ollama_its_images_field(monkeypatch):
    sent = []

    def fetch(req, timeout):
        sent.append(json.loads(req.data))
        return {"content": [{"type": "text", "text": "read"}]} if "anthropic" in req.full_url else {"message": {"content": "read"}}

    monkeypatch.setattr(model, "_fetch_json", fetch)
    monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    b64 = base64.b64encode(PNG).decode()
    out = model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, images=[png()])
    assert out["backend"] == "anthropic" and out["text"] == "read"
    assert sent[-1]["messages"][-1]["content"] == [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                                                   {"type": "text", "text": USER}]

    monkeypatch.setenv("HUBPROTO_OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(model, "context_length", lambda m: 32768)
    monkeypatch.setattr(model, "_ollama_capabilities", lambda name: ["completion", "vision"] if name == "qwen2.5vl:7b" else ["completion"])
    out = model.ModelGateway().complete(model="ollama/qwen2.5vl:7b", system=SYSTEM, user=USER, images=[png()])
    assert out["backend"] == "ollama"
    assert sent[-1]["messages"][-1] == {"role": "user", "content": USER, "images": [b64]}
    n = len(sent)
    with pytest.raises(model.ImagesNotSupported, match="reads text only"):
        model.ModelGateway().complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[png()])  # Ollama says: no vision
    assert len(sent) == n, "the refused call was not sent"


# ---------------------------------------------------------------- the reading lines
def test_a_confirmed_reading_sits_after_its_picture_and_typed_words_never_read_as_one():
    pic = f"![flow]({ref(PNG)})"
    text = f"# X\nBefore.\n{pic}\nAfter.\n"
    merged = picture_reading.with_readings(text, {ref(PNG): ["The buyer raises the order.", "The controller approves it."]})
    line = office.reading_line(ref(PNG), "The buyer raises the order.")
    assert merged.splitlines()[2:5] == [pic, line, office.reading_line(ref(PNG), "The controller approves it.")]
    assert picture_reading.readings_in(merged) == {ref(PNG): ["The buyer raises the order.", "The controller approves it."]}
    assert picture_reading.strip_readings(merged) == text and picture_reading.with_readings(merged, {}) == text
    assert picture_reading.with_readings(text.replace(ref(PNG), ref(PNG2)), picture_reading.readings_in(merged)) == text.replace(ref(PNG), ref(PNG2)), \
        "a replaced picture does not keep the reading of the one it replaces"
    assert office.escape_text_line(line) == "\\" + line and not office.is_reading_line(office.escape_text_line(line))
    typed = sopdocs.to_docx(f"# X\n\\{line}\n")  # a paragraph whose words are the reading line
    assert sopdocs.docx_lines(typed)[1] == "\\" + line, "words typed like a reading line read escaped: a document never holds a confirmed reading"
    written = sopdocs.docx_lines(sopdocs.to_docx(f"# X\nBefore.\n{pic}\n{line}\n", {ref(PNG): sopdocs.pictures_of(
        word_picture(sopdocs.to_docx("# X\n\nBefore."), "Before.", PNG))[ref(PNG)]}))
    assert pic in written and not any("Picture sha256:" in x for x in written), "the hub never writes a reading into a document"


def test_the_grammar_reads_a_reading_as_prose_in_a_rule_section_only():
    from hubproto.extract import ConventionExtractor

    line = office.reading_line(ref(PNG), "Approvals in purchase-to-pay go to the controller.")
    out = ConventionExtractor().extract(_frame(f"# X\n## Routing\n![flow]({ref(PNG)})\n{line}\n## Process: p\n1. [controller] Approve it\n"
                                               f"![flow]({ref(PNG)})\n{office.reading_line(ref(PNG), '1. [clerk] Do more')}\n"))
    [routing] = [r for r in out.rules if r["kind"] == "routing"]
    assert routing["assignee"] == "role:controller" and routing["source"]["text"] == line and routing["source"]["line"] == 10
    [proc] = [r for r in out.rules if r["kind"] == "process"]
    assert [s["title"] for s in proc["steps"]] == ["Approve it"], "a reading is never part of a process's structure"


# ---------------------------------------------------------------- part 2: senior cogs read a picture at compile
def test_the_seniors_read_a_new_picture_and_a_difference_from_the_prose_is_a_divergence(hub, tmp_path):
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    differs = {**READING, "sentences": [SAID, "Journal reversals over 5,000 USD go to the policy owner."],
               "steps": [{"title": "Approve the reversal", "role": "policy-owner"}, {"title": "Request the journal reversal", "role": "finance-clerk"}],
               "rules": READING["rules"] + [{"kind": "threshold", "fields": {"name": "match-tolerance", "value": 5, "unit": "percent"},
                                             "quote": "Journal reversals over 5,000 USD go to the policy owner."}]}
    fake = Seniors(differs)
    ex = committee(hub, tmp_path, fake)
    frame = sopdocs.document_store(hub, eco.drafts_of(hub, co)).get("finance-policy")
    out = ex.extract(frame)
    [q] = [q for q in out.questions if q.get("picture")]
    assert fake.seen == [[hashlib.sha256(PNG).hexdigest()]] * 2, "each senior was sent the picture's bytes from the document"
    assert q["status"] == "open" and q["blocking"] and q["code"] == "unread-rule", "a person decides"
    r = q["reading"]
    assert r["by"] == ["senior-a", "senior-b"] and r["agreed"] and r["proposed"] == differs["sentences"]
    assert any("the picture states “The match tolerance is 5 percent”; the prose states “The match tolerance is 2 percent”" in d for d in r["divergences"]), r
    assert any("done by policy-owner" in d and "done by controller" in d for d in r["divergences"]), r
    assert any("in another order" in d for d in r["divergences"]) and any("does not show: Post the reversal" in d for d in r["divergences"])
    assert not any("journal-reversal" in d and "routing" in d for d in r["divergences"]), "what the prose states too is no divergence"
    assert "It differs from the prose" in q["text"] and "confirm the reading" in q["text"]
    assert [n for n in out.notes if n["severity"] == "warning" and n["note"].startswith("divergence: ")], "each divergence is a warning"


def test_members_who_read_a_picture_differently_are_shown_side_by_side(hub, tmp_path):
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    fake = Seniors(per_member={"senior-b": {"states_nothing": True, "sentences": [], "steps": [], "rules": []}})
    out = committee(hub, tmp_path, fake).extract(sopdocs.document_store(hub, eco.drafts_of(hub, co)).get("finance-policy"))
    [q] = [q for q in out.questions if q.get("picture")]
    assert not q["reading"]["agreed"] and "The members read it differently: senior-b: it states nothing" in q["text"]


def test_a_senior_on_a_text_only_model_is_refused_and_the_picture_stays_unread(hub, tmp_path):
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    ex = committee(hub, tmp_path, None, members=(("senior-a", "ollama/qwen3:14b"),), gateway=hub.model)
    [q] = [q for q in ex.extract(sopdocs.document_store(hub, eco.drafts_of(hub, co)).get("finance-policy")).questions if q.get("picture")]
    assert "reading" not in q and q["status"] == "open" and q["blocking"]
    assert "the senior-a cog could not read it: ollama/qwen3:14b reads text only, so it cannot be asked to read a picture" in q["text"]
    assert "Nobody has read what it states" in q["text"]


def test_a_picture_read_confirmed_and_released_is_part_of_the_text_in_force(hub, tmp_path, monkeypatch):
    fake = Seniors()
    seniors_read(hub, monkeypatch, tmp_path, fake)
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    pic = f"![Routing of a bill]({ref(PNG)})"
    [q] = picture_questions(review(hub, co))
    assert q["reading"]["proposed"] == [SAID] and q["reading"]["divergences"] == [] and q["status"] == "open"
    with pytest.raises(ValueError, match="only the committee's chair"):
        cc.answer_question(hub, co["number"], q["id"], "", "alice", picture="confirm")
    cc.answer_question(hub, co["number"], q["id"], "", "bob", picture="confirm")  # a system administrator, for the committee
    assert any("a picture's reading was confirmed after the last compile" in r for r in cc.send_reasons(hub, co["number"])), \
        "the confirmed reading is compiled into the text before the change order goes for approval"
    assert hub.kernel.get(co["locator"])["fields"]["picture_readings"]["finance-policy"][ref(PNG)]["lines"] == [SAID]

    shutil.rmtree(tmp_path / "cache")  # nothing read before is at hand: a confirmed picture is still not read again
    [q2] = picture_questions(review(hub, co))
    assert q2["status"] == "answered" and q2["by"] == "bob" and q2["confirmed_reading"] == [SAID] and SAID in q2["answer"]
    lines = compiled_text(hub, co).splitlines()
    at = lines.index(pic)
    assert lines[at + 1] == office.reading_line(ref(PNG), SAID), "the reading follows its picture in the SOP's text"
    assert len(fake.seen) == 2, "a confirmed picture is not read again"
    assert not any("reading was confirmed" in r for r in cc.send_reasons(hub, co["number"]))

    assert "bob" in cc.contributors(hub, co["number"]), "who confirmed the reading wrote SOP text"
    released = release_it(hub, co)
    assert office.reading_line(ref(PNG), SAID) in released.splitlines(), "the confirmed reading is part of the release"
    bom = {f["name"]: f for f in hub.releases.current()["frames"]}["finance-policy"]
    assert bom["sha256"] == hashlib.sha256(released.encode()).hexdigest(), "the release digest covers the reading"
    assert "a routing flowchart" not in json.dumps(hub.releases.current()), "no picture bytes in a release"
    in_force = sopdocs.in_force(hub.kernel, "finance-policy")
    assert sopdocs.document_store(hub).get("finance-policy").text == released, "the text in force keeps the reading"
    assert office.reading_line(ref(PNG), SAID) not in sopdocs.frame_text(hub, in_force), "the document itself holds only the picture"
    assert hub.compile(save=False)["changes"] == []

    # The next revision keeps the picture: its reading stays, and nothing is asked or read.
    co2 = new_co(hub, title="Lower the AP cog limit")
    d = eco.add_sop(hub, co2["number"], "finance-policy", by="alice")
    body = sopdocs.frame_text(hub, d).split("\n---\n", 1)[1]
    content, _ = hub.dms.content(d["locator"])
    old = "Approvals in purchase-to-pay at or under 2,000 USD go to the ap-reviewer cog."
    hub.dms.add_version(d["locator"], sopdocs.to_docx(body.replace(old, old.replace("2,000", "1,500")), sopdocs.pictures_of(content)),
                        by="alice", filename="finance-policy.docx", mime=DOCX)
    assert picture_questions(review(hub, co2)) == [] and len(fake.seen) == 2, "an unchanged picture asks nothing and is not read"
    assert office.reading_line(ref(PNG), SAID) in compiled_text(hub, co2).splitlines()

    # The revision then replaces the picture: the new one loses the reading of the one it replaces and is read again.
    content, _ = hub.dms.content(d["locator"])
    files = parts(content)
    files[next(n for n, b in files.items() if b == PNG)] = PNG2  # the same place, another drawing
    hub.dms.add_version(d["locator"], package(files), by="alice", filename="finance-policy.docx", mime=DOCX)
    [q3] = picture_questions(review(hub, co2))
    assert ref(PNG2) in q3["quote"] and q3["status"] == "open" and fake.seen[-1] == [hashlib.sha256(PNG2).hexdigest()]
    assert not any(office.is_reading_line(x) for x in compiled_text(hub, co2).splitlines()), "the old reading went with the old picture"


def test_a_corrected_reading_or_nothing_is_what_the_picture_states(hub, tmp_path, monkeypatch):
    seniors_read(hub, monkeypatch, tmp_path, Seniors())
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    with pytest.raises(ValueError, match="is not a sentence"):
        cc.answer_question(hub, co["number"], q["id"], "", "carol", picture="correct", reading_text="# a heading")
    cc.answer_question(hub, co["number"], q["id"], "the drawing shows the reversal only", "carol", picture="correct",
                       reading_text="Journal reversals are requested by a finance clerk.\nThe controller approves journal reversals.")
    review(hub, co)
    lines = compiled_text(hub, co).splitlines()
    assert [x for x in lines if office.is_reading_line(x)] == [office.reading_line(ref(PNG), "Journal reversals are requested by a finance clerk."),
                                                             office.reading_line(ref(PNG), "The controller approves journal reversals.")]

    co2 = new_co(hub, title="Add the logo")
    d = eco.add_sop(hub, co2["number"], "document-control-sop", by="alice")
    content, _ = hub.dms.content(d["locator"])
    first = next(x for x in sopdocs.frame_text(hub, d).split("\n---\n", 1)[1].splitlines() if x and not x.startswith("#"))
    hub.dms.add_version(d["locator"], word_picture(content, first, PNG2), by="alice", filename="document-control-sop.docx", mime=DOCX)
    [q] = picture_questions(review(hub, co2))
    cc.answer_question(hub, co2["number"], q["id"], "", "carol", picture="nothing")
    assert not any("reading was confirmed" in r for r in cc.send_reasons(hub, co2["number"])), "states nothing: the text does not change"


def test_no_picture_answer_is_given_on_a_change_order_awaiting_approval(hub, tmp_path, monkeypatch):
    seniors_read(hub, monkeypatch, tmp_path, Seniors())
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    cc.send_for_approval(hub, co["number"], "alice")
    for how in ("confirm", "correct", "nothing"):  # whatever the answer: the text signed is the text compiled
        with pytest.raises(ValueError, match="so answer it while the change order is being revised"):
            cc.answer_question(hub, co["number"], q["id"], "", "carol", picture=how, reading_text="The controller approves.")
    assert not hub.kernel.get(co["locator"])["fields"].get("picture_readings")


# ---------------------------------------------------------------- upgrading
def test_a_picture_in_force_before_readings_keeps_its_text_digest_and_asks_nothing(hub, tmp_path, monkeypatch):
    """A picture released the way #10 released it (a person answered "it states no rule"; no reading) stays as it is: the
    text and digest in force do not move, and a change order that carries its SOP asks nothing and reads nothing."""
    co = new_co(hub, title="Add the routing flowchart")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    cc.answer_question(hub, co["number"], q["id"], "a drawing of the routing sentences above; it states no rule", "carol")
    released = release_it(hub, co)
    meta = hub.releases.current()
    assert not any(office.is_reading_line(x) for x in released.splitlines())

    fake = Seniors()
    seniors_read(hub, monkeypatch, tmp_path, fake)
    assert sopdocs.document_store(hub).get("finance-policy").text == released
    p = hub.compile(save=False)
    assert p["changes"] == [] and {f["name"]: f["sha256"] for f in p["frames"]} == {f["name"]: f["sha256"] for f in meta["frames"]}, \
        "no digest moves"
    co2 = new_co(hub, title="Lower the AP cog limit")
    d = eco.add_sop(hub, co2["number"], "finance-policy", by="alice")
    body = sopdocs.frame_text(hub, d).split("\n---\n", 1)[1]
    content, _ = hub.dms.content(d["locator"])
    old = "Approvals in purchase-to-pay at or under 2,000 USD go to the ap-reviewer cog."
    hub.dms.add_version(d["locator"], sopdocs.to_docx(body.replace(old, old.replace("2,000", "1,500")), sopdocs.pictures_of(content)),
                        by="alice", filename="finance-policy.docx", mime=DOCX)
    assert picture_questions(review(hub, co2)) == [] and fake.seen == []
    assert not any(office.is_reading_line(x) for x in compiled_text(hub, co2).splitlines())


def test_a_picture_in_force_from_a_text_only_release_is_read_only_with_the_change_order_that_carries_it(hub, tmp_path, monkeypatch):
    rec = sopdocs.in_force(hub.kernel, "finance-policy")
    content, _ = hub.dms.content(rec["locator"])
    hub.dms.add_version(rec["locator"], word_picture(content, ANCHOR, PNG, inside=True), by="hub", system=True,
                        filename="finance-policy.docx", mime=DOCX)  # as it was in force before pictures had lines
    meta = hub.releases.current()
    fake = Seniors()
    seniors_read(hub, monkeypatch, tmp_path, fake)
    p = hub.compile(save=False)
    assert p["changes"] == [] and {f["name"]: f["sha256"] for f in p["frames"]} == {f["name"]: f["sha256"] for f in meta["frames"]}
    assert fake.seen == [], "nothing is read for an SOP nobody revised"
    co = new_co(hub, title="Revise the finance policy")
    eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    [q] = picture_questions(review(hub, co))
    assert q["reading"]["proposed"] == [SAID] and fake.seen, "the change order that carries it has it read"


def test_with_unread_rules_still_asks_about_a_picture_no_committee_reads():
    from hubproto.extract import ConventionExtractor

    old = _frame("# X\n## Routing\nApprovals in purchase-to-pay go to the controller.\n")
    new = _frame(f"# X\n## Routing\nApprovals in purchase-to-pay go to the controller.\n![flow]({ref(PNG)})\n")
    base = {"digest": "d", "texts": {"x": old.text}, "rules": [], "chair": "carol"}
    [q] = [q for q in with_unread_rules(new, ConventionExtractor().extract(new), base).questions if q.get("picture")]
    assert "Nobody has read what it states (no senior cog of the compile-frame committee reads pictures here)" in q["text"]
    assert "not in the SOP's text in force" in q["text"] and "read for the first time" in q["text"]


# ---------------------------------------------------------------- the change-order page
def test_the_chair_confirms_a_reading_on_the_change_order_page(shell, tmp_path, monkeypatch):
    from hubproto.shell.change_order_ui import anchor
    from test_change_order_pages import co_page, ok

    hub = shell["hub"]
    seniors_read(hub, monkeypatch, tmp_path, Seniors())
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    html = co_page(shell, co, "carol")
    assert "Confirm the committee's reading" in html and "The picture states nothing" in html and SAID in html
    fid = "answer-" + anchor(q["id"])
    ok(post(shell, "/co-answer", {"as": "carol", "co": co["locator"], "form": fid, "qid": q["id"], "picture": "confirm"}))
    q = next(x for x in cc.questions(hub, co["number"]) if x["id"] == q["id"])
    assert q["status"] == "answered" and q["confirmed_reading"] == [SAID] and q["picture_answer"] == "confirm"
    assert "reading was confirmed after the last compile: request review again" in plain(co_page(shell, co, "alice"))


def test_a_rule_read_from_a_reading_sentence_is_cited_at_its_whole_line():
    from hubproto.guards import grounding_guard
    from hubproto.llm_extract import _build_rule

    line = office.reading_line(ref(PNG), "Approvals in purchase-to-pay go to the controller.")
    f = _frame(f"# X\n## Purpose\n![flow]({ref(PNG)})\n{line}\n")
    rule, _ = _build_rule(f, {"kind": "routing", "line": 10, "quote": "Approvals in purchase-to-pay go to the controller.",
                              "fields": {"process": "purchase-to-pay", "step": "approve", "assignee": "role:controller"}}, "senior-a")
    assert rule["source"]["text"] == line and grounding_guard([rule], {"x": f}) == []


# ---------------------------------------------------------------- review of PR #41
def test_who_corrects_a_reading_made_what_is_read_from_it_and_is_never_its_only_approver(hub, tmp_path, monkeypatch):
    """carol, the committee's chair and a policy owner, corrects a picture's reading into a routing sentence: she wrote that
    SOP text, so she is a contributor and a maker of the item read from it, and cannot approve it alone."""
    seniors_read(hub, monkeypatch, tmp_path, Seniors())
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    wrote = "Approvals in journal-reversal go to the payables-reviewer."
    cc.answer_question(hub, co["number"], q["id"], "", "carol", picture="correct", reading_text=wrote)
    assert "carol" in cc.contributors(hub, co["number"])
    p = review(hub, co)
    [item] = [c for c in cc.delta(hub, co["number"]) if c["op"] == "add" and c["rule"].get("assignee") == "role:payables-reviewer"]
    assert "carol" in item["makers"] and item["origin"]["by"] == "carol", item
    try:
        cc.decide_item(hub, co["number"], item["id"], "approve", "carol", "fine", accept_warnings=("all",))
    except ValueError:
        pass  # refused outright is fine too
    item = next(c for c in cc.delta(hub, co["number"]) if c["id"] == item["id"])
    assert item["state"] != "approved", "whoever wrote a change is never its only approver"
    cc.send_for_approval(hub, co["number"], "alice")
    accept_co_warnings(hub, co, "bob")
    assert cc.approval_reasons(hub, co["number"]), "it cannot be signed with carol as its only approver"
    # The corrected reading is compared with the prose: routing the same approvals elsewhere is a divergence.
    [q2] = picture_questions(p)
    assert q2["confirmed_divergences"] and "the prose states “Approvals in journal-reversal go to the controller" in q2["confirmed_divergences"][0]
    assert "It differs from the prose" in q2["text"]
    assert any(f["severity"] == "warning" and "divergence: " in f["detail"] for f in p["findings"]), "a warning the approvers see"


def test_a_confirmed_readings_divergences_stay_in_what_the_approvers_accept(hub, tmp_path, monkeypatch):
    differs = {**READING, "steps": [{"title": "Approve the reversal", "role": "policy-owner"}]}
    seniors_read(hub, monkeypatch, tmp_path, Seniors(differs))
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    assert q["reading"]["divergences"]
    cc.answer_question(hub, co["number"], q["id"], "", "bob", picture="confirm")
    review(hub, co)
    [w] = [w for w in cc.co_warnings(hub, co["number"]) if w.get("ref") == q["id"]]
    assert "It differs from the prose" in w["text"] and "done by policy-owner" in w["text"], w


def test_confirm_is_refused_when_the_members_read_the_picture_differently(hub, tmp_path, monkeypatch):
    other = {"states_nothing": False, "sentences": ["Approvals in journal-reversal go to the policy owner."], "steps": [], "rules": []}
    seniors_read(hub, monkeypatch, tmp_path, Seniors(per_member={"senior-b": other}))
    co = new_co(hub, title="Add the journal-reversal flow")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    with pytest.raises(ValueError, match="read this picture differently"):
        cc.answer_question(hub, co["number"], q["id"], "", "bob", picture="confirm")
    with pytest.raises(ValueError, match="senior-c did not read this picture"):
        cc.answer_question(hub, co["number"], q["id"], "", "bob", picture="confirm:senior-c")
    cc.answer_question(hub, co["number"], q["id"], "", "bob", picture="confirm:senior-b")
    entry = hub.kernel.get(co["locator"])["fields"]["picture_readings"]["finance-policy"][ref(PNG)]
    assert entry["lines"] == other["sentences"] and entry["chosen"] == "confirm:senior-b"


def test_an_answer_carries_what_it_said_across_a_recompile():
    from hubproto.compile import carry_questions

    old = [{"id": "q-1", "code": "unread-rule", "status": "answered", "answer": "keep it as text", "by": "carol", "record_divergence": True,
            "category": None, "picture_answer": "correct", "confirmed_reading": ["The controller approves."], "answer_line": 12}]
    [q] = carry_questions(old, [{"id": "q-1", "code": "unread-rule", "status": "open", "answer": None, "by": None}])
    assert q["status"] == "answered" and q["record_divergence"] is True and q["picture_answer"] == "correct"
    assert q["confirmed_reading"] == ["The controller approves."] and q["answer_line"] == 12, "the release records what was answered"


def test_ollama_that_does_not_say_is_refused_and_asked_again_later(monkeypatch):
    monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
    monkeypatch.setenv("HUBPROTO_OLLAMA_URL", "http://127.0.0.1:9")  # nothing answers there
    asked = []
    answers = {"down": None, "old": {"details": {}}, "up": {"capabilities": ["completion", "vision"]}}
    state = {"now": "down"}

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps(self.body).encode()

    def urlopen(req, timeout=None):
        asked.append(state["now"])
        if answers[state["now"]] is None:
            raise OSError("connection refused")
        return Resp(answers[state["now"]])

    monkeypatch.setattr(model.urllib.request, "urlopen", urlopen)
    assert "Ollama does not say whether ollama/llava:13b reads pictures" in model.images_refused("ollama/llava:13b"), "never guessed yes"
    state["now"] = "old"
    assert model.images_refused("ollama/llava:13b"), "an Ollama that reports no capabilities: refused"
    state["now"] = "up"
    assert model.supports_images("ollama/llava:13b") and asked == ["down", "old", "up"], "a failed lookup is not kept"
    assert model.supports_images("ollama/llava:13b") and len(asked) == 3, "an answer is kept"


def test_a_picture_is_measured_as_it_is_sent():
    gw = model.ModelGateway()
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (model.MAX_IMAGE_B64 * 3 // 4 - 8 + 3)  # under the limit raw, over it in base64
    assert len(big) < model.MAX_IMAGE_B64
    with pytest.raises(ValueError, match="as sent"):
        gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[{"data": big, "media_type": "image/png"}])
    part = b"\x89PNG\r\n\x1a\n" + b"x" * (model.MAX_IMAGE_B64 * 3 // 4 - 100)
    with pytest.raises(ValueError, match="one call carries at most"):
        gw.complete(model="ollama/llava:13b", system=SYSTEM, user=USER, images=[{"data": part, "media_type": "image/png"}] * 5)
