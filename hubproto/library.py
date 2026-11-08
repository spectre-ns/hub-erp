"""Library folders: subfolders people add to the document library.

The policy declares the folders that carry rights (org-model "## Folders"; Settings). Rights match by prefix, so a
subfolder of a policy folder has exactly its parent's rights. People who may create documents in a folder may add a
subfolder there to organise it, without a policy change: nobody gains or loses anything. A folder that needs rights
of its own is a policy folder, added in Settings. A library folder is removed only while it is empty, by whoever
added it or a document controller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import authz
from .release import utcnow

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()&-]{0,59}$")


class LibraryFolders:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "folders.json"

    def all(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except ValueError:
            return {}

    def paths(self) -> list[str]:
        return sorted(self.all())

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, hub, parent: str, name: str, *, by: str) -> str:
        """A subfolder ``name`` of ``parent``; returns its path. Refused unless ``by`` may create documents in ``parent``."""
        bundle = hub.releases.bundle(hub.releases.current()["digest"])
        name = (name or "").strip()
        if not NAME.match(name):
            raise ValueError("a folder name is up to 60 letters, digits, spaces and . _ ( ) & -, and starts with a letter or digit")
        if parent not in (bundle.get("folders") or []):
            raise ValueError(f"there is no folder {parent!r}")
        allowed = authz.check(bundle, by, "create", "document", parent)
        if not allowed["allowed"]:
            raise ValueError(f"only people who may file documents in {parent} add folders there: {allowed['why']}")
        path = f"{parent}/{name}"
        if path in (bundle.get("folders") or []):
            raise ValueError(f"{path} is already there")
        data = self.all()
        data[path] = {"by": by, "at": utcnow(), "parent": parent}
        self._save(data)
        return path

    def remove(self, hub, path: str, *, by: str) -> None:
        data = self.all()
        if path not in data:
            raise ValueError(f"{path} is not a library folder (policy folders change in Settings)")
        if any(p.startswith(path + "/") for p in data):
            raise ValueError(f"{path} has folders in it: remove those first")
        if hub.kernel.query(folder=path):
            raise ValueError(f"{path} is not empty: move what is in it first")
        bundle = hub.releases.bundle(hub.releases.current()["digest"])
        if by != data[path].get("by") and "document-controller" not in authz.roles_of(bundle, by):
            raise ValueError(f"only {data[path].get('by')}, who added it, or a document controller removes {path}")
        data.pop(path)
        self._save(data)
