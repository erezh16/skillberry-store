"""Minimal prototype of the §34 git-backed artifact store, used only to test the design's claims.

Not production code: git CLI plumbing, one bare repo per namespace, no API, no database.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

ENV = dict(os.environ, GIT_AUTHOR_NAME="sbs", GIT_AUTHOR_EMAIL="sbs@local",
           GIT_COMMITTER_NAME="sbs", GIT_COMMITTER_EMAIL="sbs@local")
ZERO = "0" * 40


def git(repo, *args, inp: bytes | None = None, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], input=inp, capture_output=True, env=ENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode().strip()}")
    return r.stdout.decode().strip()


def init_ns(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    # internal refs are never advertised to fetchers (the mirror refs are)
    git(path, "config", "transfer.hideRefs", "refs/artifacts")
    git(path, "config", "--add", "transfer.hideRefs", "refs/keep")
    git(path, "config", "gc.auto", "0")
    return path


# ---------------- identity ----------------

def canonical_entries(src: Path, ignore: set[str] = frozenset()):
    """(relpath, mode, bytes) for every regular file; symlinks/empty dirs reported, not stored."""
    out, skipped = [], []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if any(part in ignore for part in rel.split("/")):
            continue
        if p.is_symlink():
            skipped.append((rel, "symlink"))
        elif p.is_file():
            mode = "100755" if os.stat(p).st_mode & stat.S_IXUSR else "100644"
            out.append((rel, mode, p.read_bytes()))
        elif p.is_dir() and not any(p.iterdir()):
            skipped.append((rel, "empty directory"))
    return out, skipped


def content_digest(entries) -> str:
    h = hashlib.sha256()
    for rel, mode, data in sorted(entries, key=lambda e: e[0].encode()):
        h.update(rel.encode() + b"\0" + mode.encode() + b"\0" + data + b"\0")
    return "sha256:" + h.hexdigest()


def harbor_digest(entries) -> str:
    """Re-implementation of harbor.skills.compute_skill_digest (component-wise path order, no mode)."""
    h = hashlib.sha256()
    for rel, _mode, data in sorted(entries, key=lambda e: Path(e[0])):
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\0")
    return "sha256:" + h.hexdigest()


# ---------------- object writing ----------------

def write_tree(repo, entries) -> str:
    """Build nested git trees from (relpath, mode, bytes). Raw bytes: no filters, no attributes."""
    root: dict = {}
    for rel, mode, data in entries:
        blob = git(repo, "hash-object", "-w", "--no-filters", "--stdin", inp=data)
        node = root
        *dirs, name = rel.split("/")
        for d in dirs:
            node = node.setdefault(d, {})
        node[name] = (mode, blob)
    return _mk(repo, root)


def _mk(repo, node) -> str:
    lines = []
    for name, v in node.items():
        if isinstance(v, dict):
            lines.append(f"040000 tree {_mk(repo, v)}\t{name}")
        else:
            lines.append(f"{v[0]} blob {v[1]}\t{name}")
    return git(repo, "mktree", inp=("\n".join(lines) + "\n").encode())


def tree_of(repo, entries_by_role: dict[str, str], extra_blobs: dict[str, bytes] = {}) -> str:
    lines = [f"040000 tree {t}\t{role}" for role, t in entries_by_role.items()]
    for name, data in extra_blobs.items():
        b = git(repo, "hash-object", "-w", "--no-filters", "--stdin", inp=data)
        lines.append(f"100644 blob {b}\t{name}")
    return git(repo, "mktree", inp=("\n".join(lines) + "\n").encode())


def commit(repo, tree, parents=(), msg="v") -> str:
    args = ["commit-tree", tree, "-m", msg]
    for p in parents:
        args += ["-p", p]
    return git(repo, *args)


def line_ref(kind, name, branch="main"):
    return f"refs/artifacts/{kind}/{name}/heads/{branch}"


def transact(repo, updates) -> tuple[bool, str]:
    """All-or-nothing ref transaction. updates: [(ref, new, old_or_None_for_create)]."""
    cmds = ["start"]
    for ref, new, old in updates:
        cmds.append(f"create {ref} {new}" if old is None else f"update {ref} {new} {old}")
    cmds += ["prepare", "commit"]
    r = subprocess.run(["git", "-C", str(repo), "update-ref", "--stdin"],
                       input=("\n".join(cmds) + "\n").encode(), capture_output=True)
    return r.returncode == 0, r.stderr.decode().strip()


def rev(repo, ref):
    return git(repo, "rev-parse", "--verify", "-q", ref, check=False) or None


def canonical_json(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
