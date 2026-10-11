"""Checks for §34.19: can §34's mutable state live in git + plain files, without a database?

Usage: python3 test_nodb.py   (stdlib + git only; no corpus needed)
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gitstore import ENV, canonical_json, commit, git, init_ns, write_tree  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


META = "refs/meta/skill/pdf"


def add_label(args):
    """Additive label write on a line's metadata ref, compare-and-set with retry."""
    repo, label, snap = args
    tries = 0
    while True:
        tries += 1
        old = subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", META], capture_output=True, text=True).stdout.strip()
        labels = {}
        if old:
            raw = subprocess.run(["git", "-C", repo, "cat-file", "blob", f"{old}:labels/{snap}.json"], capture_output=True).stdout
            labels = json.loads(raw) if raw else {}
        labels[label] = {"by": label.split(":")[0]}
        blob = subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"], input=canonical_json(labels), capture_output=True).stdout.decode().strip()
        # rebuild labels/ tree from the old tree plus this one file
        entries = {}
        if old:
            for line in subprocess.run(["git", "-C", repo, "ls-tree", f"{old}:labels"], capture_output=True, text=True).stdout.splitlines():
                meta, name = line.split("\t"); entries[name] = meta
        entries[f"{snap}.json"] = f"100644 blob {blob}"
        sub = subprocess.run(["git", "-C", repo, "mktree"], input="".join(f"{m}\t{n}\n" for n, m in entries.items()), capture_output=True, text=True).stdout.strip()
        root = subprocess.run(["git", "-C", repo, "mktree"], input=f"040000 tree {sub}\tlabels\n", capture_output=True, text=True).stdout.strip()
        c = subprocess.run(["git", "-C", repo, "commit-tree", root, *(["-p", old] if old else []), "-m", label], capture_output=True, text=True, env=ENV).stdout.strip()
        r = subprocess.run(["git", "-C", repo, "update-ref", META, c, old or "0" * 40], capture_output=True)
        if r.returncode == 0:
            return tries
        time.sleep(random.random() * 0.01)


work = Path(tempfile.mkdtemp(prefix="sbsnodb-"))
ns = init_ns(work / "ns.git")

# T20 labels in git: 16 concurrent additive writers on one line's metadata ref lose nothing
labels = [f"plugin{i}:checked" for i in range(16)]
with ProcessPoolExecutor(8) as ex:
    tries = list(ex.map(add_label, [(str(ns), l, "sha256-aa") for l in labels]))
final = json.loads(git(ns, "cat-file", "blob", f"{META}:labels/sha256-aa.json"))
hist = len(git(ns, "rev-list", META).splitlines())
check("T20 16 concurrent label writers (CAS + retry) on one metadata ref: no lost update",
      set(final) == set(labels) and hist == 16, f"retries={sum(tries) - len(tries)}, history commits={hist}")

# T21 throughput of label writes through the git CLI (process spawn dominated)
n = 200
s = time.perf_counter()
for i in range(n):
    add_label((str(ns), f"bench:{i}", f"sha256-{i % 20:02d}"))
dt = (time.perf_counter() - s) / n
check("T21 sequential label writes via git CLI", dt < 0.25, f"{dt * 1000:.1f} ms/write (~{1 / dt:.0f}/s); SBS today: 2-3 writes/s (survey M15)")

# T22 evidence as append-only files: content-addressed 'seen' lookup needs no index
ev = work / "evidence"
def key(cap, task, ds, split, ctx):
    return hashlib.sha256(json.dumps([cap, task, ds, split, ctx]).encode()).hexdigest()
N = 20000
s = time.perf_counter()
for i in range(N):
    k = key(f"cap:{i % 50}", f"task{i}", "tau2@3", "val", "m1")
    p = ev / "seen" / k[:2] / f"{k}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_bytes(b'{"reward":0.5}'); os.replace(tmp, p)
    sp = ev / "by-subject" / f"cap{i % 50}"; sp.mkdir(parents=True, exist_ok=True)
    (sp / f"{k}.json").write_bytes(json.dumps({"task": f"task{i}", "reward": (i % 7) / 7}).encode())
wdt = (time.perf_counter() - s) / N
s = time.perf_counter()
hits = sum((ev / "seen" / k[:2] / f"{k}.json").exists() for k in (key(f"cap:{i % 50}", f"task{i}", "tau2@3", "val", "m1") for i in range(0, N, 10)))
ldt = (time.perf_counter() - s) / (N // 10)
s = time.perf_counter()
vals = [json.loads(f.read_bytes())["reward"] for f in (ev / "by-subject" / "cap7").iterdir()]
adt = time.perf_counter() - s
check("T22a seen-cache lookup by content-addressed path (no index)", hits == N // 10, f"{ldt * 1e6:.0f} us/lookup")
check("T22b agg(subject) by scanning one subject's directory", len(vals) == N // 50, f"{len(vals)} records in {adt * 1000:.1f} ms; write {wdt * 1000:.2f} ms/record")

# T22c a query across all subjects (frontier over a dataset) must scan everything without an index
s = time.perf_counter()
allrec = sum(1 for d in (ev / "by-subject").iterdir() for f in d.iterdir() if json.loads(f.read_bytes())["reward"] > 0.8)
sdt = time.perf_counter() - s
check("T22c cross-subject query = full scan without a derived index", allrec > 0, f"{N} records scanned in {sdt * 1000:.0f} ms")

# T23 budget counter: fcntl-locked increment, 8 processes x 250 increments, single host
cnt = work / "budget"; cnt.write_text("0")
def bump(_):
    for _ in range(250):
        with open(cnt, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX); v = int(f.read() or 0); f.seek(0); f.write(str(v + 1)); f.truncate(); fcntl.flock(f, fcntl.LOCK_UN)
with ProcessPoolExecutor(8) as ex:
    list(ex.map(bump, range(8)))
check("T23 flock-protected budget counter: 2000 concurrent increments, none lost (one host)", cnt.read_text() == "2000", cnt.read_text())

print(f"\n{sum(results)}/{len(results)} passed")
shutil.rmtree(work, ignore_errors=True)
