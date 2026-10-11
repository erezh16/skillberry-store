"""Checks for the mechanical claims in artifact_evolution_store.md §34 (see §34.15).

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

# T2 identity: same content -> same tree, different commit; content_digest deterministic
ents, t = imported["pdf"]
c1 = commit(ns, t, msg="a"); time.sleep(1.1); c2 = commit(ns, t, msg="a")
check("T2 same tree, different commit SHAs", c1 != c2 and git(ns, "rev-parse", c1 + "^{tree}") == git(ns, "rev-parse", c2 + "^{tree}"))
check("T2 content_digest deterministic", content_digest(ents) == content_digest(list(reversed(ents))))

# T3 exec bit is part of identity
modes = {m for _r, m, _d in imported["skill-creator"][0]}
ents2 = [(r, "100644", d) for r, _m, d in imported["skill-creator"][0]]
check("T3 exec bit changes tree hash and content_digest", "100755" in modes and
      write_tree(ns, ents2) != imported["skill-creator"][1] and content_digest(ents2) != content_digest(imported["skill-creator"][0]))

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
    {"role": r, "kind": "skill", "line": f"anthropic/{r}", "content_digest": content_digest(imported[r][0]), "tree": t}
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

# T14 capability_digest: declared ignore-set makes scratch-only differences invisible
a = [("SKILL.md", "100644", b"x\n"), ("JOURNAL.md", "100644", b"run 1\n")]
b = [("SKILL.md", "100644", b"x\n"), ("JOURNAL.md", "100644", b"run 2\n")]
cap = lambda es, ign: content_digest([e for e in es if e[0].split("/")[0] not in ign])
check("T14 capability_digest equal under ignore={JOURNAL.md}, content_digest differs",
      cap(a, {"JOURNAL.md"}) == cap(b, {"JOURNAL.md"}) and content_digest(a) != content_digest(b))

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

print(f"\n{sum(r[1] for r in results)}/{len(results)} passed (all batches)")
shutil.rmtree(work2, ignore_errors=True)
