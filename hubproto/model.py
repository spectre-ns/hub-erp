"""The model gateway: the one place a context cog turns its prompt, its SOPs and an item into words.

A cog's profile (its ``## Cog:`` section in a Frame) names the model and the prompt; this gateway calls that
model when a key is configured and otherwise answers with a deterministic stand-in, so the prototype runs the
same way with or without a model. Every call is recorded (model, prompt digest, response digest, timing) so a
Track can say which model drafted what. Models draft and answer; they never sign: Gate decisions stay with the
approval-criteria sentences.

A call may carry pictures (``images``: bytes and a media type each) for a model that reads them
(``supports_images``): the hosted Anthropic API as image content blocks, Ollama in the message's ``images`` field.
Asking a model that reads text only is refused with a sentence (``ImagesNotSupported``), never sent without the
pictures. What is recorded of a call names each picture by its sha256, never its bytes.

Ollama's cloud models (``gemma4:cloud``, ``gemma4:31b-cloud``) are called through the local Ollama but run on Ollama's
servers: they are hosted models, like Claude (``ollama_cloud``, backend ``ollama-cloud``). With hosted models off
(HUBPROTO_HOSTED_MODELS) one is refused with a sentence; with them on, every listing and call says it leaves the machine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import contextmanager

from .release import utcnow
from .rules import sha256_of

MAX_CALLS_KEPT = 500  # a long-running hub keeps only the most recent model calls in memory

DEFAULT_MODEL = "stand-in"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Claude models think before they answer (on claude-opus-5-5 thinking cannot be switched off), and the thinking counts
# against the request's max_tokens. A caller's max_tokens is the length of the answer it wants, so every Claude call
# adds this much room for the thinking (HUBPROTO_ANTHROPIC_THINKING_TOKENS); only the tokens used are billed.
ANTHROPIC_THINKING_ROOM = 8000
ANTHROPIC_MAX_OUTPUT = 128000  # the most a current Claude model writes in one response; asking for more is refused
ANTHROPIC_MIN_TIMEOUT = 180  # seconds for one non-streamed response, thinking included ...
ANTHROPIC_TOKENS_A_SECOND = 40  # ... and longer for a larger budget, at a pace slower than Claude writes


def thinking_room() -> int:
    """HUBPROTO_ANTHROPIC_THINKING_TOKENS as written in the manual ("8,000", "8 000" or "8000"); a value that is not a
    number is the default, so a typo never takes the Claude cogs offline."""
    raw = re.sub(r"[\s,_]", "", os.environ.get("HUBPROTO_ANTHROPIC_THINKING_TOKENS") or "")
    return max(0, int(raw)) if raw.isdigit() else ANTHROPIC_THINKING_ROOM


def anthropic_budget(answer_tokens: int) -> int:
    """The max_tokens sent to Claude for an answer of ``answer_tokens``: the answer plus room for the thinking, never
    more than a response may hold."""
    return min(max(1, int(answer_tokens)) + thinking_room(), ANTHROPIC_MAX_OUTPUT)


def anthropic_timeout(budget: int) -> float:
    """How long one Claude response may take: enough to write the whole budget (a non-streamed response arrives only
    when it is finished)."""
    return float(max(ANTHROPIC_MIN_TIMEOUT, budget / ANTHROPIC_TOKENS_A_SECOND))


class AnswerCutOff(RuntimeError):
    """Claude stopped at the token limit before it finished: what it wrote is not an answer and is never used as one."""


def _anthropic_stopped_short(data: dict, budget: int) -> None:
    if (data or {}).get("stop_reason") == "max_tokens":
        raise AnswerCutOff(f"its answer was cut off at the token limit ({budget} tokens, thinking included)")

# ---------------------------------------------------------------- letting go of the hub lock while a model thinks
_held = threading.local()


@contextmanager
def thinking_releases(lock):
    """While a cog works under the hub lock (the queue worker), every wait on a model gives the lock up, so pages and
    saves are served meanwhile; the cog takes the lock back before it reads or writes anything."""
    prev = getattr(_held, "lock", None)
    _held.lock = lock
    try:
        yield
    finally:
        _held.lock = prev


_THINK = re.compile(r"<think>.*?</think>|</?think>", re.S)


def strip_thinking(text: str) -> str:
    """A reasoning model's thinking (qwen3's <think>...</think>, or a stray closing tag when the server strips the
    opening) is not its answer: it never reaches a page, a review or a chat."""
    if not text or "think>" not in text:
        return text
    return _THINK.sub("", text).strip()

class ModelGate:
    """Turns on a local model. Each cog has its own worker, but a local model (Ollama) answers one call at a time and
    in arrival order, and its wait would count against the call's timeout. So the hub hands out the turns itself:
    ``slots`` calls at once (HUBPROTO_MODEL_PARALLEL, default 1), the highest priority first (a chat before other
    work), then first come, first served; the timeout starts only when the call is sent."""

    def __init__(self, slots: int | None = None):
        self.slots = max(1, int(slots or os.environ.get("HUBPROTO_MODEL_PARALLEL") or 1))
        self.cv = threading.Condition()
        self.busy: list[str] = []  # who holds a turn now
        self.waiting: list[tuple[int, int, str]] = []  # (-priority, arrival, who), kept sorted
        self._n = 0

    @contextmanager
    def turn(self, priority: int = 0, who: str = ""):
        with self.cv:
            self._n += 1
            me = (-priority, self._n, who or f"call-{self._n}")
            self.waiting.append(me)
            self.waiting.sort()
            while not (len(self.busy) < self.slots and self.waiting[0] == me):
                self.cv.wait(timeout=5)
            self.waiting.pop(0)
            self.busy.append(me[2])
        try:
            yield
        finally:
            with self.cv:
                self.busy.remove(me[2])
                self.cv.notify_all()

    def ahead(self, who: str) -> int | None:
        """How many calls go before ``who`` (those running and those before it in the line); None when it is not waiting."""
        with self.cv:
            for i, w in enumerate(self.waiting):
                if w[2] == who:
                    return len(self.busy) + i
        return None


MODEL_GATE = ModelGate()
_GATES: dict[str, ModelGate] = {}
_GATES_LOCK = threading.Lock()


def gate_for(url: str, body: bytes | None = None) -> ModelGate:
    """The gate a call waits at. Today the cogs share one local model server and one GPU, so every call shares one
    gate. HUBPROTO_MODEL_GATE=per-model gives each model its own (when cogs run separate models on their own
    resources), and HUBPROTO_MODEL_GATE=off lets every call through."""
    how = (os.environ.get("HUBPROTO_MODEL_GATE") or "shared").lower()
    if how == "off":
        return _OPEN_GATE
    if how != "per-model":
        return MODEL_GATE
    try:
        name = json.loads(body or b"{}").get("model") or url
    except (ValueError, AttributeError):
        name = url
    with _GATES_LOCK:
        return _GATES.setdefault(name, ModelGate())


class _Open(ModelGate):
    @contextmanager
    def turn(self, priority: int = 0, who: str = ""):
        yield


_OPEN_GATE = _Open()


@contextmanager
def model_priority(priority: int, who: str = ""):
    """The priority (and the name, a queue task id) of this thread's model calls while it works one task."""
    prev = (getattr(_held, "priority", 0), getattr(_held, "who", ""))
    _held.priority, _held.who = priority, who
    try:
        yield
    finally:
        _held.priority, _held.who = prev


def _fetch_json(req, timeout: float):
    lock = getattr(_held, "lock", None)
    saved = None
    if lock is not None and getattr(lock, "_is_owned", lambda: False)():
        saved = lock._release_save()  # all the way out of the re-entrant lock
    try:
        url = str(getattr(req, "full_url", "") or "")
        if not url.startswith(ANTHROPIC_URL):  # a hosted API takes calls side by side; a local server does not
            with gate_for(url, getattr(req, "data", None)).turn(getattr(_held, "priority", 0), getattr(_held, "who", "")):
                # A local model that timed out is usually still busy (a long prompt, a cold load): ask once more, longer,
                # before the caller hears it did not answer (a review would otherwise be recorded as failed).
                for attempt, limit in enumerate((timeout, timeout * 1.5)):
                    try:
                        with urllib.request.urlopen(req, timeout=limit) as r:
                            return json.load(r)
                    except (TimeoutError, OSError) as e:
                        timed_out = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
                        if attempt or not timed_out or isinstance(e, urllib.error.HTTPError):
                            raise
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    finally:
        if saved is not None:
            lock._acquire_restore(saved)

PROMISE = __import__("re").compile(r"\b(let me (check|look|search|find|see)|i'?ll (check|look|search|find)|checking|looking (that|it) up|one moment)\b", __import__("re").I)


def ollama_url() -> str:
    """Where a local Ollama answers (HUBPROTO_OLLAMA_URL, e.g. http://localhost:11434); empty when not configured."""
    return (os.environ.get("HUBPROTO_OLLAMA_URL") or "").rstrip("/")


class ModelUnavailable(Exception):
    """A cog's model is not reachable: the cog is not available (a cog is bundled with its model; nothing stands in)."""


class ImagesNotSupported(ModelUnavailable):
    """The call carries pictures and the model reads text only: the call is refused, never sent without them."""


# ---------------------------------------------------------------- pictures in a call
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")  # what both the Anthropic API and Ollama take
MAX_IMAGE_B64 = 5 * 1024 * 1024  # one picture as sent (base64), as the Anthropic API limits it; the hub sends nothing larger
MAX_IMAGES_B64 = 20 * 1024 * 1024  # every picture of one call together, as sent: well inside the API's request limit
MAX_IMAGES = 20  # pictures in one call
# Ollama models that read pictures, by family: only for a development hub where the stand-in answers in place of an
# Ollama model (no Ollama to ask). A real Ollama is asked (``/api/show``); a family never stands in for its answer, as
# a family may have text-only sizes. Families with text-only sizes (gemma3, llama4) are not listed; gemma4 is, as every
# size and build Ollama's library has of it reads pictures (the junior cogs' gemma4:12b-it-qat among them).
VISION_FAMILIES = ("llava", "bakllava", "llama3.2-vision", "qwen2.5vl", "qwen3-vl", "minicpm-v", "moondream", "granite3.2-vision",
                   "llava-llama3", "llava-phi3", "gemma4")


# ---------------------------------------------------------------- what Ollama says of a model: one record per model
# One ``/api/show`` answer per model gives what it can do (capabilities), where it runs (a cloud model names a remote host)
# and its context length. It is kept SHOW_TTL seconds; a lookup that failed is asked again after SHOW_RETRY seconds; a call
# about to be sent asks again every time (``where_now``), so a send never relies on an old answer.
SHOW_TTL = 60.0
SHOW_RETRY = 15.0
_SHOW: dict[tuple[str, str], dict] = {}  # (Ollama URL, model) -> {"at", "capabilities", "remote_host", "remote_model", "context_length"}
_SHOW_MISSED: dict[tuple[str, str], float] = {}  # (Ollama URL, model) -> when Ollama last did not answer
_clock = time.monotonic  # tests move it on


def _ollama_show(name: str, max_age: float = SHOW_TTL, need: str = "") -> dict | None:
    """Ollama's record of the model (``/api/show``): {"at", "capabilities" (a list, or None when Ollama does not report
    them), "remote_host", "remote_model", "context_length" (or None)}; None when Ollama is not configured or did not
    answer. A record younger than ``max_age`` is used as it is, unless it lacks ``need`` and is older than SHOW_RETRY;
    ``max_age=0`` asks now, whatever was answered or failed before."""
    url = ollama_url()
    if not url:
        return None
    key, now = (url, name), _clock()
    rec = _SHOW.get(key)
    if max_age > 0:
        if rec is not None and now - rec["at"] < max_age and (not need or rec.get(need) is not None or now - rec["at"] < SHOW_RETRY):
            return rec
        if now - _SHOW_MISSED.get(key, float("-inf")) < SHOW_RETRY:
            return None
    try:
        req = urllib.request.Request(url + "/api/show", data=json.dumps({"model": name}).encode("utf-8"), method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            got = json.load(r)
        if not isinstance(got, dict):
            raise ValueError("not a model record")
    except Exception:
        _SHOW_MISSED[key] = _clock()
        return None
    caps = got.get("capabilities")
    info = got.get("model_info") if isinstance(got.get("model_info"), dict) else {}
    found = [int(v) for k, v in info.items() if str(k).endswith(".context_length") and str(v).isdigit()]
    _SHOW[key] = {"at": _clock(), "capabilities": [str(c) for c in caps] if isinstance(caps, list) else None,
                  "remote_host": str(got.get("remote_host") or ""), "remote_model": str(got.get("remote_model") or ""),
                  "context_length": found[0] if found else None}
    _SHOW_MISSED.pop(key, None)
    return _SHOW[key]


def _ollama_capabilities(name: str) -> list[str] | None:
    """What Ollama says the model can do (``/api/show``: "completion", "vision", "tools", ...); None when it does not say
    (an Ollama before 0.6.4) or did not answer. Either is asked again after SHOW_RETRY seconds."""
    rec = _ollama_show(name, need="capabilities")
    return rec["capabilities"] if rec else None


def images_refused(model: str) -> str:
    """Why a picture may not be sent to the profile's model ('' when it may). It is a fact about the model, not a policy:
    every Claude model the hub offers reads pictures; an Ollama model does only when Ollama reports the "vision"
    capability for it, and when Ollama does not say (it did not answer, or is too old to report capabilities) the call
    is refused, never guessed; the development stand-in takes pictures and names them, in place of an Ollama model only
    of a family that reads pictures (``VISION_FAMILIES``)."""
    model = model or DEFAULT_MODEL
    if model == DEFAULT_MODEL or model.startswith("claude"):
        return ""
    name = served_name(model)
    if backend_for(model) in OLLAMA_BACKENDS:
        caps = _ollama_capabilities(name)
        if caps is None:
            return (f"Ollama does not say whether {model} reads pictures (it did not answer, or it is a version that does not report "
                    "a model's capabilities), so it is not asked to read one: try again when Ollama answers, or update Ollama")
        return "" if "vision" in caps else image_refusal(model)
    base = name.split(":", 1)[0].lower().rsplit("/", 1)[-1]
    return "" if base in VISION_FAMILIES else image_refusal(model)


def supports_images(model: str) -> bool:
    """Whether a picture may be sent to the profile's model now (``images_refused``)."""
    return not images_refused(model)


def image_refusal(model: str) -> str:
    """The sentence a picture call to a text-only model is refused with."""
    return (f"{model or DEFAULT_MODEL} reads text only, so it cannot be asked to read a picture: give this work to a cog whose model "
            "reads pictures (a Claude model, or an Ollama model with vision)")


def _b64_len(n: int) -> int:
    return 4 * ((n + 2) // 3)


def prepare_images(model: str, images) -> list[dict]:
    """The pictures of a call, checked: each {"data": bytes, "media_type": one of IMAGE_TYPES}, not empty, at most
    MAX_IMAGE_B64 as sent (base64) and MAX_IMAGES_B64 all together, for a model that reads pictures. Returns them with
    their sha256; raises ImagesNotSupported for a model that may not be sent a picture and ValueError (a sentence) for a
    picture that cannot be sent."""
    out = []
    total = 0
    for i, img in enumerate(images or [], start=1):
        data = img.get("data") if isinstance(img, dict) else None
        kind = str((img or {}).get("media_type") or "").lower() if isinstance(img, dict) else ""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError(f"picture {i} of the call holds no bytes")
        if kind not in IMAGE_TYPES:
            raise ValueError(f"picture {i} is {kind or 'of no known type'}; a model is sent only {', '.join(IMAGE_TYPES)}")
        sent = _b64_len(len(data))
        if sent > MAX_IMAGE_B64:
            raise ValueError(f"picture {i} is {len(data):,} bytes ({sent:,} as sent); a model is sent pictures of at most "
                             f"{MAX_IMAGE_B64:,} bytes as sent (about {MAX_IMAGE_B64 * 3 // 4:,} bytes of picture)")
        total += sent
        out.append({"data": bytes(data), "media_type": kind, "sha256": hashlib.sha256(data).hexdigest()})
    if len(out) > MAX_IMAGES:
        raise ValueError(f"a call carries at most {MAX_IMAGES} pictures, not {len(out)}")
    if total > MAX_IMAGES_B64:
        raise ValueError(f"the call's pictures are {total:,} bytes as sent; one call carries at most {MAX_IMAGES_B64:,}")
    why = images_refused(model) if out else ""
    if why:
        raise ImagesNotSupported(why)
    return out


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def prompt_digest(system: str, user: str, images: list[dict] | None = None) -> str:
    """The digest of what a call asked: its words and, when it carried pictures, each picture's sha256 (never its bytes).
    A call without pictures has the digest it always had."""
    shas = [i.get("sha256") or hashlib.sha256(i["data"]).hexdigest() for i in images or []]
    return sha256_of({"system": system, "user": user, **({"images": shas} if shas else {})})[:16]


def stand_in_allowed() -> bool:
    """Development and tests only: a deterministic stand-in answers for any model. Never in a real hub."""
    return (os.environ.get("HUBPROTO_ALLOW_STAND_IN") or "").lower() in ("1", "true", "yes", "on")


def hosted_allowed() -> bool:
    """Hosted models (Claude) only when switched on explicitly: by default the hub talks only to local models (Ollama)."""
    return (os.environ.get("HUBPROTO_HOSTED_MODELS") or "").strip().lower() in ("1", "on", "yes", "true")


# ---------------------------------------------------------------- Ollama's cloud models
# Ollama's library has cloud tags (gemma4:cloud, gemma4:31b-cloud, gpt-oss:120b-cloud): the local Ollama passes the call on
# to Ollama's servers, so the prompt and the records in it leave this machine. Such a model is hosted, like Claude.
CLOUD_TAG = re.compile(r"[:-]cloud$", re.I)
OFF_MACHINE = "runs on Ollama's servers, not this machine"

def cloud_tag(model: str) -> bool:
    """Whether the model's name is one of Ollama's cloud tags: it ends in ``:cloud`` or ``-cloud``."""
    return bool(CLOUD_TAG.search(served_name((model or "").strip())))


def _remote(info: dict | None) -> bool:
    """Whether an Ollama answer (``/api/show``, or a model of ``/api/tags``) says the model runs elsewhere."""
    return bool(info) and bool(str(info.get("remote_host") or "").strip() or str(info.get("remote_model") or "").strip())


def _listed(name: str) -> dict | None:
    """The model's entry in the list Ollama gave last (``/api/tags``, kept a minute), if it is that recent."""
    if _TAGS["models"] is None or _clock() - _TAGS["at"] >= 60:
        return None
    return next((m for m in _TAGS["models"] if m["name"] in (name, f"{name}:latest") or m["name"].removesuffix(":latest") == name), None)


def ollama_cloud(model: str, listed: dict | None = None) -> bool:
    """Whether an Ollama model runs on Ollama's servers, not this machine. Ollama's own word comes first: its record of the
    model (``/api/show``) or its entry in ``/api/tags`` (``listed``, or the list Ollama gave last) names a ``remote_host``
    or ``remote_model`` for a cloud model, whatever it is called. The tag fails closed: a name ending in ``:cloud`` or
    ``-cloud`` is a cloud model, never read as local. This answer is for listing and choosing; a call about to be sent
    asks Ollama again (``where_now``) and is refused when Ollama does not say."""
    if cloud_tag(model) or _remote(listed):
        return True
    name = served_name((model or "").strip())
    if (_listed(name) or {}).get("cloud"):
        return True
    return _remote(_ollama_show(name))


class WhereUnknown(RuntimeError):
    """Ollama did not say whether the model runs on this machine or on its servers: nothing is sent."""


def where_now(model: str, backend: str) -> str:
    """Right before a call goes through Ollama: where the model runs, asked of Ollama now (``/api/show``), never from an
    older answer. Returns the backend to send with ('ollama' or 'ollama-cloud'; any other backend unchanged). Raises
    ModelUnavailable for a cloud model while hosted models are off, and WhereUnknown when Ollama does not say where a
    model without a cloud tag runs: the hub fails closed and sends nothing."""
    if backend not in OLLAMA_BACKENDS:
        return backend
    name = served_name((model or "").strip())
    cloud = cloud_tag(model)
    if not cloud:
        rec = _ollama_show(name, max_age=0)
        if rec is None:
            raise WhereUnknown(f"Ollama did not say whether {model} runs on this machine or on Ollama's servers, so nothing was sent "
                               "to it: try again when Ollama answers")
        cloud = _remote(rec) or bool((_listed(name) or {}).get("cloud"))
    if cloud and not hosted_allowed():
        raise ModelUnavailable(hosted_refusal(model))
    return "ollama-cloud" if cloud else "ollama"


def off_machine(model: str, cog: str = "") -> str:
    """The sentence that says a cog's model leaves the machine ('' for a model this machine runs): for the change order,
    the draft's note and the policy owners when a person or a cog proposes one of Ollama's cloud models."""
    if not model or model.startswith("claude") or not (cloud_tag(model) or (ollama_url() and ollama_cloud(model))):
        return ""
    whose = f"{cog}'s" if cog else "the cog's"
    return f"{model} {OFF_MACHINE}: {whose} prompts and the records in them would leave this machine"


def hosted_refusal(model: str) -> str:
    """The sentence a cloud model is refused with while hosted models are off."""
    return (f"{model} {OFF_MACHINE}, and hosted models are switched off here (HUBPROTO_HOSTED_MODELS), so nothing is sent to it: "
            "choose a model Ollama runs on this machine, or ask a system administrator to switch hosted models on")


def cloud_refused(model: str) -> str:
    """Why the hub may not send work to the model ('' when it may, as far as where it runs goes): one of Ollama's cloud
    models while hosted models are off."""
    if not model or model.startswith("claude") or hosted_allowed():
        return ""
    return hosted_refusal(model) if cloud_tag(model) or (ollama_url() and ollama_cloud(model)) else ""


def backend_for(model: str) -> str:
    """Which backend serves a profile's model: 'anthropic' for claude-* with a key, 'ollama' for 'ollama/<name>' or a
    bare name with an Ollama configured, 'ollama-cloud' for one of Ollama's cloud models (``ollama_cloud``) with hosted
    models on, 'stand-in' when allowed, else 'none'. A claude-* model is never served locally; a cloud model is hosted,
    so with hosted models off it is 'none', never stood in for."""
    if os.environ.get("HUBPROTO_MODEL_FAKE"):
        return "stand-in"
    if not model or model == DEFAULT_MODEL:
        return "stand-in" if stand_in_allowed() else "none"
    if model.startswith("claude"):
        if not hosted_allowed():
            return "none"  # hosted models are off: a claude-* cog is unavailable, never stood in for
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
    elif ollama_url():
        if ollama_cloud(model):
            return "ollama-cloud" if hosted_allowed() else "none"
        return "ollama"
    elif cloud_tag(model) and not hosted_allowed():
        return "none"
    return "stand-in" if stand_in_allowed() else "none"


def leaves_machine(backend: str) -> bool:
    """Whether a call to this backend sends the prompt off this machine."""
    return backend in ("anthropic", "ollama-cloud")


OLLAMA_BACKENDS = ("ollama", "ollama-cloud")  # both are called through the Ollama at HUBPROTO_OLLAMA_URL


CLAUDE_MODELS = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
_TAGS: dict = {"at": 0.0, "models": None, "error": ""}


def served_name(model: str) -> str:
    """The Ollama model a profile names: ``ollama/<name>`` or a bare ``<name>``."""
    return model.split("/", 1)[1] if model.startswith("ollama/") else model


def context_length(model: str) -> int:
    """The longest context the model was trained for, as Ollama reports it (qwen3:14b: 40,960 tokens); the last length
    Ollama gave while it does not answer, so the window (and Ollama's loaded model) stays the same."""
    backend = backend_for(model)
    if backend == "anthropic":
        return 200000
    if backend not in OLLAMA_BACKENDS:
        return 32768
    name = served_name(model)
    rec = _ollama_show(name) or _SHOW.get((ollama_url(), name)) or {}
    return rec.get("context_length") or 32768


def num_ctx_for(model: str) -> int:
    """The window each call asks for: the model's own length, capped (HUBPROTO_MODEL_CTX_MAX, default 65536) or set
    (HUBPROTO_MODEL_CTX); never more than the model was trained for. One value per model, so Ollama does not reload."""
    native = context_length(model)
    want = int(os.environ.get("HUBPROTO_MODEL_CTX") or 0) or min(native, int(os.environ.get("HUBPROTO_MODEL_CTX_MAX") or 65536))
    return max(2048, min(want, native))


def prompt_budget_chars(model: str, max_tokens: int = 1200) -> int:
    """How many characters of prompt fit the window with room for the answer (about 3.2 characters a token for
    English with markup), unless HUBPROTO_MODEL_MAX_CHARS says otherwise."""
    if os.environ.get("HUBPROTO_MODEL_MAX_CHARS"):
        return int(os.environ["HUBPROTO_MODEL_MAX_CHARS"])
    return int(max(4096, num_ctx_for(model) - max_tokens - 1024) * 3.2)


def ollama_models(refresh: bool = False) -> tuple[list[dict], str]:
    """The models installed in Ollama (cached for a minute): [{name, params, family, quant, size_gb, cloud}], and an error.
    ``cloud``: the model runs on Ollama's servers, not this machine (``ollama_cloud``)."""
    url = ollama_url()
    if not url:
        return [], "no Ollama is configured (HUBPROTO_OLLAMA_URL)"
    if not refresh and _TAGS["models"] is not None and _clock() - _TAGS["at"] < 60:
        return _TAGS["models"], _TAGS["error"]
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=4) as r:
            data = json.load(r)
        models = [{"name": m["name"], "params": (m.get("details") or {}).get("parameter_size") or "", "family": (m.get("details") or {}).get("family") or "",
                   "quant": (m.get("details") or {}).get("quantization_level") or "", "size_gb": round((m.get("size") or 0) / 1e9, 1),
                   "cloud": ollama_cloud(m["name"], listed=m)}
                  for m in data.get("models") or []]
        _TAGS.update(at=_clock(), models=sorted(models, key=lambda m: m["name"]), error="")
    except Exception as e:
        _TAGS.update(at=_clock(), models=[], error=f"Ollama at {url} did not answer: {e}")
    return _TAGS["models"], _TAGS["error"]


def _installed(name: str, models: list[dict]) -> bool:
    names = {m["name"] for m in models}
    return name in names or f"{name}:latest" in names or name.removesuffix(":latest") in names


def model_label(m: dict) -> str:
    """How a model Ollama lists is named in a picker: its size and family, and where it runs."""
    what = " ".join(x for x in (m.get("params"), m.get("family")) if x)
    where = OFF_MACHINE if m.get("cloud") else "local"
    return f"{m['name']} ({what + ', ' if what else ''}{where})"


def available_models(refresh: bool = False) -> dict:
    """What a cog's profile can name: installed Ollama models, Claude models when a key is set, and what a claude-*
    name falls back to without one. Ollama's cloud models are hosted: labelled as running on Ollama's servers, after the
    local ones, and not available while hosted models are off."""
    local, err = ollama_models(refresh)
    key = bool(os.environ.get("ANTHROPIC_API_KEY")) and hosted_allowed()
    # The most capable (largest) model on this machine first; a cloud model (no size here) after every local one.
    local = sorted(local, key=lambda m: (bool(m.get("cloud")), -(m.get("size_gb") or 0)))
    choices = []
    for m in local:
        cloud = bool(m.get("cloud"))
        label = model_label(m) + ("" if not cloud or hosted_allowed() else ": not available while hosted models are off")
        choices.append({"value": f"ollama/{m['name']}", "label": label, "available": not cloud or hosted_allowed(), "cloud": cloud})
    if hosted_allowed():  # otherwise the hub offers only the models Ollama serves here
        choices += [{"value": c, "label": c + (" (Anthropic)" if key else " (needs an Anthropic key: not available here)"), "available": key, "cloud": False}
                    for c in CLAUDE_MODELS]
    return {"ollama": local, "ollama_error": err, "anthropic_key": key, "choices": choices}


def resolve_model(model: str, refresh: bool = False) -> dict:
    """Who would answer for a profile's model right now, and whether that model is there. ``leaves_machine``: the call
    would send the prompt off this machine (a Claude model, or one of Ollama's cloud models)."""
    model = model or DEFAULT_MODEL
    backend = backend_for(model)
    if backend in OLLAMA_BACKENDS:
        name = served_name(model)
        local, err = ollama_models(refresh)
        ok = _installed(name, local)
        cloud = backend == "ollama-cloud"
        here = f"hosted: {OFF_MACHINE}" if cloud else "local"
        return {"asked": model, "backend": backend, "served": name, "available": ok, "leaves_machine": cloud,
                "note": here if ok else (err or f"{name} is not installed in Ollama (ollama pull {name})" + (f"; it {OFF_MACHINE}" if cloud else ""))}
    if backend == "anthropic":
        return {"asked": model, "backend": "anthropic", "served": model, "available": True, "leaves_machine": True, "note": "Anthropic API"}
    if backend == "stand-in":
        return {"asked": model, "backend": "stand-in", "served": "stand-in", "available": True, "leaves_machine": False,
                "note": "development stand-in (HUBPROTO_ALLOW_STAND_IN)"}
    away = False
    if not model or model == DEFAULT_MODEL:
        why = "the profile names no model"
    elif model.startswith("claude"):
        why, away = f"{model} needs an Anthropic key (ANTHROPIC_API_KEY)", True
    elif cloud_refused(model):
        why, away = cloud_refused(model), True
    else:
        why = f"{model} needs an Ollama (HUBPROTO_OLLAMA_URL)"
    return {"asked": model, "backend": "none", "served": "", "available": False, "leaves_machine": away, "note": why}


def cog_availability(bundle: dict, cog: str) -> tuple[bool, str]:
    """Whether a cog can be used now: it is bundled with its profile's model, and that model must answer."""
    prof = (bundle.get("cogs") or {}).get(cog)
    if prof is None:
        return (True, "") if stand_in_allowed() else (False, f"{cog} has no profile naming its model")
    r = resolve_model(prof.get("model") or "")
    return (True, "") if r["available"] else (False, f"{cog} is not available: {r['note']}")


class ModelGateway:
    def __init__(self):
        self.calls: deque[dict] = deque(maxlen=MAX_CALLS_KEPT)  # the recent calls, for tests and /models; bounded
        self._last_usage = None

    def available(self, model: str) -> bool:
        return backend_for(model) != "stand-in"

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1200, by: str = "", history: list[dict] | None = None,
                 images: list[dict] | None = None) -> dict:
        """Returns {"text", "model", "backend", "stand_in", "prompt_digest", "response_digest", "ms", "at"} (and "images":
        [{"sha256", "media_type", "bytes"}] when the call carried pictures). ``images``: [{"data": bytes, "media_type"}],
        sent with ``user`` to a model that reads pictures (``prepare_images``); a text-only model is refused."""
        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        backend = backend_for(model)
        if backend == "none":
            raise ModelUnavailable(resolve_model(model)["note"])
        imgs = prepare_images(model, images) if images else []  # refused before anything is sent, never dropped
        try:
            backend = where_now(model, backend)  # through Ollama: where the model runs, asked now; refused when it does not say
            if backend == "anthropic":
                text = self._anthropic(model, system, user, max_tokens, history, imgs)
            elif backend in OLLAMA_BACKENDS:
                text = self._ollama(model, system, user, max_tokens, history, imgs)
            else:
                text = self._stand_in(system, user, imgs)
        except ModelUnavailable:
            raise  # a cloud model while hosted models are off: refused, never stood in for
        except Exception as e:
            if not stand_in_allowed():
                raise ModelUnavailable(f"{model} did not answer: {e}") from None
            text = f"[{backend} model {model} did not answer: {e}]\n" + self._stand_in(system, user, imgs)
            backend = "stand-in"
        text = strip_thinking(text)
        stand_in = backend == "stand-in"
        usage = getattr(self, "_last_usage", None) if backend in OLLAMA_BACKENDS else None
        self._last_usage = None
        out = {"text": text, "model": model, "served_model": (usage or {}).get("served_model") or (model if backend == "anthropic" else "stand-in"),
               "backend": backend, "stand_in": stand_in, "leaves_machine": leaves_machine(backend), "by": by, "at": utcnow(),
               "prompt_chars": len(system) + len(user), "usage": usage,
               "prompt_digest": prompt_digest(system, user, imgs), "response_digest": sha256_of(text)[:16],
               "ms": int((time.monotonic() - t0) * 1000)}
        if imgs:  # each picture by its hash: its bytes are never recorded
            out["images"] = [{"sha256": i["sha256"], "media_type": i["media_type"], "bytes": len(i["data"])} for i in imgs]
        self.calls.append(out)
        u = usage or {}
        sys.stdout.write(f"model call by={by or '-'} model={model} served_by={out['served_model']} backend={backend} prompt_chars={out['prompt_chars']}"
                         + (f" prompt_tokens={u.get('prompt_tokens')} output_tokens={u.get('output_tokens')} done={u.get('done_reason')}" if u else "")
                         + (f" images={','.join(i['sha256'][:12] for i in imgs)}" if imgs else "")
                         + f" ms={out['ms']}\n")
        sys.stdout.flush()
        return out

    @staticmethod
    def _anthropic(model: str, system: str, user: str, max_tokens: int, history: list[dict] | None = None, images: list[dict] | None = None) -> str:
        turns = [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")]
        # Pictures are image content blocks before the words of the same turn (the API's advice for a picture and a question).
        content = ([{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": _b64(i["data"])}} for i in images]
                   + [{"type": "text", "text": user}]) if images else user
        budget = anthropic_budget(max_tokens)
        body = json.dumps({"model": model, "max_tokens": budget, "system": system,
                           "messages": turns + [{"role": "user", "content": content}]}).encode("utf-8")
        req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
            "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        data = _fetch_json(req, anthropic_timeout(budget))
        _anthropic_stopped_short(data, budget)
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")

    def _ollama(self, model: str, system: str, user: str, max_tokens: int, history: list[dict] | None = None,
                images: list[dict] | None = None) -> str:
        """A local model through Ollama's chat API (``ollama/<name>`` or any name when HUBPROTO_OLLAMA_URL is set); pictures go
        in the user message's ``images`` field, base64."""
        name = served_name(model)
        ceiling = prompt_budget_chars(model, max_tokens)  # what fits in the model's window with room to answer
        if len(system) + len(user) > ceiling:
            keep = max(ceiling - len(user) - 200, 2000)
            system = system[:keep] + "\n[... context trimmed to fit the model's window]"
        payload = {"model": name, "stream": False,
                   "options": {"num_predict": max_tokens, "num_ctx": num_ctx_for(model)},
                   "messages": [{"role": "system", "content": system}]
                               + [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")]
                               + [{"role": "user", "content": user, **({"images": [_b64(i["data"]) for i in images]} if images else {})}]}
        if not os.environ.get("HUBPROTO_MODEL_THINK"):
            payload["think"] = False  # a thinking model otherwise spends its budget before it answers
        data = None
        for attempt in (payload, {k: v for k, v in payload.items() if k != "think"}):
            body = json.dumps(attempt).encode("utf-8")
            req = urllib.request.Request(ollama_url() + "/api/chat", data=body, method="POST", headers={"content-type": "application/json"})
            try:
                data = _fetch_json(req, float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 180))
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace")
                if "think" in detail.lower() and "think" in attempt:
                    continue  # this model does not take the flag; ask again without it
                raise RuntimeError(f"HTTP {e.code} from Ollama for model {name!r}: {detail}") from None
        msg = (data or {}).get("message") or {}
        self._last_usage = {"prompt_tokens": (data or {}).get("prompt_eval_count"), "output_tokens": (data or {}).get("eval_count"),
                            "done_reason": (data or {}).get("done_reason"), "served_model": name}
        return msg.get("content") or (data or {}).get("response") or msg.get("thinking") or ""

    # ------------------------------------------------------------ tool use (the record tools, MCP-shaped)
    def complete_with_tools(self, *, model: str, system: str, user: str, tools: list[dict], call_tool, by: str = "",
                            history: list[dict] | None = None, max_tokens: int = 800, max_rounds: int = 6) -> dict:
        """A turn in which the model may call tools (function calling) before it answers. ``call_tool(name, args)``
        runs a tool and returns a dict. Returns complete()'s shape plus ``tool_calls`` (what was looked at)."""
        from .tools import as_text

        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        backend = backend_for(model)
        if backend == "none":
            raise ModelUnavailable(resolve_model(model)["note"])
        calls: list[dict] = []

        def run(name, args):
            res = call_tool(name, args or {})
            calls.append({"tool": name, "arguments": args or {}, "ok": "error" not in res})
            return res

        usage = None
        try:
            backend = where_now(model, backend)  # through Ollama: where the model runs, asked now; refused when it does not say
            if backend in OLLAMA_BACKENDS:
                text, usage = self._ollama_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text)
            elif backend == "anthropic":
                text = self._anthropic_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text)
            else:
                text = self._stand_in_tools(user, run)
        except ModelUnavailable:
            raise  # a cloud model while hosted models are off: refused, never stood in for
        except Exception as e:
            if not stand_in_allowed():
                raise ModelUnavailable(f"{model} did not answer: {e}") from None
            text = f"[{backend} model {model} did not answer: {e}]\n" + self._stand_in_tools(user, run)
            backend = "stand-in"
        text = strip_thinking(text)
        out = {"text": text, "model": model, "served_model": (usage or {}).get("served_model") or (model if backend == "anthropic" else "stand-in"),
               "backend": backend, "stand_in": backend == "stand-in", "leaves_machine": leaves_machine(backend), "by": by, "at": utcnow(),
               "prompt_chars": len(system) + len(user),
               "usage": usage, "tool_calls": calls, "prompt_digest": sha256_of({"system": system, "user": user})[:16],
               "response_digest": sha256_of(text)[:16], "ms": int((time.monotonic() - t0) * 1000)}
        self.calls.append(out)
        u = usage or {}
        sys.stdout.write(f"model call by={by or '-'} model={model} served_by={out['served_model']} backend={backend} tools={len(calls)}"
                         f" [{', '.join(c['tool'] for c in calls)}] prompt_chars={out['prompt_chars']}"
                         + (f" prompt_tokens={u.get('prompt_tokens')} output_tokens={u.get('output_tokens')}" if u else "") + f" ms={out['ms']}\n")
        sys.stdout.flush()
        return out

    def _ollama_tools(self, model, system, user, history, tools, run, max_tokens, max_rounds, as_text):
        name = served_name(model)
        msgs = [{"role": "system", "content": system}] + [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")] \
            + [{"role": "user", "content": user}]
        fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        usage = {"prompt_tokens": 0, "output_tokens": 0, "served_model": name}
        for round_ in range(max_rounds + 1):
            payload = {"model": name, "stream": False, "messages": msgs, "tools": fns if round_ < max_rounds else [],
                       "options": {"num_predict": max_tokens, "num_ctx": num_ctx_for(model)}}
            if not os.environ.get("HUBPROTO_MODEL_THINK"):
                payload["think"] = False
            req = urllib.request.Request(ollama_url() + "/api/chat", data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"content-type": "application/json"})
            try:
                data = _fetch_json(req, float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 240))
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"HTTP {e.code} from Ollama for model {name!r}: {e.read()[:300].decode('utf-8', 'replace')}") from None
            usage["prompt_tokens"] += data.get("prompt_eval_count") or 0
            usage["output_tokens"] += data.get("eval_count") or 0
            msg = data.get("message") or {}
            tcs = msg.get("tool_calls") or []
            if not tcs:
                text = (msg.get("content") or msg.get("thinking") or "").strip()
                if round_ < max_rounds and PROMISE.search(text) and len(text) < 240:
                    # It said it would look but did not call a tool: once, tell it to go ahead.
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({"role": "user", "content": "Go ahead: call the tools now, then answer with what you found."})
                    continue
                return text, usage
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                fn = tc.get("function") or {}
                res = run(fn.get("name") or "", fn.get("arguments") or {})
                msgs.append({"role": "tool", "content": as_text(res), "tool_name": fn.get("name") or ""})
        return "", usage

    @staticmethod
    def _anthropic_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text):
        msgs = [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")] + [{"role": "user", "content": user}]
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        budget = anthropic_budget(max_tokens)
        for round_ in range(max_rounds + 1):
            body = {"model": model, "max_tokens": budget, "system": system, "messages": msgs}
            if round_ < max_rounds:
                body["tools"] = specs
            req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
                "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
            data = _fetch_json(req, anthropic_timeout(budget))
            _anthropic_stopped_short(data, budget)  # a cut-off tool call or answer is neither run nor used
            content = data.get("content") or []
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            msgs.append({"role": "assistant", "content": content})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": as_text(run(b["name"], b.get("input") or {}))}
                                                     for b in uses]})
        return ""

    @staticmethod
    def _stand_in_tools(user: str, run) -> str:
        """Without a model: look up any record numbers mentioned, else search for the words; say what was found."""
        import re as _re

        numbers = _re.findall(r"\b[A-Z]{2,}-\d[\w-]*\b", user or "")
        lines = ["(No model is configured, so I can only look things up, not reason about them.)"]
        if numbers:
            for n in numbers[:3]:
                r = run("get_record", {"number": n})
                if "error" in r:
                    lines.append(f"{n}: {r['error']}.")
                else:
                    wait = f", waiting for {', '.join(r['waiting_for'])}" if r.get("waiting_for") else ""
                    lines.append(f"{r['number']} ({r['type']}) {r['title']}: {r['state']}{wait}.")
            return "\n".join(lines)
        low = (user or "").lower()
        if any(w in low for w in ("waiting", "my queue", "to do", "todo", "on my plate")):
            r = run("waiting_for", {})
            lines.append(f"{r['count']} item(s) wait for {r['who']}: " + "; ".join(f"{i['record']} ({i['step']})" for i in r["items"][:8]) + ".")
            return "\n".join(lines)
        words = [w for w in _re.findall(r"[a-zA-Z0-9]{4,}", low) if w not in {"what", "which", "where", "when", "about", "there", "have", "does", "with", "that", "this", "please", "could", "would", "find", "show", "tell", "know"}][:3]
        r = run("search_records", {"query": " ".join(words), "limit": 5})
        lines.append(f"Records matching '{' '.join(words)}': " + ("; ".join(f"{x['number']} {x['title']} ({x['state']})" for x in r["records"]) or "none") + ".")
        return "\n".join(lines)

    @staticmethod
    def _stand_in(system: str, user: str, images: list[dict] | None = None) -> str:
        """Deterministic words for a run without a model: a review that walks the SOP headings it was given. Pictures it
        was given are named by their hash (it reads none)."""
        heads = [line[3:].strip() for line in system.splitlines() if line.startswith("## ")][:6]
        lines = ["Stand-in review (no model configured):"]
        for i in images or []:
            lines.append(f"- picture sha256:{i['sha256']} ({i['media_type']}, {len(i['data'])} bytes): given; the stand-in reads no picture.")
        for h in heads:
            lines.append(f"- {h}: checked against the item; no finding.")
        findings = user.split("FINDINGS:", 1)[1].split("\n\n", 1)[0].strip() if "FINDINGS:" in user else ""
        if findings:
            lines.append(findings)
        lines.append("Verdict: fail" if "- [error]" in findings else "Verdict: pass")
        return "\n".join(lines)
