"""Frame loading and inheritance resolution (Frame Spec v0.2).

A Frame is a Markdown file with YAML frontmatter. The four required fields are
``type``, ``name``, ``description`` and ``visibility``. ``inherits`` names one
or more parents. Resolution follows the spec: inheritance is explicit, the
child overrides its parents, and with several parents the later one wins.

This implementation resolves the full inheritance chain (transitive) and says
so in the release bill of materials, as the spec asks implementations to
disclose.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # PyYAML is optional; the subset used by Frames parses without it.
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised only without PyYAML
    yaml = None

REQUIRED_FIELDS = ("type", "name", "description", "visibility")
_TYPE_RE = re.compile(r"^frame(?: \[(\d+)\.(\d+)\])?$")


class FrameError(ValueError):
    """A Frame file does not conform to the spec."""


@dataclass
class Frame:
    path: Path
    name: str
    type: str
    description: str
    visibility: str
    body: str
    frontmatter: dict
    version: str = "0"
    scope: str = ""
    maintainer: str = ""
    inherits: list[str] = field(default_factory=list)
    sha256: str = ""

    @property
    def lines(self) -> list[str]:
        return self.body.splitlines()


def _parse_scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if (raw[0] == raw[-1]) and raw[0] in "\"'" and len(raw) >= 2:
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [_parse_scalar(x) for x in inner.split(",")] if inner else []
    return raw


def _mini_yaml(text: str) -> dict:
    """Parse the flat key/value + simple list subset Frames use."""
    out: dict = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.strip().startswith("- "):
            if key is None:
                raise FrameError("list item without a key in frontmatter")
            out.setdefault(key, [])
            if not isinstance(out[key], list):
                out[key] = []
            out[key].append(_parse_scalar(line.strip()[2:]))
            continue
        if ":" not in line:
            raise FrameError(f"cannot parse frontmatter line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        out[key] = _parse_scalar(value) if value.strip() else ""
    return out


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise FrameError("missing frontmatter block")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise FrameError("unterminated frontmatter block")
    raw = parts[0][3:]
    body = parts[1].lstrip("\n")
    data = yaml.safe_load(raw) if yaml else _mini_yaml(raw)
    if not isinstance(data, dict):
        raise FrameError("frontmatter must be a mapping")
    return data, body


def load_frame(path: Path) -> Frame:
    text = path.read_text(encoding="utf-8")
    data, body = parse_frontmatter(text)
    for key in REQUIRED_FIELDS:
        if key not in data or data[key] in ("", None):
            raise FrameError(f"{path}: missing required field {key!r}")
    if not _TYPE_RE.match(str(data["type"])):
        raise FrameError(f"{path}: type must be 'frame' or 'frame [major.minor]'")
    inherits = data.get("inherits", [])
    if isinstance(inherits, str):
        inherits = [inherits]
    return Frame(
        path=path,
        name=str(data["name"]),
        type=str(data["type"]),
        description=str(data["description"]),
        visibility=str(data["visibility"]),
        body=body,
        frontmatter=data,
        version=str(data.get("version", "0")),
        scope=str(data.get("scope", "")),
        maintainer=str(data.get("maintainer", "")),
        inherits=[str(x) for x in inherits],
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class FrameStore:
    """All Frames under a directory, indexed by name."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.frames: dict[str, Frame] = {}
        for path in sorted(self.root.rglob("*.frame.md")):
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue  # dot-directories hold replaced or archived Frames
            frame = load_frame(path)
            if frame.name in self.frames:
                raise FrameError(f"duplicate Frame name {frame.name!r}")
            self.frames[frame.name] = frame

    def get(self, name: str) -> Frame:
        try:
            return self.frames[name]
        except KeyError:
            raise FrameError(f"unknown Frame {name!r}") from None

    def resolve(self, names: list[str]) -> list[Frame]:
        """Ordered Frames for a scope selection, parents before children.

        Later entries override earlier ones, so the result is in application
        order. Each Frame appears once, at its earliest required position.
        """
        ordered: list[Frame] = []
        seen: set[str] = set()

        def visit(name: str, trail: tuple[str, ...]):
            if name in trail:
                raise FrameError(f"inheritance cycle: {' -> '.join(trail + (name,))}")
            frame = self.get(name)
            for parent in frame.inherits:
                visit(parent, trail + (name,))
            if name not in seen:
                seen.add(name)
                ordered.append(frame)

        for name in names:
            visit(name, ())
        return ordered

    def bill_of_materials(self, frames: list[Frame]) -> list[dict]:
        return [
            {
                "name": f.name,
                "version": f.version,
                "scope": f.scope,
                "maintainer": f.maintainer,
                "sha256": f.sha256,
                "inherits": list(f.inherits),
                "path": str(f.path.relative_to(self.root)),
            }
            for f in frames
        ]
