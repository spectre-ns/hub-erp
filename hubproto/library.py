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

    def add(self, hub, parent: str, name: str, *, by: str, rights_from: str = "") -> str:
        """A folder ``name`` in ``parent`` (or at the top of the library when ``parent`` is empty); returns its path.
        A subfolder: whoever may file documents in ``parent``, or a policy owner or document controller. A top-level
        folder: a policy owner, who names the folder whose rights it takes (``rights_from``)."""
        bundle = hub.releases.bundle(hub.releases.current()["digest"])
        name = (name or "").strip()
        if not NAME.match(name):
            raise ValueError("a folder name is up to 60 letters, digits, spaces and . _ ( ) & -, and starts with a letter or digit")
        roles = set(authz.roles_of(bundle, by))
        folders = bundle.get("folders") or []
        if not parent:
            if "policy-owner" not in roles:
                raise ValueError("only a policy owner adds a folder at the top of the library (its rights are policy)")
            if rights_from not in folders:
                raise ValueError("choose the folder whose rights the new one takes")
            path = name
        else:
            if parent not in folders:
                raise ValueError(f"there is no folder {parent!r}")
            allowed = authz.check(bundle, by, "create", "document", parent)
            if not allowed["allowed"] and not ({"policy-owner", "document-controller"} & roles):
                raise ValueError(f"only people who may file documents in {parent}, a document controller or a policy owner add folders there: {allowed['why']}")
            path = f"{parent}/{name}"
            rights_from = ""
        if path in folders:
            raise ValueError(f"{path} is already there")
        data = self.all()
        data[path] = {"by": by, "at": utcnow(), "parent": parent, **({"rights_from": rights_from} if rights_from else {})}
        self._save(data)
        if not parent:  # the other policy owners see every new top-level folder
            for owner in sorted(p for p in bundle.get("members") or {} if "policy-owner" in authz.roles_of(bundle, p) and p != by):
                hub.inbox.notify(owner, kind="policy", subject=f"New top-level folder {path}",
                                 body=f"{by} added {path} to the library, with the rights of {rights_from}.")
        return path

    def rights(self) -> dict[str, str]:
        """{top-level library folder: the folder whose rights it takes}."""
        return {p: d["rights_from"] for p, d in self.all().items() if d.get("rights_from")}

    def remove(self, hub, path: str, *, by: str) -> None:
        data = self.all()
        if path not in data:
            raise ValueError(f"{path} is not a library folder (policy folders change in Settings)")
        if any(p.startswith(path + "/") for p in data):
            raise ValueError(f"{path} has folders in it: remove those first")
        if hub.kernel.query(folder=path):
            raise ValueError(f"{path} is not empty: move what is in it first")
        bundle = hub.releases.bundle(hub.releases.current()["digest"])
        if by != data[path].get("by") and not ({"document-controller", "policy-owner"} & set(authz.roles_of(bundle, by))):
            raise ValueError(f"only {data[path].get('by')}, who added it, a document controller or a policy owner removes {path}")
        data.pop(path)
        self._save(data)
