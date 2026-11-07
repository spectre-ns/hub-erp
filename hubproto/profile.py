"""Seed profiles: install a profile's Frames into a hub and report clause conformance.

A profile is a directory under ``profiles/<name>/`` with a ``profile.json``
(its Frames, templates, required roles and the clause list of the standard it
targets), the Frames themselves, an organization-model template and form
templates. ``install`` copies the Frames into ``<frames root>/<name>/``;
``conformance`` compares the clauses the installed Frames claim in their
frontmatter against the profile's clause list.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .frames import FrameStore, load_frame

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
ORG_MODEL = "org-model"
REPLACED_DIR = ".replaced"
REPLACED_SUFFIX = ".replaced"


class ProfileError(ValueError):
    pass


def profile_dir(name: str) -> Path:
    d = PROFILES_DIR / name
    if not (d / "profile.json").exists():
        raise ProfileError(f"no profile {name!r} under {PROFILES_DIR}")
    return d


def load_profile(name: str) -> dict:
    d = profile_dir(name)
    data = json.loads((d / "profile.json").read_text(encoding="utf-8"))
    data["dir"] = str(d)
    return data


def clause_key(clause: str) -> tuple:
    """Natural order for clause numbers: 4.1 < 4.2 < 10.1."""
    parts = []
    for p in str(clause).split("."):
        parts.append(int(p) if p.isdigit() else p)
    return tuple(parts)


def _silent(*_args) -> None:
    pass


def install(hub, name: str, force: bool = False, log=print) -> list[Path]:
    """Copy the profile's Frames into ``<frames root>/<name>/`` and return the paths written.

    A Frame with the same name that already lives elsewhere under the frames
    root is moved to ``frames/.replaced/`` (with a ``.replaced`` suffix so the
    store no longer loads it) and the move is reported through ``log``. The
    organization-model template is installed only when no Frame named
    ``org-model`` exists. Existing files are kept unless ``force``.
    """
    log = log or _silent
    profile = load_profile(name)
    pdir = Path(profile["dir"])
    root = Path(hub.frames.root)
    dest = root / name
    existing = {f.name: Path(f.path) for f in hub.frames.frames.values()}
    installed: list[Path] = []

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root))
        except ValueError:
            return str(p)

    def place_frame(src: Path, target: Path) -> None:
        frame = load_frame(src)
        other = existing.get(frame.name)
        if other and other.resolve() != target.resolve():
            moved = root / REPLACED_DIR / Path(rel(other)).with_name(other.name + REPLACED_SUFFIX)
            moved.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(other), str(moved))
            existing[frame.name] = target
            log(f"replaced Frame {frame.name!r}: moved {rel(other)} to {rel(moved)}")
        if target.exists() and not force:
            log(f"kept {rel(target)} (exists; use --force to overwrite)")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        installed.append(target)
        log(f"installed {rel(target)} ({frame.name})")

    for item in profile.get("frames", []):
        src = pdir / item
        if not src.exists():
            raise ProfileError(f"profile {name!r} lists a missing Frame {item!r}")
        place_frame(src, dest / src.name)

    tmpl = pdir / profile.get("org_model_template", "org-model.template.frame.md")
    if tmpl.exists():
        if ORG_MODEL in existing:
            log(f"org-model exists at {rel(existing[ORG_MODEL])}; template not installed")
        else:
            place_frame(tmpl, dest / f"{ORG_MODEL}.frame.md")
            log("fill the org model before the first release: it has roles and folders but no members")

    for item in profile.get("templates", []):
        src = pdir / item
        if not src.exists():
            raise ProfileError(f"profile {name!r} lists a missing template {item!r}")
        target = dest / "templates" / src.name
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        installed.append(target)
        log(f"installed {rel(target)}")

    hub.frames = FrameStore(root)
    return installed


def claimed_clauses(hub) -> tuple[dict[str, list[str]], str | None]:
    """{frame name: [clauses]} from the current release, or from the Frames when there is none."""
    meta = hub.releases.current()
    if meta and meta.get("clauses"):
        return {k: [str(c) for c in v] for k, v in meta["clauses"].items()}, meta["digest"]
    return {f.name: [str(c) for c in (f.frontmatter.get("clauses") or [])]
            for f in hub.frames.frames.values() if f.scope != "reference"}, None


def conformance(hub, name: str = "iso9001") -> dict:
    """Clause checklist: which profile clauses the hub's Frames claim, and which nobody does.

    A Frame that claims a clause also covers its sub-clauses (7.5 covers 7.5.1).
    """
    profile = load_profile(name)
    titles: dict[str, str] = dict(profile.get("clauses", {}))
    claimed, release = claimed_clauses(hub)
    covered: dict[str, list[str]] = {}
    for clause in titles:
        frames = sorted(fname for fname, cls in claimed.items()
                        if any(clause == c or clause.startswith(c + ".") for c in cls))
        if frames:
            covered[clause] = frames
    ordered = sorted(titles, key=clause_key)
    return {
        "covered": {c: covered[c] for c in ordered if c in covered},
        "uncovered": [c for c in ordered if c not in covered],
        "titles": titles,
        "release": release,
        "profile": name,
    }


# ------------------------------------------------------------------ command line

def cmd_seed(args) -> None:
    from .app import Hub

    hub = Hub(Path(args.root))
    paths = install(hub, args.profile, force=args.force, log=print)
    print(f"seeded profile {args.profile!r}: {len(paths)} file(s) installed under {hub.frames.root / args.profile}")
    if getattr(args, "as_user", None):
        from .cli_templates import print_report

        print_report(hub.import_templates(by=args.as_user))
    else:
        print("templates: run `hubproto templates import --as PERSON` to put them in the library as controlled templates")
    print("next: hubproto compile, then review and release with two policy owners (or bootstrap-release)")


def cmd_conformance(args) -> None:
    if getattr(args, 'profile_pos', None):
        args.profile = args.profile_pos
    from .app import Hub

    hub = Hub(Path(args.root))
    report = conformance(hub, args.profile)
    src = f"release {report['release'][:16]}" if report["release"] else "the Frames on disk (no release in effect)"
    print(f"{args.profile} clause conformance from {src}")
    print(f"{'clause':8s} {'status':10s} {'title':68s} frames")
    for clause in sorted(report["titles"], key=clause_key):
        frames = report["covered"].get(clause)
        status = "covered" if frames else "uncovered"
        print(f"{clause:8s} {status:10s} {report['titles'][clause][:68]:68s} {', '.join(frames or [])}")
    n = len(report["titles"])
    print(f"{len(report['covered'])} of {n} clauses covered; uncovered: {', '.join(report['uncovered']) or 'none'}")


def register(sub) -> None:
    s = sub.add_parser("seed", help="install a seed profile's Frames into this hub")
    s.add_argument("profile", help="profile name under profiles/ (for example iso9001)")
    s.add_argument("--force", action="store_true", help="overwrite Frames already installed from the profile")
    s.add_argument("--as", dest="as_user", metavar="PERSON", help="also import the profile's templates into the library as this author")
    s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("conformance", help="print the clause conformance checklist of a profile")
    s.add_argument("--profile", default="iso9001")
    s.add_argument("profile_pos", nargs="?", help="profile name (same as --profile)")
    s.set_defaults(fn=cmd_conformance)
