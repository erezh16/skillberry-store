# Skillberry Store as the Artifact Plane for Agent Optimization and Evaluation

**Status:** design study for review. Not an implementation plan.
**Date:** 2026-09-27
**Question it answers:** what must change in skillberry-store (SBS) for it to be the best artifact
persistence layer for the eight state-of-the-art agent optimization and evaluation systems — and
what measurable value that would deliver to each.

| Section | For |
|---|---|
| **§1 Vision · §2 Why now · §3 Value per project · §4 Priorities** | the decision |
| **§5 How the eight systems persist artifacts · §6 SBS today · §7 Gaps** | the technical case |
| **§8 Design · §9 Integrations · §10 Two strategic bets · §11 Risks** | the work |
| **Appendix A/B** | measurement method and reproduction |

Claims are labelled **[measured]** (reproduced on a running SBS during this study — Appendix A/B),
**[read]** (from the named source), or **[judgement]** (my opinion — argue with these).

---

# Part I — The case

## 1. Vision and goal

### 1.1 The goal

> **Make SBS the place an agent artifact lives once it has to outlive the run that produced it.**

Not a file store, and not another experiment tracker. An **artifact plane**: immutable,
content-addressed capability artifacts — skills, tools, prompts, tool surfaces — plus the evidence
bound to them: which version, measured how, on which tasks and split, under which model and harness,
at what cost, descended from what, and what has already been ruled out.

### 1.2 The one question that defines the product

All eight systems studied can answer:

> *"How did my artifact do, in my run?"*

Seven answer it well, with **git and a directory** — offline, byte-exact, free, already in CI. A
store that competes on that question loses, and should not try.

None can answer:

> *"Has anyone, anywhere, ever measured **this exact artifact** — and does it help **my** agent, on
> **my** harness, at what cost?"*

That is unanswerable today for one structural reason: **an artifact has no identity that survives
leaving the run that produced it.** Everything in this document follows from fixing that.

### 1.3 The scope of the claim: value tracks boundaries crossed

| Boundaries an artifact crosses | Best tool |
|---|---|
| one run, one machine, one agent, one person | **git + a directory.** Genuinely — do not deploy a service |
| more than one **run** | the store — cross-run caching, recurrence, lineage |
| more than one **agent / model / harness** | the store — transfer is measurable, and sometimes **negative** |
| more than one **person or team** | the store — safe sharing, authz, provenance, receipts |
| more than one **optimizer** | the store — the only plausible meeting point (§10.2) |

**[judgement]** SBS earns its place exactly when an artifact outlives its run. Below that threshold
it is overhead; above it, nothing in this landscape is even trying.

### 1.4 Why this is the moment

All eight projects are actively crossing those boundaries, and each is straining against the
single-run assumption its storage was built on **[read]**:

- **GEPA** replaced monotonic iteration counters with random ids specifically so its state survives
  *"agent-swarm engines that propose candidates without a shared clock."*
- **CORAL** added multi-island runs with migration between islands.
- **SkillOpt** ships seven host integrations, and its headline result is 52
  `(model, benchmark, harness)` cells.
- **gskill**'s entire result is **cross-agent transfer**: skills learned on a small open model
  improve Claude Code.
- **Arbor** documents its own limitation: *"Experience lives per session, not in a global library."*

### 1.5 The vision, made demonstrable: eight zeros

For each project there is a capability whose value today is **provably zero** — not small, not
unmeasured, but zero by construction, because the mechanism that would produce it does not exist.
When a baseline is zero, **one successful demonstration is the proof** — no paired runs, no
significance test.

| Project | Capability | Today | Zero because |
|---|---|:-:|---|
| **cap-evolve** | candidates evaluable in parallel against one store | **1** | names are global per type; re-import duplicates every tool **[measured]** |
| **Harbor** | skills sourced from SBS | **0** | Harbor needs an immutable ref **and** a content digest; SBS has neither |
| **Arbor** | findings reused across *projects*, recurrence-ranked | **0** | experience is per-session by design |
| **GEPA** | eval-cache hits across *runs* | **0** | the cache lives inside per-run `GEPAState` |
| **gskill** | repo skills published with a per-agent transfer matrix | **0** | output is `best_skills.txt` in a run dir |
| **SkillOpt** | Sleep proposals shared across machines | **0** | staging is local to one machine |
| **CORAL** | knowledge surviving a run boundary | **0** | `.coral/` is created by `coral start`, scoped to that run |
| **AEH** | measurements attributable to a **judge version** | **0%** | judges are ordinary unversioned files |

**Seven of eight have a provable-zero baseline.** That is what makes this vision demonstrable rather
than plausible — and it matters because §6 shows this document's whole spine is *not* producing
numbers that look right but aren't.

---

## 2. Why now: the evidence in brief

### 2.1 Eight systems independently built the same primitives

Full detail in §5. Every primitive below was implemented independently, with no shared lineage
**[read]**:

| Primitive | cap-evolve | Arbor | Harbor | GEPA | gskill | SkillOpt | CORAL | AEH | n |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **Measurement bound to an artifact version** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **8** |
| **Split discipline (val vs sealed test)** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **8** |
| Immutable versioned snapshots | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | 7 |
| Sealed / protected eval surface | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✓✓ | ✓ | 7 |
| Cost / duration as co-metrics | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – | ✓ | 7 |
| Content digest over an artifact | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✓ | – | 6 |
| Missing data ≠ 0 (status, not score) | ✓ | ✓ | – | ✓ | – | ✓ | ✓ | ✓ | 6 |
| Lineage with multi-parent merge | ✓ | ✓ | – | ✓ | ✓ | – | ✓ | – | 5 |
| Stored negative knowledge | ✓ | ✓ | – | ✓ | – | ✓ | ✓ | – | 5 |
| Cross-agent / harness transfer measured | – | – | – | – | ✓ | ✓ | – | – | 2 |
| Pareto / multi-objective archive | – | – | – | ✓ | – | ✓ | – | – | 2 |
| Eval cache keyed by candidate digest | ✓ | – | – | ✓ | – | – | – | – | 2 |
| **Default persistence** | git | git | git→registry | pickle+JSON | files | files | dirs+worktrees | **MLflow** | — |

Two observations carry the argument.

**The two unanimous primitives are the two SBS lacks entirely.** A measurement must be bound to an
artifact version; the test split must be sealed. Eight of eight concluded they could not work
without these.

**Nobody has an artifact registry.** Seven persist to files or git; AEH uses MLflow, which is
run-centric. Harbor has a real registry — for **tasks and datasets**, explicitly not for the
agent-side artifacts being optimized, and explicitly *"not a development platform."* The hole is
real across eight systems, and the two most sophisticated (GEPA's pickle, CORAL's symlinked
directory) strain hardest against it.

### 2.2 SBS has the right shape and the wrong contracts

Measured on a running instance at `main` (`c6e72ce`), and on **81 real published skills from five
independent projects** (759 files; Appendix B) **[measured]**:

| Finding | Value |
|---|---|
| Round-trip **bundle digest** fidelity (bytes + modes) | **0 / 12** — never achieved, even for a single-file skill |
| Skills whose declared licence is replaced by a fabricated `Proprietary` claim | **17 / 81 (21%)** — verified live on Anthropic's `algorithmic-art`, whose `LICENSE.txt` **is Apache-2.0** |
| Skills losing an executable bit on a script they invoke | **15 / 81 (18.5%)**, 38 files |
| Skills silently losing a binary payload (`ignored_files: []`) | **3 / 81**, 56 files (54 fonts; a component tarball) |
| Skills losing frontmatter keys | **36 / 81 (44%)**; 7 key families destroyed |
| Concurrent read-modify-write | **lost update reproduced on the first attempt**, both writers got `200` |
| One concurrent writer's effect on a paged read | **7.1 ms → 699.5 ms (99×)** |
| `DELETE …?delete_tools=true` | **`200`** with `deleted_tools: []`, 18 objects orphaned |
| Retrieval of a query quoting an artifact's own body verbatim | distance **1.713** vs **1.898** for an unrelated query — no signal |

### 2.3 The one architectural decision everything follows from

> **Make the immutable, content-addressed *bundle* the source of truth, and make tools, snippets,
> params and frontmatter *derived projections* over it.**

Today it is the reverse: objects are truth and the file tree is reconstructed from `file:<path>`
tags, with `SKILL.md` frontmatter regenerated from `name` + `description`. **Every** fidelity,
digest, versioning, caching and interop defect above follows from that single choice — and so does
the fact that SBS cannot be a Harbor skill source at all.

---

## 3. What each project gains

The justification, per project. Each number carries its basis: **[measured]** this study,
**[definitional]** zero by construction, **[countable]** trivially counted once built,
**[to-instrument]** no baseline yet — say so rather than guess.

### 3.1 cap-evolve — the live consumer, and the largest measured saving

**Today.** cap-evolve already uses SBS (via the SPA intervention). Because two candidates cannot
coexist, the adapter **wipes the whole store per candidate**: `reset_store_to_skill()` is 5–7 calls
with two retry loops, a hardcoded list of dependent object kinds *derived by reading SBS's source at
tag 0.2.1*, and a client-side `Protection` class emulating immutability. An interrupted run once
left vMCP servers behind and failed **every rollout of a 50-task × 10-trial arm (coverage 0/50)**
**[read]**.

**Gains.** One atomic workspace `:apply`; parallel candidates; a cross-run eval cache; broke/fixed/
protect-set as a service; `memory_skill: sbs`; and — first — a **zero-code** integration through the
`store: command` backend it already ships for exactly this purpose.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Workaround LOC in `spa_env.py` | ~250–300 | 0 | [countable] |
| Objects orphaned by one cascade delete | **18** | 0 | [measured] |
| Candidates evaluable in parallel | **1** | K | [definitional] |
| Store calls per candidate deploy | 5–7 + 2 retry loops | **1** | [countable] |
| cap-evolve LOC changed for record-keeping | — | **0** | [countable] |
| Rollouts avoided by cache hits | — | instrument | [to-instrument] — a 20-iteration × 50-task × 10-trial run is 10,000 rollouts; report *rollouts avoided × cost*, not a guessed rate |

### 3.2 Harbor — the cleanest binary demonstration available

**Today.** Harbor resolves a skill source to an immutable directory and records
`{name, source, digest, git_url, git_commit_id}` in the job lock *"so that job results are fully
traceable back to the exact skill content that was used, even if the branch has since moved."* SBS
satisfies neither requirement **[read]**.

**Gains.** A skill source needing **zero Harbor code change** (§8.6); digest agreement so the job
lock resolves back to an SBS ref; job import closing the loop; an evidence panel per skill version
across every job that used it — which Hub cannot give, being keyed by job rather than by artifact.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Skills runnable from SBS | **0** | works | [definitional] |
| Harbor code changed | — | **0** | [countable] |
| `SBS content_digest == Harbor's computed digest` | n/a | exact match | [countable] — byte comparison, pass/fail |
| Jobs joinable to a capability version | **0** | N | [definitional] |

**Demo:** `harbor run --skill https://sbs/git/<ns>/tree/<ref>/<skill> -a claude-code -m …`, then show
the job lock's digest resolving back to an SBS ref. **Today that command cannot be written.**

### 3.3 gskill — the biggest new product, not just an integration

**Today.** gskill turns any GitHub repo into a learned skill: Jinja 55%→**82%**, Bleve 24%→**93%**.
Transferred to Claude Code: Bleve Haiku 79.3%→**98.3%** with duration **173s→142s**. The output is
`best_skills.txt` in a run directory; transfer to each new agent is re-measured by hand; nothing is
catalogued **[read]**. So the question users actually have — *"is there a learned skill for my repo,
and does it help my agent?"* — has nowhere to be asked.

**Gains.** A catalogue of repo-scoped learned skills, each with a measured per-`(agent, model,
harness)` transfer matrix including duration and including **negative** entries — Jinja/Sonnet went
100%→**98.5%**, and a store that hid that would be lying.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Repos with a published, addressable learned skill | **0** | N | [definitional] |
| Skills carrying a per-agent transfer matrix | **0** | N | [definitional] |
| With/without pair stored as a first-class counterfactual | **0** | N | [definitional] — gskill already runs both arms; the pairing ends up in a chart, not a record |

**[judgement]** The most legible artefact of this design for an outside audience, and it needs no new
science.

### 3.4 GEPA — a provable zero, and an escape from pickle

**Today.** `EvaluationCache` is keyed `(CandidateHash, split, DataId)` — the right key — but lives
inside per-run `GEPAState`, so **cross-run hit rate is 0 by construction**. Resume state is a
**pickle**: unqueryable, unshareable, unsafe to load from an untrusted source **[read]**.

**Gains.** A shared digest-keyed cache spanning runs, users and frameworks; typed queryable state;
Pareto-frontier queries as a service. GEPA already computes the digest SBS needs (`sha256` over the
sorted candidate dict) — only the cache's *scope* changes.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Cross-run eval-cache hits | **0** | >0 | [definitional] |
| Resume state queryable / shareable / safe to load untrusted | no/no/no | yes/yes/yes | [countable] |
| Frontier queryable outside the producing run | **0** | yes | [definitional] |

**Inherits GEPA's own warning:** `split` must be a *mandatory* cache key. GEPA drops split-less
legacy entries rather than serving them, because they cannot be distinguished from contaminated
ones. A shared cross-team cache turns that from a local bug into cross-team corruption.

### 3.5 Arbor — removes a limitation Arbor documents about itself

**Today.** `distill_abstract` mines findings from a finished run with an LLM and is **off by
default** because it *"spends extra LLM calls."* Experience then *"lives per session, not in a
global library,"* so `[xN]` recurrence can only count within one project's own history **[read]**.

**Gains.** Mining moved off the run budget; a findings library scoped by project/dataset — keeping
the isolation Arbor rightly wants — but with **cross-project** recurrence ranking; `code_ref`
branches published as versions; contamination metadata as queryable fields.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| In-run LLM calls spent on distillation | N (or 0, feature off) | **0** | [countable] |
| Findings reusable across projects | **0** | K | [definitional] |
| Refuted directions a later run avoids | — | count | [to-instrument] — a *count*, robust at small n |

**Honest limit.** Arbor already reuses experience *within* a project. The marginal gain is
cross-project scope, recurrence ranking and budget relief — not reuse itself.

### 3.6 SkillOpt — a paper table becomes a live query

**Today.** The 52 `(model, benchmark, harness)` cells exist as a **paper table** (+23.5 points direct
chat, +24.8 in Codex, +19.1 in Claude Code on GPT-5.5). Reference artifacts are
`ckpt/<benchmark>/<model>_skill.md` in git. SkillOpt-Sleep stages proposals **locally**, per machine
**[read]**.

**Gains.** The transfer matrix as a live query; artifacts addressed by `(benchmark, model)` with
digests; Sleep proposals as *staged* store proposals shareable across machines and teams, with
evidence-retention enforced centrally rather than by a README warning.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| `(model, benchmark, harness)` cells queryable | **0** (a PDF table) | 52 | [definitional] |
| Sleep proposals shareable across machines | **0** | N | [definitional] |

**Honest limit.** For the *paper* checkpoints, git is already adequate. The gain is the deployment
case — Sleep running per-user, per-machine, needing to share.

### 3.7 CORAL — knowledge that survives the run

**Today.** `.coral/` is created by `coral start`: one directory, one machine, one run. Islands scope
attempts, notes and skills *within* a run, with migration between them. CORAL's ablation attributes
its **3–10× improvement rate** to *knowledge reuse* — and that reuse stops at the run boundary
**[read]**.

**Gains.** Islands as durable store workspaces with the migration policy preserved; attempts as
measurements (its record already carries commit hash, producer, parent, status, feedback); notes as
findings with cross-run recurrence; agents distributed across machines.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Knowledge surviving a run boundary | **0** | K | [definitional] |
| Machines in one shared memory | **1** | N | [definitional] |

**Honest limit.** CORAL measured reuse *within* a run. That cross-run reuse helps similarly is the
plausible **hypothesis**, not a result.

### 3.8 AgentEvalHarness — version the thing that defines every reward

**Today.** AEH's LLM judges become the reward function inside Harbor trial containers, which NeMo Gym
feeds to GRPO weight updates. Those judges are ordinary files: no version, no digest, no identity. So
**no measurement anywhere is attributable to a judge version**, and nothing identifies what needs
regrading when a judge changes **[read]**.

**Gains.** Judges as versioned, digested, measured artifacts; regrade-staleness labels; skills-under-
test with identity; measurements joined to MLflow runs via `producer.run_ref`.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Measurements attributable to a judge version | **0%** | 100% | [definitional] |
| Regrade candidates identified when a judge changes | **0** | all affected | [definitional] |

**Honest limit.** AEH already has MLflow, so the *measurement* pitch is weakest here. The value is
artifact identity and judge versioning — and SBS must federate with MLflow, never replace it.

### 3.9 Where SBS would *not* be the best answer

| Case | Better tool | Why |
|---|---|---|
| One run, one machine, one person | **git + a directory** | Simpler, offline, byte-exact, zero ops. Seven of eight chose it for good reasons |
| Tasks, datasets, benchmark distribution | **Harbor Hub** | Already does digests, revisions, tags, visibility, leaderboards |
| Run tracking: params, metrics, traces | **MLflow / W&B** | Deployed and mature. SBS federates via `producer.run_ref` |
| Executing evaluations, sandboxes, rollouts | **Harbor / AEH / GEPA `EvalServer`** | SBS is the ledger of record, never a competing eval service |
| RL weight training | **NeMo Gym / SkyRL / TRL** | Out of scope |

---

## 4. Priorities: what to fix first, how, and why

Three rules shaped the order — disagree with the rule rather than the ranking if you disagree:
**defects before features**; **order by what else it unblocks, not by visibility**; **every tier
produces a falsifiable result against an already-measured baseline.**

Sizing is **[judgement]**, relative, not an estimate: **S** ≈ days, **M** ≈ weeks, **L** ≈ a quarter+.

| # | What | How | Why — intended value | Success measure | Size |
|:-:|---|---|---|---|:-:|
| **T0** | **Five wrong-output defects** | §4.1 | Stop emitting incorrect data. Two are correctness/legal; one drops payloads silently; one discards all metadata; one silently loses concurrent writes | licence fidelity **79%→100%**; dropped binaries **3 skills/56 files→0**; cascade success-with-nothing-done **yes→no**; lost-update race **reproducible→`412`** | **S** |
| **T1** | **Artifact identity** — bundles, blobs, dual digests, immutable versions, Harbor refs | §8.1–8.3 | **The keystone.** Nothing attaches to an artifact until it has a stable identity. Unblocks Harbor, the eval cache, measurement subjects, enrichment attribution, cross-optimizer duplicate detection | bundle-digest fidelity **0/12 → 12/12** (App. B corpus); Harbor runs a skill from SBS; cap-evolve integrates at **0 LOC changed** | **L** |
| **T2** | **Concurrency safety** — ETag/If-Match, PATCH, atomic publish, async indexing | §8.4 | Makes the store **safely shareable**. Today two writers silently lose each other's work and one writer stalls every reader | lost update → **`412`**; read-under-write **99× → ≤2×**; partial import reported as success **yes→no** | **M** |
| **T3** | **Isolation** — namespaces, workspaces, declarative `:apply` | §8.5 | **Biggest win for the live consumer.** Ends store-reset-per-candidate; enables parallel evaluation | **~250–300 LOC deleted** from `spa_env.py`; orphans **18→0**; parallel candidates **1→K** | **M** |
| **T4** | **Evidence** — measurements + trajectories (ATIF) | §8.7 | Catalogue → artifact **plane**. Delivers the eval cache (the only direct dollar saving here), broke/fixed/protect-set, and the transfer matrix | reproduce cap-evolve's published τ²/SkillsBench numbers **from store queries alone**; report **rollouts avoided × cost**; publish a transfer matrix | **L** |
| **T5** | **Knowledge** — findings, recurrence ranking, staged proposals | §8.8 | What CORAL credits its gains to; answers Arbor's stated limitation; the **reachable** form of cross-improvement | count of refuted directions a second optimizer avoids | **M** |
| **T6** | **Scale and ops** — externalized indexes, multi-worker, dedup, tiering, incremental backup | §8.9 | A **gate, not a growth story**: enrichment and multi-optimizer work are write-heavy, and a backfill today would be an outage | read p50/p90 at 10⁵ artifacts, 16 workers; dedup bytes on a 20-iteration run | **L** |
| **T7** | **Comparability + governance** — dataset/split and judge kinds, Pareto frontier, budget ledger, per-object authz, seals | §8.10 | Makes cross-optimizer composition **defensible**, and multi-tenant sharing safe | adversarial honesty suite 100% blocked; ACL conformance incl. search and facets | **L** |

**If you do only one thing: T0 + T1.** T0 because the store currently misstates licences and drops
files without saying so. T1 because every other proposal here is unreachable without it.

### 4.1 T0 — the five defects, with a recommendation each

Independent, small, needing none of the architecture. Each is a measured finding.

| Defect | Evidence | Recommendation |
|---|---|---|
| **Licence fabrication** | `exporter.generate_skill_md` emits `license: Proprietary. LICENSE.txt has complete terms` whenever any path contains "license". Verified live on `algorithmic-art`, which declares `license: Complete terms in LICENSE.txt` over an **Apache-2.0** `LICENSE.txt`. 17/81 of the corpus | **Never synthesize a licence.** Preserve the declared value verbatim; emit none if none is declared. Delete the filename heuristic |
| **Silent file loss** | `read_from_folder` opens every file as UTF-8 and drops what fails to decode; `ignored_files` reported `[]` while 56 files vanished | **Report every skipped file** with its reason. Interim fix ahead of T1: read bytes and store undecodable files as opaque blobs |
| **Inert delete cascade** | `?delete_tools=true` returns **200** with `deleted_tools: []`, orphaning 18 objects, because the skill is still its own tools' dependent and `ObjectInUseError` is caught as a warning | **Correct-or-`409`.** Delete in dependency order (the store can compute it); if something genuinely blocks, `409` naming it. Never succeed having done nothing |
| **Destructive frontmatter parse** | `parse_skill_metadata` returns `None` on any YAML error, discarding **all** metadata. Agents write malformed YAML | **Lenient, non-destructive parse** — CORAL's flat `key: value` fallback — and keep the raw block verbatim |
| **Plugin label race** | `StoreAPI.update_*_tags` does read-modify-write while `emit_event` runs handlers **concurrently**; `security` and `evaluator` both subscribe today | **Atomic additive merge** server-side instead of client-side RMW. Producer-namespaced labels are then conflict-free by construction |

**[judgement]** Treat the licence item as urgent independently of this document: the store is making a
false proprietary-licence assertion about third-party content on a fifth of a real corpus, including
Apache-2.0 material published by Anthropic. It is a one-line heuristic to delete.

### 4.2 Why not the obvious alternatives

| Tempting first move | Why not |
|---|---|
| **Retrieval** — visible, demo-friendly, embarrassing baseline | Attaches to nothing stable and improves no decision yet. Body embeddings over artifacts whose bytes change under `PUT` produce an index that silently describes content that is gone. Do it at T5–T6 |
| **Measurements** — the strategic differentiator | A measurement needs a stable subject. Recorded against today's mutable objects it is invalidated by any later `PUT`, undetectably. T1 first |
| **The enrichment pipeline** — ~70% built, looks cheap | Enrichment writes are writes, and one writer stalls readers 99×; a backfill would be an outage. Needs T0's merge fix, then T1–T2 |
| **The cross-optimizer demo** — the most differentiated idea | Needs T1–T4 plus a comparability agreement (T7). The reachable subset is the §10.2 knowledge plane at T5 |
| **LLM-judgement enrichment** (quality/security scores) | Weakest demonstrable value, highest honesty hazard (§10.1). Fix the fact/judgement distinction before promoting it at all |
| **Scale** | Nothing is worth scaling until it is correct and identifiable. T6 gates *promoting* T5/§10, it is not a growth project |

---

# Part II — The technical case

## 5. How the eight systems persist agent artifacts today

All **[read]**. Commits: cap-evolve `c2a4aa6`, Arbor `7cdaf1f`, Harbor `15da91c`, GEPA `d771eb2`,
SkillOpt `79124b3`, CORAL `0123dfb`, AEH `55357f7`.

### 5.1 cap-evolve — candidate dirs, git, and a JSONL event spine

Optimizes system prompts, **tool code**, MCP tool surfaces and whole skill packages against the
user's own eval, with a val-only significance gate and a sealed test split. **Default persistence is
git** — commit after every iteration, accepted or rejected, so the process is `git log`-browsable.
Its `store.py` also ships a `command` backend whose docstring names the intended use: *"to push a
skill to a skills store."* An empty socket waiting for exactly this.

```
.capevolve/run_<ts>/
  state.json  splits.json          # atomic write + file lock: carry the seal and budget
  graph.jsonl                      # candidate DAG: parents[] (2+ = merge), cluster_ids,
                                   #   edit_kind, micro_tests, subset, status, gate row
  rejected.jsonl history.jsonl     # negative + positive memory
  candidates/<id>/  rollouts/  events.jsonl  screens/  wiki/
  LEDGER.md JOURNAL.md PROCESS.md RUNMAP.md prior_iterations/
```

Four mechanisms worth importing:

- **`cache.hash_candidate_dir()`** — SHA-256 over sorted relative paths + content, *excluding* a
  declared ignore-set (`MEMORY.md`, `JOURNAL.md`, `.git`, `trajectories/`, …). Two byte-identical
  candidates share cache entries under different ids. **A digest with a declared scope** — a store
  that hashes everything produces digests cap-evolve cannot use.
- **`integrity.py`** — content-hash manifest of protected paths, verified before the gate decides,
  with precise semantics: *"Tamper means the score is not data … folding a 0.0 into the mean would
  poison both the split mean and the paired gate."* Host-independent by design, because a Claude Code
  hook *"does nothing when the optimizer is Codex, Gemini, a bare shell."*
- **`memory_skill: wiki`** — a weakness graph: nodes with `status`, `affected_tasks`,
  `related[] {slug, why}` (typed, annotated edges), solution cards with diffs, and an append-only
  **Rejected Store Memory**. Plus a **freeze rule** — `affected_tasks` may only grow in the discovery
  iteration *"because solutions are scored against that exact task set"* — i.e. field-level
  immutability enforced by a paragraph addressed to an LLM.
- **`footprint.py`** — which val tasks an edit could causally reach, so out-of-footprint tasks are
  Δ=0 *by construction* rather than noise. Notably it **abstains** (returns `None`) rather than
  manufacture confidence; on the measured run 5 of 7 edits had no available restriction.

**What consuming SBS costs it today** — `spa_env.py` is 1473 lines, of which ~250–300 exist purely as
workarounds. Each comment names the cause: the cascade is unusable (it runs while the skill is still
its own tools' dependent, and the error is swallowed); deletes need retry-until-no-progress because
there is no topological order; the dependent-kind list was derived by *reading SBS's source*;
`Protection` emulates immutability client-side; tags need GET→mutate→PUT because there is no PATCH;
`_list()` handles two response shapes; and **"SPA serves exactly ONE skill,"** so every candidate
deploy resets the entire store.

### 5.2 Arbor — one JSON tree, git branches, per-session experience

Durable state is the **Idea Tree**: one JSON file (canonical) plus generated Markdown. A node carries
`hypothesis`, `status` (pending/running/done/needs_retry/merged/pruned), `insight` (direct *and*
back-propagated), `score` + **`score_split`** (dev|test), `test_score` (set only at merge),
**`code_ref`** (a git branch), `related_work`, `grounding` (citations), **`eval_status`**
(scored|skipped|failed_to_run), `stop_reason`, `attempt`.

Three things stand out. **The split travels with the number** (`score_split`), and `eval_status`
distinguishes *scored* from *skipped* from *failed_to_run*. **Artifacts are git branches** in isolated
worktrees, and merge re-runs `eval_cmd_test` on the source branch in a fresh worktree — the store
never trusts a branch's self-reported score. **Retrieval leads with negative knowledge**:
`get_constraints_block()` emits TREE SHAPE → ROOT INSIGHT → *"PRUNED LESSONS (N — these directions
FAILED. Do NOT re-propose any idea that shares the same hidden assumption…)"* → VALIDATED FINDINGS.

`protected_paths` are hash-verified at runtime; on mismatch the dev score is **discarded** and the
branch becomes unmergeable. Contamination is declared per benchmark (`release_date`, `is_public`,
`canaries[]`) and assessed at INIT. Self-evolution writes typed findings — *leverage* and *pitfall* —
into `findings.jsonl` and `EXPERIENCE.md`, reused at intake with `[xN]` recurrence ranking — but
*"experience lives per session, not in a global library."*

### 5.3 Harbor — the registry half, built, and aimed elsewhere

Task package → isolated environment → agent → verifier → result artifacts, fanned out across
tasks × agents × models × attempts. **Harbor already is a versioned artifact registry — for tasks and
datasets.** The task is the atomic unit; a dataset is a collection of tasks *at specific versions*;
*"every published task or dataset is versioned by its digest, a revision number, and optional tags."*
Refs resolve as `org/task@tag`, `@revision`, `@sha256:<hash>`. Hub stores datasets, tasks, jobs,
trials and trajectories. Scoping is explicit: *"not a development platform"* — publish from VCS,
*"similar to how Docker or PyPI work."*

**Skills are a job input with lockfile provenance** — the integration point that matters most. Per
trial Harbor resolves skill sources, uploads each to `/harbor/skills/<name>`, and hands the directory
to the agent integration. Sources are local paths, `<org/repo>[@ref]`, or
`https://…/tree/<ref>/<path>`; git skills cache under `~/.cache/harbor/skills/<host>/<org>/<name>/<sha>/`.
The job lock records `name`, `source`, **`digest`** (SHA-256 of all files), `git_url`,
`git_commit_id`. **This is the contract SBS must satisfy to be a skill source, and it satisfies
neither half.**

Three more mechanisms to import: **leaderboards** separate the artifact from the claim about it
(rows of `{metadata, metrics, status, trial_ids[]}`, with *"Hub intentionally does not calculate row
scores from linked trials"*), edited **transactionally** with `--dry-run` and timestamp-based
optimistic concurrency; **regrade** re-scores recorded outputs with an updated verifier and no agent
re-run, which only works because artifact collection is manifested; and **metrics are code**
(`metric.py` shipped in the dataset's `[[files]]`).

**ATIF v1.8** is a mature versioned trajectory interchange format — `schema_version`, `trajectory_id`,
`session_id` (run-scoped), `agent{name, version, model_name, tool_definitions}`, `steps[]` with
`source`/`tool_calls`/`observation`/`metrics` (incl. `prompt_token_ids` *"to avoid retokenization
drift"*), `final_metrics`, `subagent_trajectories[]`. Two structural consequences: a trajectory can be
a **tree**, and it references images/audio **by relative path** — so a trajectory is a *bundle*, not a
document.

### 5.4 GEPA — the eval cache, the Pareto archive, and a warning

`core/state.py` is the most sophisticated persistence model studied.

- **`_candidate_hash()`** — `sha256` over the sorted candidate dict. Content addressing, again.
- **`EvaluationCache` keyed `(CandidateHash, split, DataId)`** — exactly the cache §8.7 proposes,
  with a hard-won correction: *"Keys persisted before splits existed … cannot be told apart from
  contaminated distinct-valset entries, so `GEPAState.load` **drops them** rather than serving them.
  There is no default `split`: omitting it is a `TypeError`, not a silent trainset hit."*
- **A Pareto archive** — `FrontierType = instance | objective | hybrid | cartesian`
  (per val example, per objective metric, both, or example × objective), with
  `program_at_pareto_front_valset: {val_id: {candidate_idx}}` persisted to `pareto/*.json`. GEPA
  *selects* by sampling the frontier, so this is load-bearing.
- **Independent validation of the concurrency argument**: iteration ids became random 8-hex strings
  *"so the `iterations/` tree stays collision-free under concurrent/parallel proposal backends — where
  a monotonic counter is neither unique nor necessarily available (e.g. agent-swarm engines …)."*
- `iterations/<id>/` dirs are **immutable once `meta.json` exists**; `validation_schema_version` is
  persisted; `parent_program_for_candidate` is a list *of lists* (multi-parent merges).
- Weakness: resume truth is a **pickle** — unqueryable, unshareable, unsafe from an untrusted source.

`oa/ensemble.py` composes **multiple engines over one task** with five strategies — `sequential`
(*"monotonic: an engine that regresses doesn't poison the chain"*), `adaptive_sequential` (rotate on
score plateaus, sharing one budget), `parallel`, `best_of`, `vote` — and explicit budget semantics:
*"budgets are pre-partitioned per config; if an engine finishes early its leftover budget is not
redistributed."* `oa/eval_server.py` is *"the single choke point for evaluation, budget, and
tracking,"* exposing both an in-process call and **`POST /evaluate` for external/black-box engines**,
returning **429** when the budget is exhausted. See §10.2.

**gskill** (inside GEPA): SWE-smith mines a repo into ~300 verifiable tasks; GEPA optimizes a skill
against train ~200 / val ~50 / test ~60. Results in §3.3. Three consequences: skills are
agent-agnostic transferable artifacts; **transfer is not uniformly positive**; and **duration is a
co-metric that sometimes moves alone** — a single-scalar reward would call the saturated runs
worthless.

### 5.5 SkillOpt — a skill is a trained parameter; Sleep is the enrichment pipeline

Treats the skill document as *"the trainable state of a frozen agent"*, trained with epochs,
minibatches, a textual learning-rate budget, a **rejected-edit buffer** and an epoch-wise slow/meta
update, gated on held-out validation. Deployed artifact: a 300–2,000 token `best_skill.md` adding
**zero inference-time model calls**. Results span 52 `(model, benchmark, harness)` cells — **harness
is a distinct dimension from model and agent**. Artifacts are keyed by what they were trained for:
`ckpt/<benchmark>/<model>_skill.md`.

**SkillOpt-Sleep** is §10.1's pipeline, shipped:

```
harvest transcripts (Claude Code / Codex / Copilot / Cursor / Pi / OpenCode)
  → mine recurring tasks → replay → consolidate (reflect → bounded edit → GATE on held-out tasks)
  → stage proposal → (you) adopt
```

Two elements **correct** my earlier design: **review-then-adopt** — *"nothing live changes until the
user explicitly adopts it"* (enrichers should be able to *propose*, not only write); and
`gate_no_regression`, where *"a missing task result or non-finite task score also blocks the
candidate."* Its `evidence.jsonl` holds redacted, truncated copies of every prompt and reply, with an
instruction to *"treat it as sensitive local data and apply an appropriate retention policy"* and an
honest caveat that outbound prompts are *"not currently guaranteed to be secret-free."*

SkillOpt distributes itself as **seven host integrations, three of them MCP servers with ~7 tools** —
which is exactly the surface shape §8.11 proposes, so that plan is not speculative.

### 5.6 CORAL — multi-agent co-improvement of a shared skill set, working

Long-running agents *"explore, reflect, and collaborate through shared persistent memory,
asynchronous multi-agent execution, and heartbeat-based interventions"* in isolated git worktrees.
SOTA on 10 tasks, **3–10× higher improvement rates at fewer evaluations**; on Anthropic's kernel task
four co-evolving agents took the best known score from 1363 to **1103** cycles. Gains attributed to
*"**knowledge reuse** and multi-agent exploration and communication."*

```
.coral/
├── public/                            # shared; symlinked into every agent's worktree
│   ├── attempts/<commit-hash>.json    # MEASUREMENTS
│   ├── notes/001_agent-1_*.md         # FINDINGS, one file per note (conflict-free)
│   ├── skills/<name>/SKILL.md         # SHARED, AGENT-WRITABLE SKILLS
│   └── eval_count                     # global budget counter
└── private/                           # grader venv + answer keys — agents CANNOT read
```

An attempt is `{commit_hash, agent_id, title, score, status, parent_hash, timestamp, feedback}` with
`status ∈ {pending, improved, baseline, regressed, crashed, timeout}` — §8.7's measurement record
almost field for field, including crash/timeout as statuses distinct from a score.

Three mechanisms are new to this study. **Scope isolation as a *search* strategy**: islands exist so
each *"can explore a different region of the solution space without immediately converging on the same
ideas"* — diversity maintenance, not just collision avoidance. **Migration between scopes**, policied:
*"selects strong agents … without worsening island roster balance."* **Two-level visibility**: scoped
from inside a worktree, aggregated across islands from outside. And the access model is a precise
specification of what §10.2 needs — own worktree read/write, **sibling worktrees read-only**, shared
area read/write, **evaluation internals no access** — enforced by OS user isolation, not convention:
agents run unprivileged and cannot read the grader venv *"not even via Bash."*

Its limitation is the gap: one directory, one machine, one run.

### 5.7 AgentEvalHarness — judges are reward models; MLflow is the incumbent

Evaluates skills and agent capabilities from one declarative `eval.yaml`:
`analyze → generate cases → run → judge → trace in MLflow → optimize`, running locally, in **Harbor
containers**, or on EvalHub. LLM + code judges, pairwise A/B, thresholds, and `/eval-optimize` to
*"propose skill fixes from failures and re-run."*

**The judge is a reward model.** `/eval-train` plugs the judge engine in as the *verifier* inside
Harbor trial containers, which NeMo Gym wraps as an agent, feeding `(trajectory, reward)` to
NeMo RL / TRL / Unsloth / VeRL for GRPO/DAPO weight updates, with `collect_rollout_details: true`
capturing token ids. **[judgement]** This makes the evaluator/judge arguably the highest-leverage
artifact in the stack and the least version-controlled: change the judge and you change every reward,
every gate decision, and every trained model.

**MLflow is an incumbent worth naming.** AEH persists to MLflow (experiments, datasets, hierarchical
traces); GEPA has `--wandb`. The honest boundary is a layering, not a competition:

| Layer | Incumbent | Keyed by |
|---|---|---|
| execution substrate | Harbor, AEH | job / trial |
| run tracking | **MLflow, W&B** | experiment → run |
| **artifact registry** | **— the gap** | **artifact version digest** |

MLflow is run-centric; its Model Registry versions models, not skills as content-addressed bundles
whose identity outlives any run. SBS measurements should be **joinable to** an MLflow/W&B run id, never
a replacement for one.

---

## 6. skillberry-store today

### 6.1 What is genuinely strong

Assets none of the eight consumers has, and which would be expensive to rebuild **[read]**:

- **A real service**, not a run directory: five object types, OpenAPI, a React UI, an auto-generated
  `sbs` CLI.
- **Multiple delivery frontends** — vMCP (skill → MCP server), vNFS/WebDAV (skill → mountable
  filesystem, via `export_skill_to_directory()`), and an MCP *control* API exposing REST operations as
  MCP tools. The vNFS path is close to what Harbor needs.
- **Sandboxed tool execution** with transitive dependency resolution — a capability none of the eight
  has and several could use.
- **A 16-plugin architecture** with per-plugin identity, an owner-tenant model and scoped tokens.
  Several plugins already circle the target problem: `evaluator`, `skill-optimizer`, `provenance`,
  `dependency-tracker`, `dedupe`.
- **A designed ACL** with tenants, groups, roles, verbs, per-tenant MCP mounts — and a documented plan
  for the missing piece (`scope:`).
- **Server-side pagination, field selection** (`minimal ⊆ narrow ⊆ wide ⊆ full`) **and facets**, with a
  good rationale. The payload-discipline instincts are right.
- **Atomic writes and path-traversal hygiene** in storage, plus SAST/DAST/CodeQL and OpenShift
  hardening that most research code lacks.

The `skill-optimizer` plugin is a proof of intent: it exports a skill, stages metadata **and
trajectories**, runs Claude Code in a container, re-imports the result as a new skill, and attaches
`extra.optimization` with rationale, issues addressed, tool/snippet deltas and `source_skill_uuid`.
That is a lineage-and-evidence model — expressed in untyped `extra` blobs and a `_optimized(1)` naming
convention.

### 6.2 The data model, and why fidelity fails

`ManifestSchema` is the base: `name, uuid, version(str), description, state, tags[], extra{},
parent(uuid), created_at, modified_at`. A skill adds `tool_uuids[]`/`snippet_uuids[]`; a tool adds
`module_name`, `packaging_format`, `params`, `dependencies[]`; a snippet adds `content`.

A version chain *does* exist — `parent` is "the previous version with same name", with a
`name → HEAD uuid` cache. But the structural fact is: **a skill is a list of tool and snippet UUIDs,
not a file tree.** The tree is reconstructed at export from `file:<path>` **tags** on snippets, and
`SKILL.md` frontmatter is *regenerated* from `name` + `description`; on import `parse_skill_metadata()`
extracts exactly those two keys and `strip_frontmatter()` removes the rest. `read_from_folder()` opens
every file as UTF-8 and **drops** what fails to decode. (`is_text_file()`'s 18-extension allowlist
gates only `snippet_mode="paragraph"`; in `"file"` mode — the API default — every file is processed, so
`.csv`/`.sql`/`.jsonl` survive and the losses are specifically **binary** files.)

### 6.3 Persistence, indexes, runtime

- **Storage**: local filesystem, one directory per UUID. Atomic writes; path-traversal validated.
- **Git persistence is not a backend** — it is `ShellHook`, env-var-templated
  `subprocess.run(shell=True)` fired per write, typically `git add && commit && push`. No transaction,
  no atomic multi-object commit.
- **Caches — all in-process and authoritative**: `DictCache` (every object dict in memory),
  `LookupCache` (name → HEAD), `DependencyManager` (reverse deps, rebuilt at boot by full scan). None
  persisted.
- **Semantic index**: FAISS/Chroma/LanceDB over **descriptions only**, 384-dim MiniLM, default `k=5`,
  `similarity_threshold=1` as an L2 *distance* ceiling.
- **Change detection**: a single process-global integer counter.
- **List/filter/sort**: in-memory scan of the full cache per request; substring match on
  name+description; AND-only tag filter; offset/limit. Bare array without `limit`/`offset`, envelope
  with — deliberate back-compat.
- **"Namespaces" are tags with a `namespace:` prefix**, split out for the UI picker. Not an isolation
  boundary.
- **Runtime**: single-process uvicorn — stated in the ACL design: *"Single-process only … sessions
  won't be shared."* The same is true of all three caches.
- **Backup**: whole-store JSON dump; **restore purges first**.
- **Plugin events**: in-process, uuid-only payloads, no external webhook or durable log.

### 6.4 Where evaluation evidence lives today

There is already an evaluation story, which shows both the demand and the shape of the gap: the
`evaluator` plugin writes `quality-score:N` / `performance-score:N` as **string tags** plus free text
in `extra["evaluation"]`, stripping the old tags first so *"re-evaluation is a clean overwrite"* — no
history, no task, no dataset, no split, no cost, no comparability, and numbers encoded as strings
cannot be compared numerically. `skill-optimizer` records lineage in
`extra["optimization"]["source_skill_uuid"]` and takes trajectories from a **filesystem path**. No
measurement, trajectory, finding, task, dataset or split object exists.

**[judgement]** SBS independently arrived at "artifacts need attached evidence" and implemented it with
the only tools its schema offers. The design below mostly gives those instincts proper types.

### 6.5 Measured behaviour

All **[measured]** at `main` (`c6e72ce`); method in Appendix A, corpus results in Appendix B.

| ID | Finding |
|---|---|
| **M1** | Round-tripping 12 real published skills: **0/12 preserve a bundle digest** (bytes + modes). 93/126 files byte-identical, 1 lost, 18 mode changes. Even a single-file skill fails — a folded multi-line YAML `description` is re-emitted as one line, so `SKILL.md` is always rewritten |
| **M2** | **Licence fabrication.** `algorithmic-art` declares `license: Complete terms in LICENSE.txt`; the file is Apache-2.0; SBS emits `license: Proprietary. LICENSE.txt has complete terms`. 17/81 of the corpus |
| **M3** | **Silent binary loss.** `web-artifacts-builder` lost `scripts/shadcn-components.tar.gz`, `canvas-design` 54 `.ttf` fonts — with `ignored_files: []` in both cases |
| **M4** | **Executable bits lost** on 38 files across 15/81 skills, all under `scripts/` |
| **M5** | Frontmatter reduced to `{name, description}`: 36/81 skills lose keys. `_mean`, an underscore-private helper, became a first-class public tool — contradicting the contract cap-evolve's SPA skill documents and relies on |
| **M6** | Re-importing the same skill dir creates a new skill version **but duplicates every tool and snippet by name** (3 tools → 6, 6 snippets → 12); `GET /tools/compute_score` silently resolves to one |
| **M7** | `DELETE /skills/{n}?delete_tools=true&delete_snippets=true` → **200**, `deleted_tools: []`, 18 objects orphaned. The cascade is inert and reports success |
| **M8** | No `ETag`, no `Last-Modified`; `PATCH` → **405** on tools/skills/snippets. Full-replace `PUT` is the only mutation |
| **M9** | **Lost update reproduced first attempt**: two barrier-synchronised GET→modify→PUT cycles both returned 200; one write vanished silently. This is precisely `spa_env`'s tagging pattern — and the tag it writes is the `FROZEN_TAG` that protects the benchmark's primitives from deletion |
| **M10** | `PUT` **mutates in place** — same UUID, `parent` still null, no new version — and being full-replace it **wiped `tags`**. So any measurement can be invalidated by a later `PUT`, undetectably |
| **M11** | Response shape varies with params (`GET /tools/` → array; `?limit=10` → envelope). `POST /snippets/` takes `content` as a **query parameter**; a JSON body is a 422 |
| **M12** | **Body content is unretrievable.** A query quoting an artifact's own text verbatim scores **1.713** vs **1.898** for a completely unrelated query; a description-matched query scores 0.257. At the **default** threshold the body query returns **zero** results |
| **M13** | Read scaling: a paged read is flat (~4 ms) as the collection grows 20×; an unpaged `fields=wide` list is O(n) (22.9→84.9 ms for 500→2000); a tag filter matching **nothing** costs 2.0→3.2 ms — a full scan, no tag index |
| **M14** | `/search` is flat at **~300 ms** regardless of collection size — dominated by embedding the *query*, capping semantic search at ~3 q/s/core |
| **M15** | Snippet create median **307–600 ms**, flat across 0→2000 objects: **~2–3 writes/s** serial, from synchronous embedding + index persistence on the write path |
| **M16** | **One concurrent writer degrades a paged read 99×** — 7.1 ms → 699.5 ms (p90 800.9 ms) |
| **M17** | Near-duplicate detection over all 3,240 corpus pairs found **0** above 0.30 Jaccard — a **negative** result: dedupe has no yield on curated corpora |

### 6.6 The defects are not independent

```
bundle is not the source of truth
  └─> no byte-faithful artifact (M1–M5)
        └─> no meaningful digest → no version identity
              ├─> Harbor cannot use SBS as a skill source (needs digest + immutable ref)
              ├─> measurements cannot bind to anything stable (M10)
              └─> no cross-run eval cache (the largest measurable cost saving)

names global, versions addressable only by UUID (M6)
  └─> two candidates cannot coexist
        └─> store must be reset per candidate
              └─> the delete path is exercised constantly, where the cascade is inert (M7)
                    └─> orphans accumulate → name resolution becomes ambiguous
                          └─> client-side Protection, fail-closed heuristics, retry loops

no OCC, no PATCH, full-replace PUT (M8–M10)
  └─> read-modify-write is the only idiom
        └─> lost updates, undetectable
              └─> a lost FROZEN tag deletes a protected primitive
                    └─> a silently wrong tool set is measured as if it were the candidate
```

That last chain is the one to fix first, because its failure mode is **a wrong number that looks
right** — and every one of these systems exists to produce numbers people will act on.

---

## 7. Gap analysis

Target capabilities (C1–C25, defined in §8) against measured reality. Severity is about consequence:
**P0** silently wrong results or a structural block; **P1** forces a fragile consumer workaround;
**P2** limits value or scale.

| # | Capability | SBS today | Sev |
|---|---|---|:-:|
| C1 | Bundle as source of truth | objects are truth; tree rebuilt from `file:` tags; frontmatter normalized; binaries dropped; modes lost. **0/12 digest fidelity** | **P0** |
| C2 | Open artifact taxonomy | 5 closed types. **10 of 15 target kinds absent**: measurement, trajectory, finding, weakness, task, dataset/split, evaluator, run, policy, environment | **P0** |
| C3 | Harbor-compatible refs + digests | no digest; `version` a free string hardcoded to `1.0.0` on import; non-HEAD versions reachable only by raw UUID; no `@tag`/`@rev`/`@sha256` | **P0** |
| C4 | Atomic publish + declarative apply | import creates tools one-by-one and **swallows per-item failures**; a partial skill is reported as success. No transaction, no apply | **P0** |
| C5 | Measurements as records | none — score tags + free text, overwritten on re-eval | **P0** |
| C6 | Shared eval cache by digest | none (no digest) | P1 |
| C7 | Sealed splits / tamper / receipts | no splits, no protected paths, no signing; every import lands `approved` | P1 |
| C8 | OCC + PATCH + async indexing | no ETag/If-Match; PATCH 405; full-replace PUT; ~400 ms synchronous write path | **P0** |
| C9 | Namespaces + workspaces | `namespace:` tag prefixes; names global; ACL `scope:` designed but **deferred**; no owner field | **P0** |
| C10 | Typed labels, verbatim frontmatter, query | `tags[str]`, untyped `extra{}`; substring match; AND-only tags; offset pagination; fixed 5-state lifecycle | P1 |
| C11 | Hybrid body retrieval + findings + assembly | description-only embeddings; no lexical, no pre-filter, no findings kind | P1 |
| C12 | Provenance, contamination, secrets, licence | `extra.origin` for URL imports only; no contamination fields; no secret scanning; **licence actively fabricated** | P1 (licence **P0**) |
| C13 | Interaction surfaces | REST/CLI/MCP/vMCP/vNFS already strong. Missing: an SDK with a shared digest impl, a store-usage skill, an external event stream | P2 |
| C14 | Externalized indexes, dedup, tiering, backup | single-process; three authoritative in-process caches; no dedup; whole-store backup whose restore purges. **99× read stall** | P1 |
| C15 | Per-object authz, visibility, quotas, audit | verb/resource RBAC is a good base; `scope:` unenforced; no owner, visibility, quotas; facets computed globally | P1 |
| C16 | Pareto-frontier queries | no per-task scores at all | P1 |
| C17 | Transfer matrix `(model, harness, agent)` | dimensions absent entirely — and transfer can be **negative** | **P0** for a public catalogue |
| C18 | Counterfactual / control measurements | none | P1 |
| C19 | Shared budget / eval ledger | none | P1 (P0 for §10.2) |
| C20 | Scope migration + two-level visibility | none | **P0** |
| C21 | Judge / verifier as a versioned artifact | not an artifact at all; no regrade-staleness | P1 |
| C22 | Staged proposals requiring adoption | a plugin's write is immediately live | P1 |
| C23 | Lenient, non-destructive metadata parsing | `parse_skill_metadata` returns `None` on any YAML error, discarding **all** frontmatter | P1 |
| C24 | Split mandatory in the cache key | no cache, so the hazard is latent rather than absent | P1 |
| C25 | Run-tracker interop | nothing joins an SBS object to an MLflow/W&B run | P2 |

### 7.1 The P0 set as five decisions

1. **Is the bundle or the object graph the source of truth?** (C1) — must be the bundle.
2. **Does an artifact version have an immutable, content-addressed identity?** (C3) — or measurements
   bind to nothing and Harbor cannot integrate.
3. **Is name→artifact global or scoped?** (C9, C20) — or parallel optimization is impossible.
4. **Can two writers safely touch one object?** (C8) — or protection tags are lost and wrong tool sets
   get measured.
5. **Is a measurement a first-class record?** (C5, C17) — or SBS stays a catalogue.

### 7.2 What the gaps already cost the live consumer

| `spa_env.py` workaround | Gap | Removed by |
|---|---|---|
| 110-line manual cascade; dependent kinds read from SBS source | C4, C2 | atomic delete with server-side topological order |
| `_delete_all()` retry-until-no-progress | C4 | same |
| `purge_orphans()` | C4, C9 | workspace-scoped GC |
| `Protection` + tripwire + fail-closed reads | C7, C9 | immutable workspace pins |
| `reset_store_to_skill()` | C4, C9 | one `:apply` call |
| GET→mutate→PUT tagging | C8 | `PATCH` / labels-at-create |
| `_list()` dual-shape handling | C10 | one response envelope |
| `public_functions()` AST re-implementation | C1, C2 | server-side projection (and M5 shows the client's assumption is currently wrong) |
| "SPA serves exactly ONE skill" + restart per candidate | C9 | workspace-bound resolution |

---

# Part III — The work

## 8. Target design

External behaviour and API only. Principles: **additive and versioned** (everything new under `/v2`;
`/v1` keeps its exact contract); **the bundle is truth, projections are derived**; **immutable
versions, mutable pointers**; **never coerce missing data to a value**; **borrow grammar rather than
invent it** (Harbor's refs, ATIF, JSON Schema, `SKILL.md` as-is); **degrade to advisory** (every
guarantee needs a warn-only mode, because a hard failure mid-run strands a spent budget).

### 8.1 Artifact taxonomy (C2) — fifteen kinds, open and schema-registered

| Class | Kinds | Forced by |
|---|---|---|
| **Capability** (SBS owns) | skill · tool · snippet/prompt · **toolset** (`tools.json`) · **bundle-set/lockfile** | all |
| **Eval** (reference Harbor where present) | **task** · **dataset/split** · **evaluator/verifier/metric** | Harbor, AEH, Arbor |
| **Evidence** (SBS owns) | **measurement** · **trajectory** (ATIF) · **finding** · **weakness+solution** · **experiment/run** | all |
| **Context** | **policy** (protected paths, action policy) · **environment** | cap-evolve, Arbor, Harbor |

Three are the differentiators: **finding** (nobody stores these), **measurement** (without it SBS is a
filing cabinet), **trajectory** (the input to every diagnose step, today passed as filesystem paths).
Kinds must be **open** — a registered `kind` with a schema — so a ninth framework can add
`reward-model` without a store release.

### 8.2 Identity, refs, versioning (C1, C3)

**Names** `<namespace>/<name>`, unique per `(namespace, kind, name)` — namespace a real isolation
boundary, not a tag prefix. **Refs** adopt Harbor's grammar verbatim:

```
ns/name              ns/name@1.4.0        ns/name@17           # latest · tag · revision
ns/name@sha256:ab12… ns/name@latest       ns/name@<workspace>  # digest · explicit · workspace pin
```

**Immutability.** A published version never changes: `PUT` on a version → `409`. Mutations create a
new version; tags and `latest` are mutable pointers moved by compare-and-set. Deletion is **yank**
(unresolvable by `latest`, still resolvable by digest, measurements stay interpretable) — never history
rewrite, which silently invalidates every recorded measurement.

**Two digests**, because they answer different questions: `content_digest` (every byte, every mode —
Harbor's lock) and `capability_digest` (with a declarable `digest_ignore` scope — cap-evolve's eval
cache, which deliberately excludes optimizer scratch). Canonical form: entries sorted by relative path,
each contributing `path\0mode\0content\0`. **Include mode** — cap-evolve and Harbor both lose the exec
bit otherwise.

**Lineage.** `parents[]` — plural, for merges, which cap-evolve, Arbor, GEPA and CORAL all produce —
plus `edit_kind`, `produced_by`, and typed edges carrying a `why` string.

**Frontmatter is stored verbatim**, unnormalized. This alone fixes M2/M5 for all eight consumers:
`when_to_apply`, `component`, `provides`, `needs`, `allowed-tools`, the real `license`, the real
`version`. The store may *index* known keys; it must never rewrite or synthesize one. Parsing is
**lenient** (C23): a malformed block degrades to a flat parse and the raw text is kept.

### 8.3 The resource model

```
/v2/artifacts/{ns}/{kind}/{name}@{ref}      → one immutable version
    …/@{ref}/bundle | files/{path} | manifest | projections/{tools|snippets|frontmatter|params|deps}
    …/@{ref}/lineage | measurements | diff/{other-ref}
/v2/blobs/{digest}                          → content-addressed, deduplicated
/v2/workspaces/{ws}                         → pins, :apply, /lock
/v2/measurements  /v2/trajectories  /v2/findings  /v2/runs  /v2/datasets
/v2/search  /v2/context:assemble  /v2/events
```

### 8.4 Behavioural changes that will be felt (C4, C8, C10)

| Before → After |
|---|
| **B1** binaries dropped, modes lost, frontmatter normalized, licence fabricated → **round-trip digest-identical**; `ignored_files` reports every skipped file and never silently skips one |
| **B2** `PUT` mutates in place → `409` on a version; mutation creates revision N+1; `latest` moves under `If-Match` |
| **B3** no ETag, lost updates → `ETag` on every GET; `If-Match` **required** on pointer and label mutations; `412` on conflict |
| **B4** PATCH 405 → merge-patch on labels/state/frontmatter; `labels:merge` as an **atomic additive** write (so producer-namespaced labels are conflict-free by construction) |
| **B5** partial import reported as success → **atomic publish**, all-or-nothing, idempotent on digest |
| **B6** delete rewrites history → **yank** with a reason; hard delete is an admin action |
| **B7** cascade returns 200 having deleted nothing → deletes in dependency order or **`409` naming what blocks it**. Never success-with-nothing-done |
| **B8** score tags overwritten → **append-only measurement records**; `failed_to_run`/`tampered`/`skipped` storable and **excluded from aggregates by construction** |
| **B9** array-or-envelope by params → `/v2` always `{items, total, next_cursor}`, cursor-paginated |
| **B10** `POST /snippets/?content=…` → JSON/multipart body |
| **B11** ~400 ms synchronous write → publish returns when the bundle is durable; `index_state`; `?consistent=true` to wait |
| **B12** in-process uuid-only plugin events → durable log + SSE/webhook (`artifact.published`, `tag.moved`, `measurement.recorded`, `finding.recorded`, `artifact.yanked`) |
| **B13** verb-level authz → owner on every object; the designed `scope:` enforced; visibility private-by-default; facets and search scoped per subject |
| **B14** whole-store dump whose restore purges → incremental, per-namespace, non-destructive import |

### 8.5 Isolation: namespaces and workspaces (C9, C20)

**Namespaces** are the durable ownership/auth/quota boundary. **Workspaces** are the cheap,
copy-on-write *view* boundary that unblocks parallel optimization — a named set of pins over one or
more namespaces:

```
runA:  probe-skill → @sha256:aa…    bench/primitives → @sha256:ff… (immutable pin)
runB:  probe-skill → @sha256:bb…    bench/primitives → @sha256:ff… (same blobs, shared)
```

Consumers address `?workspace=runA`; a proxy/vMCP/vNFS binds a workspace instead of a global name.
Consequences: two candidates named `probe-skill` coexist, so **the store no longer has to be reset
between candidates**; the frozen substrate is an immutable pin rather than client-side `Protection`;
a workspace *is* Harbor's lockfile (`GET /v2/workspaces/{ws}/lock`); deleting a workspace makes its
private versions garbage while shared blobs are untouched.

Borrowing CORAL: scoping is also a **search knob** (islands exist to prevent premature convergence),
so add **policied migration** between scopes and **two-level visibility** — scoped reads for
participants, aggregated reads for operators.

**The declarative apply** replaces the 5–7-call deploy dance:

```http
POST /v2/workspaces/runA:apply
{ "artifacts": [{"ref":"bench/tools/frozen-primitives@sha256:ff…","pin":"immutable"},
                {"ref":"runA/skill/probe-skill@sha256:ab…"}],
  "prune": true, "dry_run": false }
→ 200 { added, removed, kept, unchanged, plan_digest }
```

Idempotent, order-independent, atomic; the **store** computes the delete order. This is the
highest-leverage API here because it deletes the *reason* for most of `spa_env.py`.

### 8.6 Making SBS a Harbor skill source (C3)

**(a) Zero Harbor change.** Serve a workspace or version as a read-only **git-compatible HTTP
endpoint**, so Harbor's existing syntax resolves:
`harbor run --skill https://sbs/git/runA/tree/<ref>/probe-skill`. Harbor clones, resolves a SHA, caches
it, and computes its own digest over all files — and because B1 makes the tree byte-faithful,
**Harbor's digest and SBS's `content_digest` agree**, so the job lock resolves back to an SBS ref.

**(b) Native.** `…/bundle` plus a small `sbs://` resolver, or a vNFS/WebDAV/FUSE mount of a workspace at
`environment.skills_dir`.

**Inbound**, closing the loop: `POST /v2/jobs:import` reads a Harbor job dir or Hub UUID → trajectories
+ measurements, mapping trial → measurement, `result.json` → metrics, ATIF traces → trajectory bundles,
the job lock → the measurement `context`.

### 8.7 Measurements (C5, C16–C19, C24, C25)

```jsonc
{ "subject": "runA/skill/probe-skill@sha256:ab…",
  "context": ["model:gpt-oss-120b", "harbor/dataset/tau2-airline@3", "judge/rubric-v2@sha256:…"],
  "task": "airline_042", "dataset": "harbor/dataset/tau2-airline@3",
  "split": "val",                       // MANDATORY — omitting it is an error, not a default (C24)
  "trial": 2, "seed": 1234,
  "metrics": [{"name":"reward","value":0.75,"direction":"higher","primary":true},
              {"name":"duration_s","value":142,"direction":"lower"}],
  "status": "scored",                   // | skipped | failed_to_run | tampered | excluded
  "control_of": null,                   // C18: the without-artifact arm of a counterfactual pair
  "feedback": "agent cancelled the wrong segment",
  "cost": {"usd":0.031,"prompt_tokens":18422,"cached_tokens":16000},
  "trajectory": "runA/trajectory/airline_042__ab12__t2",
  "producer": {"harness":"cap-evolve/0.9.1","agent":"claude-code","model":"…",
               "run":"runA","run_ref":"mlflow://exp/17/run/abc"} }   // C25
```

Non-negotiables, all learned the hard way by 6+ systems: **`status` is not a score** (a tampered or
errored trial is *missing data*; coercing it to 0.0 poisons both the mean and the paired test); **the
split travels with the number**; **records are append-only** (a re-measurement is a new record);
**multi-objective with exactly one primary**.

Queries — these are the product:

```
agg(subject, dataset, split)      → mean, SE, pass^k, coverage, n
paired(a, b, split)               → per-task Δ, Δ̄, SE, broke[], fixed[], protect_set[]
frontier(dataset, split, type)    → C16: instance | objective | hybrid | cartesian
transfer(subject)                 → C17: score per (model, harness, agent), incl. negative
uplift(subject)                   → C18: vs its control arm
seen(capability_digest, task, dataset, split)  → the eval cache          ← the dollar saving
budget(scope)                     → C19: spent/remaining; 429 when exhausted
```

### 8.8 Findings and the weakness graph (C11, C22)

```http
POST /v2/findings   { kind: leverage|pitfall|rejected, thesis, scope{project,dataset},
                      evidence[], affected_tasks[], subject }
GET  /v2/findings?scope.project=…&rank=recurrence&budget_tokens=1200
→ { items:[{kind, thesis, recurrence:3, evidence[]}…],
    rendered:"## PRUNED LESSONS (7 — these directions FAILED…)" }
```

`recurrence` implements Arbor's `[xN]`; `rendered` returns a prompt-ready block, because all eight
hand-roll that rendering and the ordering — failures first — *is* the designed artifact. Weakness and
solution artifacts are ordinary kinds with typed edges, plus one enforced invariant: **`affected_tasks`
freezes after the discovery revision** (`409` on change), moving cap-evolve's freeze rule from prose
into the store.

**Staged proposals (C22).** An enricher or optimizer may **propose** a version or a label without
changing what is live — SkillOpt-Sleep's review-then-adopt, which nothing live changes until the user
adopts.

### 8.9 Retrieval, scale and ops (C11, C14)

**Retrieval** must serve a model composing a context window, not a human browsing: index the **body**
(chunked embeddings) plus lexical BM25 for exact identifiers; **pre-filter** on metadata *and
measurements* before vector search ("skills scoring > 0.7 on dataset X that mention refunds"); serve
negative knowledge as a named surface; offer budgeted `context:assemble`; and make it cheap — ~300 ms
per query caps throughput at ~3 q/s/core.

**Scale**: externalized indexes (SQLite → Postgres) so multiple workers can serve reads, with no
authoritative in-process caches; content-addressed blobs with dedup (20 iterations of a 9-file skill
differ in 1 file); **tiering** — measurements are small/hot/queried, trajectories are large/cold/
sensitive, and must not share a retention policy; partial fetch of one path from a bundle (Harbor's
regrade needs exactly this); incremental non-destructive backup; deterministic startup that does not
rebuild every index by full scan.

### 8.10 Integrity, comparability and governance (C7, C12, C15, C21)

- **Sealed splits, opt-in** — the store as referee: reads of test-split *targets* are metered and
  logged; a second attempt returns `409` naming the first. Tamper-evident protected paths mark affected
  measurements `status: tampered`, never `0.0`. A **signed evaluation receipt** (`subject digest +
  context lockfile + dataset version + split + metric + producer`) lets a third party verify a claimed
  number without trusting the claimant.
- **Judges as artifacts (C21)** — versioned, digested, and cited by every measurement, with
  regrade-staleness labels when a judge version moves. They are reward models in RLAIF.
- **Provenance and supply chain** — source + commit, importer version, every transformation;
  contamination fields (`release_date`, `is_public`, `canaries[]`) as queryable data; **mandatory
  secret scanning on ingest** (cap-evolve observed a real API key committed into a results file and
  shipped to the optimizer); **licence fidelity — preserve, never infer**.
- **Governance** — per-object ownership and row-level authz (the designed `scope:`, enforced);
  visibility private-by-default with cascade; quotas per namespace incl. sealed-split reads; audit log;
  approval workflow so `approved` means something an operator configured.

### 8.11 Interaction surfaces (C13) — all of them, with distinct jobs

| Surface | Caller | Job | Why not another |
|---|---|---|---|
| **HTTP `/v2`** | frameworks, CI | the contract | versioned, typed, cacheable |
| **Thin SDK** (py/ts) | adapters, plugins | digest computation, ETag handling, bundle packing | clients must not re-implement the digest — divergence is silent corruption |
| **CLI (`sbs`)** | humans, shell hooks | scriptable publish/apply/query | cap-evolve's `store: command` takes a shell string — a **zero-code** integration |
| **MCP server** | the optimizing agent | query evidence mid-loop | ~7 tools; SkillOpt ships exactly this shape |
| **Skill** | any agent host | teach *when* and *how*, and the invariants | no tool schema conveys "never coerce a failed trial to 0.0" |
| **Filesystem projection** | Harbor, sandboxes | materialize a workspace as a directory | Harbor wants a *directory*; a mount needs no Harbor change |
| **Git read endpoint** | Harbor `--skill https://…` | be a skill source with no client change | the cheapest Harbor integration |
| **Event stream** | dashboards, CI, plugins | react to publish/measure/yank | in-process events cannot reach an external consumer |

### 8.12 Compatibility and migration

`/v1` frozen, including the dual list envelope and query-param bodies; deprecate only once `/v2` has a
real consumer. **Backfill is lossy and must say so**: existing objects can be migrated by running the
current exporter, but M1–M5 mean the result is *not* the original artifact — label such versions
`provenance.reconstructed: true` and never present their digests as authoritative. **Prefer re-import**
where `extra.origin` gives a re-fetchable source, which is most of the bulk-imported catalogue.

---

## 9. Integration blueprints

Integration cost measured in lines changed in *their* repo. The surface differs per project because
each has a different natural seam.

| Project | Seam | Their LOC |
|---|---|---|
| **cap-evolve** | `store: command` → `sbs publish` (record-keeping); then SDK for `:apply`, `:seen`, `memory_skill: sbs` | **0**, then small |
| **Harbor** | git read endpoint or workspace mount (outbound); `jobs:import` (inbound) | **0** |
| **Arbor** | a plugin: `RecordFinding` → `POST /v2/findings`; intake recall → `findings?rank=recurrence`; `code_ref` → version | one plugin |
| **GEPA / gskill** | `optimize_anything` callback publishing candidates; `:seen` before each rollout; gskill publishes per-repo skills + transfer matrix | small adapter |
| **SkillOpt** | Sleep's staging target becomes a store **proposal**; `ckpt/` published with `(benchmark, model)` labels | small |
| **CORAL** | `.coral/public/{attempts,notes,skills}` backed by the store; islands → workspaces | moderate |
| **AEH** | judges published as versioned artifacts; measurements carry `judge@digest` + `run_ref` | small |

**cap-evolve, concretely.** Seam 1 needs no code change at all:

```yaml
store: command
store_commit_cmd: >-
  sbs publish --namespace {tag} --kind skill --name probe-skill --bundle {dir} --label msg="{msg}"
```

Seam 2 replaces `reset_store_to_skill()` with one call, retaining the rule that matters — `apply()`
**must not raise**, so infrastructure failure *excludes* a candidate rather than scoring it 0.0:

```python
sbs.workspace(run_id).apply([Pin(FROZEN_SUBSTRATE_REF, immutable=True),
                             Pin(sbs.publish(ns=run_id, kind="skill", name=SKILL_NAME, bundle=d))],
                            prune=True)
```

deleting `delete_skill`, `delete_skill_dependents`, `_delete_all`, `purge_orphans`, `Protection`,
`purge_store` and the per-candidate SPA restart.

**Boundary to hold everywhere:** SBS must not re-own tasks and datasets (Harbor Hub), run tracking
(MLflow/W&B), or evaluation execution (Harbor/AEH/GEPA's `EvalServer`). It owns the capability artifacts
and the evidence that joins them.

---

## 10. Two strategic bets

### 10.1 Bet 1 — labels + plugins as an enrichment pipeline

SBS's two distinctive assets are **object labelling** and **plugins that enrich objects
asynchronously**. Immutability makes them *safer*, not harder:

> **Immutable content + mutable, producer-attributed labels = a two-speed store.** Consumers write
> **facts** fast and synchronously; the store derives **judgements** slowly, asynchronously, and *once*.

Because content cannot change under a label, every derived label is attributable to an exact digest —
so enrichment is cacheable, invalidatable on plugin upgrade, **never stale** (today it can be: `PUT`
mutates in place), comparable across a corpus, and backfillable. The unique capability is not
asynchrony — they can all run code — it is **amortization and cross-corpus scope**: the store is the
only place where every run's artifacts coexist and a derivation can be cached against an immutable
digest and reused by a different run, framework or user.

**It is ~70% built**: async dispatch (`on_content_*`), per-plugin owner identity (so *"a scanner's
coverage does not become a function of who uploaded"*), **additive union** tag writes, a
producer-namespaced closed outcome vocabulary (`result`/`skip`/`error`), two live subscribers, and six
of sixteen plugins that are pure derivations.

**The value is real, demonstrated, and concentrated in deterministic work.** The ~90-line enricher
behind Appendix B found 17 verifiable licence conflicts, 38 at-risk exec bits and 7 destroyed
frontmatter conventions across 81 skills — **none of it discoverable from inside one skill or one run.**
And the first useful output is a census of the store's own import defects, which is an unusually honest
demo. Three classes, with very different standing:

| | Class | Status |
|---|---|---|
| **A** | Deterministic corpus-scale derivation (licence, secrets, binaries, modes, deps, structure) | **Demonstrated** (App. B) |
| **B** | Cross-run aggregation (flaky tasks, regrade staleness, recurrence ranking) | **Plausible, unmeasured** — needs the measurement kinds |
| **C** | LLM-judgement enrichment (`quality-score:N`) | **Weak, and currently hazardous** |

Two corrections worth stating: the **cost-saving headline does not belong to enrichment** (the
deterministic parts are milliseconds; the expensive LLM clustering barely reuses across candidates — the
real saving is the eval cache, which avoids *rollouts*); and **dedupe has no yield on curated corpora**
(M17).

**Fix before promoting.** The pipeline is racy by construction: `update_*_tags` does read-modify-write
while `emit_event` runs handlers concurrently, and two plugins already subscribe. And the `evaluator`
plugin writes `quality-score:8` as a **plain tag**, indistinguishable from human curation and one field
from real rewards — an LLM's opinion presented as evidence is exactly the failure this document is
organized against. Needs: the atomic merge (T0), a structural **fact vs judgement** class, typed
attributed labels (`computed_for_digest`, `plugin_version`, `deterministic`, `derived_from_splits`),
queryable enrichment state, triggers on *evidence* arriving, throttled backfill, and cost governance.

**Demos, in build order:** (1) the corpus defect census — done; (2) "the evidence panel nobody wrote";
(3) Arbor's `distill_abstract` moved off the run budget. **Not** downstream "optimizes faster" claims.

### 10.2 Bet 2 — several optimizers co-improving one skill set

**Does it make sense? Yes — validated three times.** CORAL: N agents co-improving a shared
`.coral/public/skills/`, SOTA on 10 tasks, gains attributed to knowledge reuse. GEPA's `ensemble.py`:
five composition strategies over one task. SkillOpt-Sleep: six agent hosts consolidated into one skill.
What is **unoccupied** is heterogeneity — GEPA composes GEPA engines in one process, CORAL composes
agents in one run; nobody composes GEPA + SkillOpt + cap-evolve + Arbor. A store is the only plausible
meeting point, because these share no process, runtime or dependency set — but can share an artifact and
a measurement.

**What it requires: three services and one agreement.**

**The agreement is comparability, and it is the part most likely to be skipped.** Every participant must
score against the **same task set, split assignment and metric definition**. GEPA identifies val
examples by position in its own loader; cap-evolve freezes its own `splits.json`; SkillOpt ships
per-benchmark manifests. If two optimizers report 0.71 and 0.68 against private splits, **`best_of` is
meaningless and `vote` is a confident wrong answer.** This makes the dataset/split and evaluator/metric
kinds *prerequisites*, not nice-to-haves — the single most important finding here.

1. **Shared artifact + lineage state** — immutable, content-addressed, multi-parent, with an
   authoritative **running best** each optimizer seeds from (GEPA's monotonicity rule).
2. **Shared measurement + budget ledger** — comparable results and one budget with an exhaustion signal.
   **The store must not *run* evaluations**; Harbor/AEH/GEPA's `EvalServer` do that, and GEPA's is
   explicitly designed for external black-box engines over HTTP.
3. **Shared knowledge surface** — the refutation log. For heterogeneous optimizers this is where most of
   the gain lives: B not re-deriving what A already refuted.

Plus CORAL's access model: own scope read-write, **sibling scopes read-only**, shared area read-write,
**evaluation internals no access**.

**Is it doable today? No — and the shape is slightly wrong.** SBS plugins are **in-process Python**;
these are separate CLIs with heavy incompatible dependency sets, and loading them in would put arbitrary
optimizer code inside the service holding the sealed splits. **The right shape is a plugin as a
*connector*** driving the optimizer out-of-process — and SBS already has that machinery
(`skill-optimizer` drives Claude Code in a container; `StoreAPI` exposes `internal_token()` and
`mcp_sse_config()` under *"Out-of-process delegation"*). But eleven requirements are missing, and **two
fail silently**: lost updates (M9) and the 99× read stall (M16). With N writers and no `If-Match` the
symptom would be a wrong score on a candidate nobody produced.

**A narrower version is reachable much sooner.** The store as the shared **knowledge and lineage plane**:
each optimizer keeps its own eval; before proposing it reads
`findings?rank=recurrence`; after each attempt it publishes the candidate bundle with `parents` and
records a finding including refutations. Needs only T0–T2 plus T5 — no shared budget, no common metric,
because nothing is being compared. Sell it as knowledge sharing, **not** ensembling.

**How to demonstrate it**, in build order:

| # | Experiment | Metric | Standing |
|---|---|---|---|
| **X1** | **Duplicate-work rate.** Two optimizers on the same seed; count how often they independently produce the **same `capability_digest`**, and how often the shared cache therefore skips a rollout | rollouts avoided; duplicate-edit rate | **Only a store can measure this** — it needs a common content address across two processes. Build first |
| **X2** | **Findings transfer.** B runs with and without A's refutations | count of refuted directions avoided | A *count*, robust at small n |
| **X3** | **Relay A/B.** Same seed, split and *total* budget; one optimizer vs two sharing it sequentially | final held-out score | The headline claim — but needs many paired runs |
| **X4** | **Diversity vs convergence.** Two optimizers in one scope vs two isolated scopes with migration | distinct edit surfaces; best score | Tests C20 for heterogeneous optimizers |

**Risks specific to this bet:** incomparable numbers presented as a comparison (mitigate: refuse to rank
measurements whose dataset version or metric differ); budget asymmetry; regression poisoning (seed from
`best`, not `latest`); premature convergence; credit attribution (solved by `paired(parent, child)`);
**multiplied leakage risk**, which is where sealed splits stop being optional; optimizer code inside the
store (connectors only); and **prompt injection across producers** — A's finding is data B's model
reads, so render findings as quoted, attributed blocks and say so in the store-usage skill.

**Verdict.** The most differentiated idea here, because it is the one capability structurally
unavailable to all eight: each can compose its own kind, none can compose across kinds. Build in the
order **X1 → the knowledge plane → X2 → X3**, and do not call it ensembling until comparability is real.

---

## 11. Risks, non-goals, open questions

| Risk | Why it is real | Mitigation |
|---|---|---|
| **Scope explosion** | This describes a different product from today's SBS | Sequence by the five P0 decisions; T0+T1 is a coherent, independently valuable increment |
| **`git` is a strong incumbent** | Seven of eight chose it: free, offline, byte-exact, in every CI | Do not replace it — index it. Accept git refs as provenance; win on query, measurement and sharing |
| **Harbor Hub overlap** | Hub already registers tasks/datasets and stores jobs/trials/trajectories | Stay on the capability side; reference Harbor refs; make job-import the flagship integration |
| **MLflow / W&B own run tracking** | AEH persists to MLflow; GEPA has `--wandb`; teams have them deployed | Layer, don't compete: they are run-centric, SBS artifact-centric. Join via `producer.run_ref` |
| **Measurement schema bloat** | Every framework will want one more field | Small required core + `extra`; promote a field only when two frameworks need it |
| **The seal becomes a bypassed checkbox** | An honesty gate in a store the optimizer can also write to is weaker than it looks | Opt-in; advisory mode; be explicit that it raises the cost of *accidental* leakage, not deliberate cheating |
| **Migration dishonesty** | Backfilling through today's lossy exporter produces artifacts that *look* faithful | `provenance.reconstructed: true`; prefer re-import from `extra.origin` |
| **Retrieval claims without a benchmark** | "Better retrieval" is unfalsifiable as stated | Build the benchmark before the work; M12 is the baseline to beat |
| **A second source of truth for acceptance** | cap-evolve is explicit that its DAG is a *view*, not an authority | The store records verdicts; it does not decide them. The harness stays the referee |

**Non-goals.** Not a benchmark runner (Harbor). Not an optimizer (cap-evolve, GEPA, SkillOpt, Arbor,
CORAL). Not a development platform for tasks (Harbor's explicit and correct stance). Not a training
pipeline. Not a replacement for git — a peer that indexes it.

**Open questions for review.**

1. **Digest scope** — is `capability_digest`'s `digest_ignore` store policy per kind, or client-declared
   per artifact? Client-declared matches cap-evolve, but two clients declaring different scopes give one
   artifact two capability identities.
2. **Are workspaces namespaces or a separate axis?** Separate is more machinery but lets one workspace
   pin across namespaces, which `bench/primitives` + `runA/skill` needs. I lean separate.
3. **Who owns tasks and datasets?** Full ownership duplicates Hub; reference-only makes sealed splits
   unusable without Harbor. A thin local kind that can *shadow* a Harbor ref may be the answer.
4. **How much of the weakness graph belongs in the store** versus staying cap-evolve's format, whose
   value is partly that cap-evolve can iterate on it freely?
5. **Sealed splits: refuse or warn by default?** Refusing is the stronger guarantee; warning is safer
   mid-budget.
6. **Trajectory retention** — largest objects, lowest read rate, highest secret risk. TTL by default, or
   keep and rely on tiering?
7. **Is vMCP/vNFS-per-skill still right** once workspaces exist? A workspace-scoped resolver needing no
   restart is strictly better for optimization; per-skill servers may remain right for interactive use.
8. **Does `/v1` keep the global name space forever?** If yes, `/v1` and `/v2` disagree about what a name
   means; if no, every bulk-imported artifact needs a namespace.

---

# Appendices

## Appendix A — sources and measurement method

### A.1 Sources studied

| Source | What was read | Commit |
|---|---|---|
| **skillberry-store** | `src/skillberry_store/**` (51k LOC), `docs/**`, `docs/design/**`, 16 plugins, `access_control_config.yaml` | `c6e72ce` |
| **cap-evolve** | `core/cap_evolve/*` (18.8k LOC), the 22-skill library, `ARCHITECTURE.md`, `ADAPTER_CONTRACT.md`, the SPA intervention (1473-line `spa_env.py`), `capevolve_harbor/` | `c2a4aa6` |
| **Arbor** | `src/coordinator/idea_tree.py`, `tools/git_ops.py`, `src/events/*`, `arbor-zoo/`, `docs/{self-evolution,skills,plugins,outputs-and-resume,zoo}.md` | `7cdaf1f` |
| **Harbor** | `registry.json`, `skills-lock.json`, `docs-mintlify/core-concepts/**` (jobs/skills, hub/{publish,download,leaderboards}, results, datasets/metrics, jobs/regrade), `rfcs/0001-trajectory-format.md` (ATIF v1.8) | `15da91c` |
| **GEPA + gskill** | `core/state.py`, `oa/{ensemble,eval_server}.py`, `proposer/merge.py`, `gskill/**`, the gskill blog + guide, arXiv:2507.19457 | `d771eb2` |
| **SkillOpt** | `README.md`, `docs/sleep/README.md`, `plugins/**` (7 host integrations), `ckpt/**`, arXiv:2605.23904 | `79124b3` |
| **CORAL** | `coral/{hub,workspace}/**`, `concepts/shared-state.mdx`, `guides/multi-agent.mdx`, arXiv:2604.01658 (COLM 2026) | `0123dfb` |
| **AgentEvalHarness** | `README.md`, `agent_eval/mlflow/**`, `docs/{harbor-workflow,eval-train-harbor-nemo-skyrl}.md` | `55357f7` |

**The most valuable single source** was cap-evolve's `spa_env.py`: cap-evolve is already an SBS
consumer, and that file is a 1473-line commented record of what consuming SBS costs in an optimization
loop — pinned to SBS tag `0.2.1`, with defect diagnoses down to file and line. It reads as a bug report
that was never filed. Several of its claims were re-verified at `main`; they still hold.

### A.2 Environment

Installed from the repo into a fresh venv, run as
`SBS_BASE_DIR=/tmp/sbsdata EXECUTE_PYTHON_LOCALLY=True sbs-srv` on port 8000. First start downloads the
~80 MB MiniLM ONNX weights; `/health` returns healthy after encoder warmup.

### A.3 Reproducing M1–M17

- **M1–M5 fidelity** — build a probe skill exercising every case (rich frontmatter incl. `version`,
  `license`, `allowed-tools`, `when_to_apply`, `component`, `provides`, `needs`; a public and an
  `_`-private function; an **executable** `scripts/run.sh`; `.csv`/`.sql`/`.jsonl` references; an
  extensionless `LICENSE`; a binary `assets/logo.png`). Then
  `POST /skills/import-anthropic -F source_type=folder -F folder_path=… -F snippet_mode=file`,
  `GET /skills/{n}/export-anthropic`, unzip, `diff -r`, and compare `stat -c '%a'`. Expect
  `Only in …: assets`, frontmatter reduced to two keys plus a fabricated `license`, and `755` → `644`.
- **M6–M7** — re-import the same directory with any body edit, then
  `GET /skills/?fields=wide` (2 skills, chain formed), `GET /tools/` (6 tools, each name twice), then
  `DELETE /skills/{n}?delete_tools=true&delete_snippets=true` → `200` with empty lists, 18 objects
  surviving.
- **M8** — `curl -D -` on a tool shows no `ETag`/`Last-Modified`; `PATCH` returns 405.
- **M9** — two threads, each `GET /tools/probe?fields=full` → append a distinct tag → `PUT`, with a
  `threading.Barrier(2)` between read and write. Both return 200; the final tag set contains exactly one.
- **M10–M11** — `PUT` with a changed description: same UUID, `parent` null, `tags` wiped. `GET /tools/`
  vs `?limit=10` differ in shape. `POST /snippets/` with a JSON body → 422 (`loc: ["query","content"]`).
- **M12** — two snippets (descriptions "Refund policy note" / "Kubernetes autoscaling guidance"; the
  refund *body* containing "basic economy fare after the 24 hour window"), then
  `/search/snippets?search_term=…&similarity_threshold=2` for four queries. Note the **default**
  threshold of 1 returns nothing for the body query.
- **M13–M15** — create snippets in batches to n = 100/500/1000/2000, timing medians of 5 reads per shape
  and 9 creates, with the store otherwise idle (a concurrent writer invalidates the read numbers — which
  is M16).
- **M16** — 20 timed paged reads idle, then a background thread creating snippets in a loop, then 20
  identical reads: 7.1 ms → 699.5 ms.
- **M17** — all 3,240 corpus pairs, Jaccard over 5-gram shingles of `SKILL.md`.

### A.4 Caveats

Single machine, single client except where stated — absolute latencies are not portable, **ratios** are
the point. An early n=4000 read row was contaminated by a concurrent writer and is excluded; the effect
it revealed is reported cleanly as M16. Encoder cost (~300 ms) was measured with weights already cached.
M7 reproduces a defect cap-evolve documented against SBS `0.2.1`; it was verified at `main` but the
intervening history was not bisected.

## Appendix B — the corpus experiment

Run to test whether deterministic corpus-scale enrichment produces real value (§10.1), and to replace a
single synthetic probe with evidence from artifacts people actually publish.

### B.1 Corpus — 81 skills, 759 files, five independent projects

| Source | Skills | Note |
|---|---|---|
| `anthropics/skills` | 20 | the reference corpus; includes `docx`/`pdf`/`pptx`/`xlsx` |
| `skillberry-ai/cap-evolve` | 26 | `skills/**/SKILL.md` |
| `obra/superpowers` | 15 | the agent set cap-evolve's optimizer registry matches |
| `RUC-NLPIR/Arbor` | 11 | `skills/*/SKILL.md` — its *agent* skills |
| `harbor-framework/harbor` | 9 | `skills/*/SKILL.md` |

Scope note: Arbor has **two** skill systems. The 11 counted are its agent skills (`name` +
`description` only); its *Coordinator* Skills live in `src/skills/` and carry `when_to_apply`, which is
why that key does not appear in the tally below.

### B.2 Method

**(a) Simulation** — a ~90-line analyser replicating SBS's code paths at `c6e72ce`: `read_from_folder`
(UTF-8 only; failures dropped), `parse_skill_metadata` (keeps only `{name, description}`),
`is_code_file` (`.py`/`.sh`/`.bash` → tools), `parse_text_files` with `snippet_mode="file"` (the API
default), `exporter.generate_skill_md` (regenerates frontmatter; injects the `Proprietary` licence when
any path contains "license").

**(b) Live validation** — 12 of the 81 imported into a running SBS and exported again, comparing a
canonical bundle digest — sorted `(relpath, exec-bit, content)` — plus per-file identity, losses and
mode changes. This caught two errors in the simulation (§B.6), so the reported numbers are the live ones
wherever the two disagree.

### B.3 Damage, tiered by severity

Blending these into one percentage would mislead; they differ by an order of magnitude in consequence.

| Tier | Finding | Rate |
|---|---|---|
| **T1 functional** — skill may no longer run | | **16 / 81 (19.8%)** |
| | lost an executable bit on a script it invokes | 15 / 81 (18.5%), **38 files** |
| | lost a non-document binary payload, silently | 2 / 81, **55 files** |
| **T2 legal/semantic** — declared licence replaced by a fabricated `Proprietary` claim | | **17 / 81 (21.0%)** |
| **T3 metadata** — frontmatter keys discarded | | **36 / 81 (44.4%)** |
| any of T1–T3 | | 44 / 81 (54.3%) |

Every lost exec bit sits on a script the skill invokes:
`anthropic-skills` docx(5) pptx(4) skill-creator(7) slack-gif-creator(4) xlsx(2) pdf(1)
webapp-testing(1) web-artifacts-builder(2); `superpowers` brainstorming(2) executing-plans(2)
subagent-driven-development(3) systematic-debugging(1) writing-skills(1); `Arbor` arbor-agent-tools(1);
**`cap-evolve` spa(2)** — cap-evolve's own SBS-integration skill.

Silently dropped payloads, both with `ignored_files: []`: `canvas-design/canvas-fonts/*.ttf` (54 files —
the skill's entire font library) and `web-artifacts-builder/scripts/shadcn-components.tar.gz`.

**T2, verified live.** `anthropic-skills/skills/algorithmic-art` declares
`license: Complete terms in LICENSE.txt`; that `LICENSE.txt` contains the **Apache License Version
2.0**. Round-tripped through a running SBS, the export reads
`license: Proprietary. LICENSE.txt has complete terms`. The store asserts *Proprietary* over Apache-2.0
on Anthropic's own published skill, triggered only by a filename containing "license".

**T3 — the frontmatter the ecosystem actually uses.** 9 distinct keys appear; SBS preserves 2:

| Discarded | Skills | | Discarded | Skills |
|---|:-:|---|---|:-:|
| `allowed-tools` | 19 | | `provides` | 18 |
| `argument-hint` | 19 | | `license` | 17 |
| `component` | 19 | | `sources` | 14 |
| `needs` | 18 | | | |

`needs`/`provides` are cap-evolve's pipeline-token declarations — dropping them destroys the DAG wiring.
`allowed-tools` is a **capability restriction**; silently discarding it widens what an agent may do.

### B.4 Byte fidelity, measured live

| Skill | files in→out | byte-identical | lost | mode changed | digest |
|---|---|---|:-:|:-:|---|
| `pdf` | 12→12 | 10/12 | 0 | 1 | differs |
| `xlsx` | 53→53 | 50/53 | 0 | 2 | differs |
| `skill-creator` | 18→18 | 8/18 | 0 | 7 | differs |
| `web-artifacts-builder` | 5→4 | 0/4 | **1** | 2 | differs |
| `brainstorming` | 8→8 | 5/8 | 0 | 2 | differs |
| `systematic-debugging` | 11→11 | 10/11 | 0 | 1 | differs |
| `create-task` | 1→1 | 0/1 | 0 | 0 | differs |
| `rewardkit` | 1→1 | 0/1 | 0 | 0 | differs |
| `arbor-agent-ideate` | 2→2 | 1/2 | 0 | 0 | differs |
| `arbor-agent-tools` | 4→4 | 2/4 | 0 | 1 | differs |
| `system-prompt` | 7→7 | 5/7 | 0 | 0 | differs |
| `spa` | 5→5 | 2/5 | 0 | 2 | differs |
| **total** | 127→126 | **93/126 (74%)** | **1** | **18** | **0/12 equal** |

`create-task` and `rewardkit` contain only `SKILL.md` and still fail: a folded multi-line YAML
`description` is re-emitted as one long line. Because `SKILL.md` is always *regenerated* rather than
stored, **essentially no real skill round-trips byte-identically.** Text content is otherwise preserved
*modulo a trailing-newline normalization* — files lacking a final newline gain one (e.g. `LICENSE.txt`
11,345 → 11,346 bytes): functionally harmless, fatal to any digest and therefore to Harbor lockfile
agreement.

### B.5 A negative result, reported

Near-duplicate detection over all 3,240 pairs found **0 at ≥ 0.30 Jaccard**. On five independently
curated collections the `dedupe` enrichment has **no yield**. It may earn its keep on a bulk scrape;
that case is untested here and should not be claimed.

### B.6 Two corrections this experiment forced

1. **The `is_text_file` allowlist is not the cause of file loss in the default mode.** In
   `snippet_mode="file"` (the API default) `parse_text_files` processes *every* file, so the
   18-extension allowlist never applies and `.csv`/`.sql`/`.jsonl` survive. The actual dropper is the
   UTF-8-only read, which is why the losses are **binary** files specifically.
2. **Declared `version` in frontmatter is rare in practice** — 0 of 81 skills use it. The hardcoded
   `version: "1.0.0"` on import is a real defect but, on this corpus, low-impact. Reported so it is not
   over-weighted.

### B.7 Reproduction

```bash
for r in anthropics/skills obra/superpowers skillberry-ai/cap-evolve \
         harbor-framework/harbor RUC-NLPIR/Arbor; do
  git clone -q --depth 1 https://github.com/$r.git "corpus/$(basename $r)"
done
find corpus -name SKILL.md -not -path '*/.git/*' | wc -l     # 81
# (a) walk each skill dir applying the code paths in B.2
# (b) per skill: import-anthropic → export-anthropic → unzip → compare
#     sorted (relpath, exec-bit, content) digests
```

Both analysis scripts are short enough to re-derive from B.2 and need only the standard library plus
PyYAML.
