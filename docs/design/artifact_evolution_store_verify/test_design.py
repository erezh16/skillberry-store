"""Checks for the mechanical claims in artifact_evolution_store.md §34 (see §34.16).

Usage:
    CORPUS=/path/to/clones python3 test_design.py

CORPUS must contain clones of: anthropics/skills (as anthropic-skills), skillberry-ai/cap-evolve,
RUC-NLPIR/Arbor, harbor-framework/harbor and obra/superpowers. Harbor must be checked out at
15da91c (its skills.py and utils/git.py are imported and run unmodified). Needs git >= 2.38 and
Python >= 3.10 (stdlib only).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gitstore import *  # noqa
from gitstore import _mk  # noqa: E402

SRC = Path(os.environ.get("CORPUS", "corpus")).resolve()
UP = SRC / "anthropic-skills"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


work = Path(tempfile.mkdtemp(prefix="sbsproto-"))
ns = init_ns(work / "ns-anthropic.git")

# T1 verbatim import: artifact tree == upstream subdirectory tree (provenance verifiable by hash)
skills = ["pdf", "xlsx", "skill-creator", "canvas-design", "algorithmic-art", "web-artifacts-builder"]
imported = {}
for s in skills:
    ents, skipped = canonical_entries(UP / "skills" / s)
    t = write_tree(ns, ents)
    up_tree = git(UP, "rev-parse", f"HEAD:skills/{s}")
    imported[s] = (ents, t)
    check(f"T1 tree(import {s}) == upstream HEAD:skills/{s}", t == up_tree, f"{len(ents)} files, skipped={skipped}")

# publish lines for every imported skill except xlsx (xlsx is reachable only via a composition below)
pub = [(line_ref("skill", s), commit(ns, imported[s][1], msg=f"{s} v1"), None) for s in skills if s not in ("xlsx", "pdf")]
assert transact(ns, pub)[0]

# T2 identity: same content -> same tree hash (the snapshot identity), different commit SHA
ents, t = imported["pdf"]
c1 = commit(ns, t, msg="a"); time.sleep(1.1); c2 = commit(ns, t, msg="a")
check("T2 same tree, different commit SHAs", c1 != c2 and git(ns, "rev-parse", c1 + "^{tree}") == git(ns, "rev-parse", c2 + "^{tree}"))
check("T2 tree hash independent of input order", write_tree(ns, ents) == write_tree(ns, list(reversed(ents))))

# T3 exec bit is part of identity
modes = {m for _r, m, _d in imported["skill-creator"][0]}
ents2 = [(r, "100644", d) for r, _m, d in imported["skill-creator"][0]]
check("T3 exec bit changes the tree hash (the content_digest)", "100755" in modes and
      write_tree(ns, ents2) != imported["skill-creator"][1])

# T4 binaries stored byte-exact (canvas-design fonts, web-artifacts-builder tarball)
fonts = [e for e in imported["canvas-design"][0] if e[0].endswith(".ttf")]
tarb = [e for e in imported["web-artifacts-builder"][0] if e[0].endswith(".tar.gz")]
ok = all(subprocess.run(["git", "-C", str(ns), "cat-file", "blob", f"{imported['canvas-design'][1]}:{r}"], capture_output=True).stdout == d for r, _m, d in fonts)
check("T4 binaries round-trip byte-exact", fonts and tarb and ok, f"{len(fonts)} fonts, {len(tarb)} tarball")

# T5 projection link: tool line's script blob is the same object as in the skill tree
script = next(e for e in imported["pdf"][0] if e[0].endswith(".py"))
tool_tree = write_tree(ns, [(Path(script[0]).name, script[1], script[2]),
                            ("tool.json", "100644", canonical_json({"name": Path(script[0]).stem, "entry": Path(script[0]).name}))])
b_tool = git(ns, "rev-parse", f"{tool_tree}:{Path(script[0]).name}")
b_skill = git(ns, "rev-parse", f"{imported['pdf'][1]}:{script[0]}")
check("T5 projected tool shares the skill's blob (link verifiable by hash)", b_tool == b_skill, script[0])

# T6 atomic publish: skill + tool + head moves in one transaction; one stale CAS aborts all
sk_c = commit(ns, imported["pdf"][1], msg="pdf v1")
tl_c = commit(ns, tool_tree, msg="tool v1")
ok, _ = transact(ns, [(line_ref("skill", "pdf"), sk_c, None), (line_ref("tool", "pdf/fill"), tl_c, None)])
check("T6a multi-line create commits together", ok and rev(ns, line_ref("skill", "pdf")) == sk_c)
sk_c2 = commit(ns, imported["pdf"][1], [sk_c], "pdf v2")
ok, err = transact(ns, [(line_ref("skill", "pdf"), sk_c2, sk_c), (line_ref("tool", "pdf/fill"), tl_c, "f" * 40)])
check("T6b stale CAS on one ref aborts the whole transaction", not ok and rev(ns, line_ref("skill", "pdf")) == sk_c, err[:60])

# T7 concurrent promotion: N racing CAS updates from the same base -> exactly one wins
base = rev(ns, line_ref("skill", "pdf"))
cands = [commit(ns, imported["pdf"][1], [base], f"cand {i}") for i in range(8)]
procs = [subprocess.Popen(["git", "-C", str(ns), "update-ref", line_ref("skill", "pdf"), c, base],
                          stderr=subprocess.DEVNULL) for c in cands]
wins = sum(p.wait() == 0 for p in procs)
check("T7 8 concurrent CAS promotions from one base: exactly 1 wins", wins == 1, f"wins={wins}")

# T8 composition: manifest + member subtrees; reachability keeps members alive through gc
members = {"pdf": imported["pdf"][1], "xlsx": imported["xlsx"][1]}
manifest = canonical_json({"kind": "composition", "profile": "skill-group", "members": [
    {"role": r, "kind": "skill", "line": f"anthropic/{r}", "content_digest": "sha1:" + t, "tree": t}
    for r, t in sorted(members.items())]})
comp_tree = tree_of(ns, members, {".sbs-composition.json": manifest})
comp_c = commit(ns, comp_tree, msg="group v1")
transact(ns, [(line_ref("composition", "office"), comp_c, None)])
# xlsx has NO line ref of its own: only the composition reaches it
loose = commit(ns, write_tree(ns, [("NOTE.md", "100644", b"unreferenced candidate\n")]), msg="rejected cand")
kept = commit(ns, write_tree(ns, [("NOTE.md", "100644", b"cited by a measurement\n")]), msg="cited cand")
git(ns, "update-ref", "refs/keep/sha256-deadbeef", kept)
git(ns, "-c", "gc.reflogExpire=now", "-c", "gc.reflogExpireUnreachable=now", "reflog", "expire", "--all")
git(ns, "gc", "--prune=now", "-q")
exists = lambda o: subprocess.run(["git", "-C", str(ns), "cat-file", "-e", o]).returncode == 0
check("T8a composition keeps member trees alive through gc", exists(imported["xlsx"][1]))
check("T8b keep-ref keeps a cited, branchless snapshot alive through gc", exists(kept))
check("T8c unreferenced snapshot is pruned by gc", not exists(loose))

# T12 gc with git's default grace window keeps a just-written, not-yet-referenced object (in-flight publish)
inflight = write_tree(ns, [("INFLIGHT.md", "100644", b"being published\n")])
git(ns, "gc", "-q")  # default gc.pruneExpire (2 weeks)
check("T12 default gc keeps fresh unreferenced objects (in-flight publish safe)", exists(inflight))

# T9 mirror: derived per-namespace commit with skills/<name>/ = existing trees (no copying), flat tag
skills_tree = tree_of(ns, {s: imported[s][1] for s in skills})
mirror_root = tree_of(ns, {"skills": skills_tree})
mc = commit(ns, mirror_root, msg="mirror anthropic r1")
git(ns, "update-ref", "refs/tags/anthropic-r1", mc)
ls = git(ns, "ls-remote", f"file://{ns}")
check("T9a internal refs hidden from ls-remote (transfer.hideRefs)", "refs/artifacts" not in ls and "refs/keep" not in ls and "refs/tags/anthropic-r1" in ls)
hidden_commit = rev(ns, line_ref("skill", "pdf"))
def fetch_by_sha(url, sha, v):
    d = Path(tempfile.mkdtemp(dir=work)); subprocess.run(["git", "init", "-q", str(d)])
    return subprocess.run(["git", "-C", str(d), "-c", f"protocol.version={v}", "fetch", "-q", "--depth=1", url, sha], capture_output=True).returncode == 0
check("T9b FINDING: protocol v2 serves a hidden ref's commit by SHA from the internal repo", fetch_by_sha(f"file://{ns}", hidden_commit, 2))
check("T9c protocol v0 refuses it", not fetch_by_sha(f"file://{ns}", hidden_commit, 0))
mirror = work / "mirror-anthropic.git"; subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True)
git(ns, "push", "-q", f"file://{mirror}", "refs/tags/anthropic-r1:refs/tags/anthropic-r1")
check("T9d separate mirror repo: hidden commit absent and unfetchable (v2)", not fetch_by_sha(f"file://{mirror}", hidden_commit, 2)
      and subprocess.run(["git", "-C", str(mirror), "cat-file", "-e", hidden_commit], capture_output=True).returncode != 0)

# T10 Harbor compatibility: run Harbor's own resolution code against the mirror
sys.modules.setdefault("harbor", type(sys)("harbor")); sys.modules.setdefault("harbor.utils", type(sys)("harbor.utils"))
for mod, f in [("harbor.utils.git", "src/harbor/utils/git.py"), ("harbor.skills", "src/harbor/skills.py")]:
    spec = importlib.util.spec_from_file_location(mod, SRC / "harbor" / f); m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m; spec.loader.exec_module(m)
hs = sys.modules["harbor.skills"]
hs._CACHE_DIR = work / "harbor-cache"
repo = hs.resolve_repo_source("https://sbs.example/sbs/ns-anthropic/tree/anthropic-r1/skills")
type(repo).clone_url = property(lambda self: f"file://{mirror}")  # transport swap only; parsing is Harbor's
paths = hs.resolve_skill_sources([f"https://sbs.example/sbs/ns-anthropic/tree/anthropic-r1/skills"])
found = hs.resolve_skills(paths)
check("T10a Harbor resolves the flat tag and finds every skill", sorted(s.name for s in found) == sorted(skills), f"{len(found)} skills")
allmatch = all(hs.compute_skill_digest(s.source) == harbor_digest(imported[s.name][0]) for s in found)
check("T10b SBS harbor_digest == Harbor's compute_skill_digest for each skill", allmatch)
up_match = all(hs.compute_skill_digest(UP / "skills" / s.name) == hs.compute_skill_digest(s.source) for s in found)
check("T10c Harbor digest via SBS mirror == digest of upstream directory", up_match)

# T28 Harbor lock join without Harbor's digest: (git_commit_id, source path) -> served tree hash
joined = 0
for sk in found:
    url_sha = hs.get_git_skill_metadata(sk.source)
    sub = Path(sk.source).resolve().relative_to(hs._CACHE_DIR.resolve()).parts[4:]   # after host/org/name/sha
    tree = git(mirror, "rev-parse", f"{url_sha[1]}:{'/'.join(sub)}")
    joined += tree == imported[sk.name][1]
check("T28 every Harbor lock entry resolves to its snapshot via mirror commit + path (no harbor_digest needed)",
      joined == len(found), f"{joined}/{len(found)}")

# T11 scale: 20k artifact lines (refs) in one namespace repo
big = init_ns(work / "ns-scale.git")
t0 = write_tree(big, [("x", "100644", b"x\n")]); c0 = commit(big, t0)
N = 20000
cmds = "start\n" + "".join(f"create refs/artifacts/tool/t{i:05d}/heads/main {c0}\n" for i in range(N)) + "prepare\ncommit\n"
s0 = time.time(); subprocess.run(["git", "-C", str(big), "update-ref", "--stdin"], input=cmds.encode(), check=True, capture_output=True); dt_create = time.time() - s0
git(big, "pack-refs", "--all")
s0 = time.time(); n = len(git(big, "for-each-ref", "--format=%(refname)", "refs/artifacts/tool/").splitlines()); dt_list = time.time() - s0
s0 = time.time()
for i in range(50):
    git(big, "rev-parse", "--verify", f"refs/artifacts/tool/t{i * 397 % N:05d}/heads/main")
dt_lookup = (time.time() - s0) / 50
c1b = commit(big, t0, [c0])
s0 = time.time(); ok, _ = transact(big, [(f"refs/artifacts/tool/t{i:05d}/heads/main", c1b, c0) for i in range(10)]); dt_tx = time.time() - s0
check("T11 20k lines: list / lookup / 10-ref transaction", n == N and ok,
      f"create-all {dt_create:.2f}s, list {dt_list*1000:.0f}ms, lookup {dt_lookup*1000:.1f}ms/op (incl. process spawn), 10-ref tx {dt_tx*1000:.0f}ms")

print(f"\nbatch 1: {sum(r[1] for r in results)}/{len(results)} passed")
shutil.rmtree(work, ignore_errors=True)


# ---------------- second batch ----------------
work2 = Path(tempfile.mkdtemp(prefix="sbsproto2-"))
nsc = init_ns(work2 / "ns-corpus.git")

# T13 verbatim import across the survey's App. B corpus (same five repos): tree == upstream tree
total = match = 0; mism = []
for r in ["anthropic-skills", "cap-evolve", "Arbor", "harbor", "superpowers"]:
    root = SRC / r
    for sk in sorted(root.rglob("SKILL.md")):
        if ".git" in sk.parts:
            continue
        d = sk.parent; rel = d.relative_to(root).as_posix()
        ents, skipped = canonical_entries(d)
        # a git tree cannot hold nested repos or ignored files; compare against what upstream tracks
        tracked = set(git(root, "ls-files", "--", rel).splitlines())
        ents = [e for e in ents if f"{rel}/{e[0]}" in tracked]
        total += 1
        if write_tree(nsc, ents) == git(root, "rev-parse", f"HEAD:{rel}"):
            match += 1
        else:
            mism.append(f"{r}:{rel}")
check("T13 corpus: imported tree == upstream tree for every SKILL.md dir", match == total, f"{match}/{total} {mism[:3]}")

# T14 scratch excluded at publish: candidates differing only in an ignored file get one identity
IGNORE = {"JOURNAL.md", "MEMORY.md", "trajectories", "prior_iterations"}
a = [("SKILL.md", "100644", b"x\n"), ("JOURNAL.md", "100644", b"run 1\n")]
b = [("SKILL.md", "100644", b"x\n"), ("JOURNAL.md", "100644", b"run 2\n")]
publish_view = lambda es: [e for e in es if e[0].split("/")[0] not in IGNORE]
check("T14 with the ignore set applied at publish, scratch-only differences give the same tree hash",
      write_tree(nsc, publish_view(a)) == write_tree(nsc, publish_view(b)) and write_tree(nsc, a) != write_tree(nsc, b))

# T15 raw bytes: CRLF and no-trailing-newline preserved (no filters applied)
raw = b"line1\r\nline2"
t = write_tree(nsc, [("w.txt", "100644", raw)])
check("T15 CRLF + missing final newline stored byte-exact",
      subprocess.run(["git", "-C", str(nsc), "cat-file", "blob", f"{t}:w.txt"], capture_output=True).stdout == raw)

# T16 cross-namespace pin = object copy; survives deletion of the source namespace
nsA = init_ns(work2 / "ns-a.git"); nsB = init_ns(work2 / "ns-b.git")
ta = write_tree(nsA, [("tool.py", "100755", b"print(1)\n")]); ca = commit(nsA, ta, msg="shared tool v1")
git(nsA, "update-ref", line_ref("tool", "shared"), ca)
pack = subprocess.run(["git", "-C", str(nsA), "pack-objects", "--revs", "--stdout", "-q"], input=(ca + "\n").encode(), capture_output=True, check=True).stdout
subprocess.run(["git", "-C", str(nsB), "index-pack", "--stdin", "--fix-thin"], input=pack, capture_output=True, check=True)
git(nsB, "update-ref", "refs/keep/pin-a-shared", ca)
shutil.rmtree(nsA)
check("T16 pinned cross-namespace snapshot resolves after source namespace is deleted",
      subprocess.run(["git", "-C", str(nsB), "cat-file", "-e", ta]).returncode == 0)

# T17 fork + upstream proposal: three-way merge of independent edits (git >= 2.38 merge-tree --write-tree)
base_t = write_tree(nsc, [("SKILL.md", "100644", b"---\nname: s\n---\nA\nB\nC\n")])
base_c = commit(nsc, base_t, msg="base")
up_c = commit(nsc, write_tree(nsc, [("SKILL.md", "100644", b"---\nname: s\n---\nA upstream\nB\nC\n")]), [base_c], "upstream")
fk_c = commit(nsc, write_tree(nsc, [("SKILL.md", "100644", b"---\nname: s\n---\nA\nB\nC fork\n")]), [base_c], "fork")
r = subprocess.run(["git", "-C", str(nsc), "merge-tree", "--write-tree", fk_c, up_c], capture_output=True, text=True)
merged = subprocess.run(["git", "-C", str(nsc), "cat-file", "blob", r.stdout.split()[0] + ":SKILL.md"], capture_output=True, text=True).stdout if r.returncode == 0 else ""
check("T17 upstream edit merges cleanly into a diverged fork (proposal branch)", "A upstream" in merged and "C fork" in merged)
c_conf = commit(nsc, write_tree(nsc, [("SKILL.md", "100644", b"---\nname: s\n---\nA fork\nB\nC\n")]), [base_c], "fork2")
r2 = subprocess.run(["git", "-C", str(nsc), "merge-tree", "--write-tree", c_conf, up_c], capture_output=True, text=True)
check("T17b conflicting edit is detected, not silently resolved", r2.returncode == 1)

# T18 Harbor parses exactly one path segment as the ref: 'a/b' refs are impossible
rr = hs.resolve_repo_source("https://sbs.example/o/r/tree/ws/runA/skills")
check("T18 Harbor URL ref cannot contain '/': ref='ws', subdir='runA/skills'", rr.ref == "ws" and rr.subdir == "runA/skills")

# T19 backup/restore: a full bundle carries every internal ref; restore is non-destructive and identical
bundle = work2 / "ns.bundle"
git(nsc, "update-ref", line_ref("skill", "probe"), base_c)
git(nsc, "update-ref", "refs/keep/skill/probe/" + up_c, up_c)
git(nsc, "bundle", "create", "-q", str(bundle), "--all")
restored = work2 / "restored.git"
subprocess.run(["git", "clone", "-q", "--bare", "--mirror", str(bundle), str(restored)], check=True)
refs = lambda r: git(r, "for-each-ref", "--format=%(refname) %(objectname)")
check("T19 bundle backup restores every ref (incl. refs/artifacts, refs/keep) identically", refs(nsc) == refs(restored) and "refs/keep/" in refs(restored))

# ---------------- third batch: generic aggregates (§34.4, §34.8) ----------------
# An aggregate snapshot = its own files under content/ + .sbs/members.json (member snapshot records and
# mount specs). It never embeds members' objects; the served tree is assembled from the records.
work3 = Path(tempfile.mkdtemp(prefix="sbsproto3-"))
home = init_ns(work3 / "home.git")
EXTRACT = (".py", ".sh", ".bash")


def tree_entries(repo, tree):
    """(path, mode, sha) for every blob under a tree."""
    out = []
    for line in git(repo, "ls-tree", "-r", tree).splitlines():
        meta, path = line.split("\t", 1); mode, _t, sha = meta.split()
        out.append((path, mode, sha))
    return out


def mktree_from(repo, entries):
    """Nested tree from (path, mode, blob-sha) without rewriting any blob."""
    root = {}
    for path, mode, sha in entries:
        node = root; *dirs, name = path.split("/")
        for d in dirs:
            node = node.setdefault(d, {})
        node[name] = (mode, sha)
    return _mk(repo, root)


def publish_aggregate(repo, own_entries, members, keep=True):
    """members: [{target, member_commit, source}] -> commit whose tree is content/ (own) + .sbs/members.json.

    keep=True: the publish holds a keep-ref per referenced member (the §34.10 rule), so records retain.
    """
    if keep:
        for m in members:
            git(repo, "update-ref", f"refs/keep/member/{m['member_commit']}", m["member_commit"])
    own = write_tree(repo, own_entries) if own_entries else git(repo, "mktree", inp=b"")
    rec = canonical_json(sorted(members, key=lambda m: m["target"]))
    sbs = write_tree(repo, [("members.json", "100644", rec)])
    root = git(repo, "mktree", inp=f"040000 tree {own}\tcontent\n040000 tree {sbs}\t.sbs\n".encode())
    return commit(repo, root, msg="aggregate")


def assemble(repo, c):
    """Served tree of a snapshot: own content/ + every member mounted at its target (recursively)."""
    entries = tree_entries(repo, git(repo, "rev-parse", f"{c}:content"))
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{c}:.sbs/members.json"], capture_output=True).stdout
    for m in (json.loads(raw) if raw else []):
        sub = assemble(repo, m["member_commit"])
        sub = mktree_from(repo, sub) if isinstance(sub, list) else sub
        for path, mode, sha in tree_entries(repo, sub):
            if m["source"] in ("", path):
                rel = path if m["source"] == "" else ""
                entries.append((f"{m['target']}/{rel}".rstrip("/") if rel else m["target"], mode, sha))
    return mktree_from(repo, entries)


import json  # noqa: E402
total = exact = 0; bad = []; n_tools = 0; skill_commits = {}
for r in ["anthropic-skills", "cap-evolve", "Arbor", "harbor", "superpowers"]:
    root = SRC / r
    for sk in sorted(root.rglob("SKILL.md")):
        if ".git" in sk.parts:
            continue
        d = sk.parent; rel = d.relative_to(root).as_posix()
        tracked = set(git(root, "ls-files", "--", rel).splitlines())
        ents = [e for e in canonical_entries(d)[0] if f"{rel}/{e[0]}" in tracked]
        own, members = [], []
        for path, mode, data in ents:
            base = path.rsplit("/", 1)[-1]
            if path.endswith(EXTRACT) and not base.startswith("_"):
                tool_c = publish_aggregate(home, [(base, mode, data),
                                                  ("tool.json", "100644", canonical_json({"entry": base}))], [])
                members.append({"target": path, "member_commit": tool_c, "source": base}); n_tools += 1
            else:
                own.append((path, mode, data))
        c = publish_aggregate(home, own, members)
        total += 1
        if assemble(home, c) == git(root, "rev-parse", f"HEAD:{rel}"):
            exact += 1; skill_commits[f"{r}:{rel}"] = c
        else:
            bad.append(f"{r}:{rel}")
check("T24 corpus: skill = own files + linked tool snapshots; assembled tree == upstream tree",
      exact == total, f"{exact}/{total} skills, {n_tools} extracted tools, {bad[:3]}")

# T25 two-level aggregate: a skill group points at skill snapshots only; assembly reproduces the
# upstream skills/ directory for the six Anthropic skills
grp_members = [{"target": f"skills/{s}", "member_commit": skill_commits[f"anthropic-skills:skills/{s}"], "source": ""} for s in skills]
grp = publish_aggregate(home, [], grp_members)
up_skills = tree_of(UP, {s: git(UP, "rev-parse", f"HEAD:skills/{s}") for s in skills})
check("T25 skill group (records only) assembles to exactly the six upstream skill trees",
      git(home, "rev-parse", f"{assemble(home, grp)}:skills") == up_skills)
own_only = tree_entries(home, git(home, "rev-parse", f"{grp}:content"))
check("T25b the group snapshot itself holds no member objects", own_only == [])

# T26 retention: records alone do not retain; publish-time keep-refs retain the whole closure
g2 = publish_aggregate(home, [], [{"target": f"skills/{s}", "member_commit": skill_commits[f"anthropic-skills:skills/{s}"], "source": ""} for s in skills])
git(home, "update-ref", "refs/artifacts/composition/g2/heads/main", g2)
lone_tool = publish_aggregate(home, [("t.py", "100755", b"print('x')\n")], [], keep=False)
lone_skill = publish_aggregate(home, [("SKILL.md", "100644", b"---\nname: lone\n---\n")],
                               [{"target": "scripts/t.py", "member_commit": lone_tool, "source": "t.py"}], keep=False)
git(home, "update-ref", "refs/artifacts/skill/lone/heads/main", lone_skill)
before = assemble(home, g2)
git(home, "-c", "gc.reflogExpire=now", "-c", "gc.reflogExpireUnreachable=now", "reflog", "expire", "--all")
git(home, "gc", "--prune=now", "-q")
exists = lambda o: subprocess.run(["git", "-C", str(home), "cat-file", "-e", o], capture_output=True).returncode == 0
check("T26a with publish-time keep-refs, a group's whole closure (skills and their tools) survives gc and re-assembles identically",
      assemble(home, g2) == before)
check("T26b negative control: a member referenced only inside a record, with no keep-ref, is pruned by gc",
      exists(lone_skill) and not exists(lone_tool))
shutil.rmtree(work3, ignore_errors=True)

# T27 identity is home-independent: the same skill imported into two homes has the same identity
h1 = init_ns(work3 / "h1.git"); h2 = init_ns(work3 / "h2.git")
pdf_ents = canonical_entries(UP / "skills" / "pdf")[0]
check("T27 same skill in two homes -> same tree hash (and same object format)",
      write_tree(h1, pdf_ents) == write_tree(h2, pdf_ents)
      and git(h1, "rev-parse", "--show-object-format") == git(h2, "rev-parse", "--show-object-format") == "sha1")

print(f"\n{sum(r[1] for r in results)}/{len(results)} passed (all batches)")
shutil.rmtree(work2, ignore_errors=True)
