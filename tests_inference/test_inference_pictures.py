"""Pictures on local models (issues #41 and #46): the junior cogs' model reads a picture because Ollama says it can
(``/api/show`` capabilities, never the family table), and a text-only model is refused before anything is sent."""

import struct
import zlib

import pytest

from conftest import JUNIOR, capabilities, need, text_only_model
from hubproto import model

SYSTEM = "You describe pictures. Answer in one short sentence."


def png(width: int, height: int, pixel) -> bytes:
    """A PNG drawn with the standard library: ``pixel(x, y)`` gives each pixel's (r, g, b)."""
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


WHITE, RED, BLACK = (255, 255, 255), (220, 20, 20), (0, 0, 0)
RED_SQUARE = png(128, 128, lambda x, y: RED if 32 <= x < 96 and 32 <= y < 96 else WHITE)

# "HUB" in a 5x7 pixel font, each pixel drawn as a 12x12 block, black on white.
FONT = {"H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
        "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
        "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"]}


def word_png(word: str, scale: int = 12, margin: int = 2) -> bytes:
    cols = []
    for ch in word:
        cols += [[FONT[ch][r][c] for r in range(7)] for c in range(5)] + [["0"] * 7]
    w, h = (len(cols) + 2 * margin) * scale, (7 + 2 * margin) * scale

    def pixel(x, y):
        cx, cy = x // scale - margin, y // scale - margin
        return BLACK if 0 <= cx < len(cols) and 0 <= cy < 7 and cols[cx][cy] == "1" else WHITE
    return png(w, h, pixel)


def test_ollama_says_the_junior_cogs_model_reads_pictures_and_the_hub_asks_it_not_the_family(monkeypatch):
    need(JUNIOR)
    caps = capabilities(model.served_name(JUNIOR))
    assert "vision" in caps, f"Ollama's /api/show for {JUNIOR} lists {caps}: no vision"
    monkeypatch.setattr(model, "VISION_FAMILIES", ())  # the family table plays no part where Ollama answers
    assert model.supports_images(JUNIOR), model.images_refused(JUNIOR)


def test_the_junior_cogs_model_reads_a_small_picture(timed, raw):
    need(JUNIOR)
    out = model.ModelGateway().complete(model=JUNIOR, system=SYSTEM, user="What shape is in this picture, and what colour is it?",
                                        images=[{"data": RED_SQUARE, "media_type": "image/png"}], max_tokens=200, by="inference-test")
    assert out["backend"] == "ollama" and not out["stand_in"] and out["images"][0]["media_type"] == "image/png"
    said = out["text"].lower()
    assert "red" in said and ("square" in said or "rectangle" in said), f"it did not see a red square:\n{raw()}"
    timed()


@pytest.mark.slow
def test_the_junior_cogs_model_reads_a_word_drawn_in_pixels(timed, raw):
    need(JUNIOR)
    out = model.ModelGateway().complete(model=JUNIOR, system="You read text in pictures. Answer with the word only.",
                                        user="What word is written in this picture?",
                                        images=[{"data": word_png("HUB"), "media_type": "image/png"}], max_tokens=50, by="inference-test")
    assert "hub" in out["text"].lower(), f"it did not read the word HUB:\n{raw()}"
    timed()


def test_a_text_only_model_is_refused_before_anything_is_sent(monkeypatch):
    need()  # skips when Ollama does not answer
    name = text_only_model()
    if name is None:
        pytest.skip(f"no model pulled into the Ollama at {model.ollama_url()} reads text only: run `ollama pull qwen3:0.6b`")
    sent = []
    real = model._fetch_json
    monkeypatch.setattr(model, "_fetch_json", lambda req, timeout: sent.append(req.full_url) or real(req, timeout))
    gw = model.ModelGateway()
    with pytest.raises(model.ImagesNotSupported, match=f"ollama/{name} reads text only, so it cannot be asked to read a picture"):
        gw.complete(model=f"ollama/{name}", system=SYSTEM, user="What is in this picture?",
                    images=[{"data": RED_SQUARE, "media_type": "image/png"}])
    assert sent == [] and not gw.calls, "the refused call was never sent to Ollama"
