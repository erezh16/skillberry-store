# Artifact Evolution Store: SBS as the Management Layer over a Git-and-Object-Store Complex

**Status:** design evaluation for review. Not an implementation plan.
**Date:** 2026-10-09 · **Revised:** 2026-10-10 — added Part VIII, an evaluation of the combined
direction (survey evidence plane + git-backed per-artifact histories), and §34, the recommended
solution, verified by a prototype ([`artifact_evolution_store_verify/`](artifact_evolution_store_verify/)).
**Companion to:** [`skill_optimization_store.md`](skill_optimization_store.md) ("the survey"). This
document tests a proposed alternative direction against the survey and against the eight projects'
sources. It does not repeat the survey's material. It cites the survey by section (for example
"survey §1.3").

**Labels.** As in the survey: **[read]** means from the named source at the pinned commit (Appendix
A), **[survey]** means established in the survey and not re-derived here, and **[judgement]** means
my opinion, which you should argue with.

---

## Table of contents

- [Part I — Summary](#part-i--summary)
  - [1. The proposed direction](#1-the-proposed-direction)
  - [2. Judgement in brief](#2-judgement-in-brief)
- [Part II — Is this the survey's direction?](#part-ii--is-this-the-surveys-direction)
  - [3. Side-by-side comparison](#3-side-by-side-comparison)
  - [4. Where they agree and where they differ](#4-where-they-agree-and-where-they-differ)
- [Part III — The facts, re-verified](#part-iii--the-facts-re-verified)
  - [5. How each project persists, evolves, links and measures artifacts](#5-how-each-project-persists-evolves-links-and-measures-artifacts)
  - [6. Facts the survey missed or understated](#6-facts-the-survey-missed-or-understated)
- [Part IV — Evaluation of the mental picture (P1–P5)](#part-iv--evaluation-of-the-mental-picture-p1p5)
  - [7. P1 — git as the way artifacts persist and evolve](#7-p1--git-as-the-way-artifacts-persist-and-evolve)
  - [8. P2 — evolutionary lines of immutable, SHA-identified snapshots](#8-p2--evolutionary-lines-of-immutable-sha-identified-snapshots)
  - [9. P3 — cross-linked artifacts at snapshot granularity](#9-p3--cross-linked-artifacts-at-snapshot-granularity)
  - [10. P4 — evidence bound to a snapshot](#10-p4--evidence-bound-to-a-snapshot)
  - [11. P5 — the skill group as an evolving artifact](#11-p5--the-skill-group-as-an-evolving-artifact)
- [Part V — Evaluation of the design principles (D1–D5)](#part-v--evaluation-of-the-design-principles-d1d5)
  - [12. D1 — one git repository per artifact](#12-d1--one-git-repository-per-artifact)
  - [13. D2 — a complex of small repos linked by SHA pointers in file data](#13-d2--a-complex-of-small-repos-linked-by-sha-pointers-in-file-data)
  - [14. D3 — an object store for evidence](#14-d3--an-object-store-for-evidence)
  - [15. D4 — SBS as the critical management layer](#15-d4--sbs-as-the-critical-management-layer)
  - [16. D5 — an SBS API rather than native git](#16-d5--an-sbs-api-rather-than-native-git)
- [Part VI — Value per project, and regression check](#part-vi--value-per-project-and-regression-check)
  - [17. Method](#17-method)
  - [18. Per-project assessment](#18-per-project-assessment)
  - [19. Value summary](#19-value-summary)
- [Part VII — Recommendation](#part-vii--recommendation)
  - [20. What to adopt from the proposal, in what form](#20-what-to-adopt-from-the-proposal-in-what-form)
  - [21. Git features: which ones earn a place, and for what](#21-git-features-which-ones-earn-a-place-and-for-what)
  - [22. Changes this implies for the survey](#22-changes-this-implies-for-the-survey)
  - [23. Open questions](#23-open-questions)
- [Part VIII — The combined direction](#part-viii--the-combined-direction)
  - [24. The combined direction as proposed](#24-the-combined-direction-as-proposed)
  - [25. Judgement in brief](#25-judgement-in-brief)
  - [26. What the new input changes in Parts IV–VI](#26-what-the-new-input-changes-in-parts-ivvi)
  - [27. Point-by-point evaluation](#27-point-by-point-evaluation)
  - [28. The key implementation choice: physical vs logical per-artifact repositories](#28-the-key-implementation-choice-physical-vs-logical-per-artifact-repositories)
  - [29. Required amendments](#29-required-amendments)
  - [30. Coverage of the survey's defects and gaps](#30-coverage-of-the-surveys-defects-and-gaps)
  - [31. Value per project, revisited](#31-value-per-project-revisited)
  - [32. Updated recommendation and sequencing](#32-updated-recommendation-and-sequencing)
  - [33. Open questions raised by the combined direction](#33-open-questions-raised-by-the-combined-direction)
  - [34. Recommended solution](#34-recommended-solution)
- [Appendix A — sources and pinned commits](#appendix-a--sources-and-pinned-commits)
- [Appendix B — citation index](#appendix-b--citation-index)

---

# Part I — Summary

## 1. The proposed direction

Position skillberry-store (SBS) as the **management layer** over a large, complex data store. The
store would hold full evolutionary lines of many cross-linked artifacts, plus the measurement
evidence attached to them.

**The mental picture:**

| | Claim |
|---|---|
| **P1** | Most surveyed projects use git to persist and evolve artifacts (skills, tools, prompt snippets). |
| **P2** | So each artifact has an evolutionary line of immutable, uniquely identified (git SHA) snapshots. |
| **P3** | Artifacts are cross-linked. A skill snapshot links to specific tool and snippet snapshots. |
| **P4** | Evidence is not an artifact and does not evolve, but each measurement set is linked to one specific snapshot. |
| **P5** | Projects develop *sets* of skills for a group of tasks. A **skill group** is a new artifact type that evolves in git, and each group snapshot is a specific set of skill snapshots. |

**The design principles:**

| | Principle |
|---|---|
| **D1** | Each artifact gets its own git repository, so it evolves on its own and can be shared, at different snapshots, by the artifacts that depend on it. |
| **D2** | So one project is a complex of many small git repos whose files contain SHA pointers to other repos. |
| **D3** | Evidence is file or object data linked to artifacts. An object store suits it, since objects are immutable too. |
| **D4** | Humans cannot navigate such a complex. SBS is the critical management layer that makes it usable for humans and optimizer agents, over a backbone built from technologies all the surveyed projects accept. |
| **D5** | So SBS keeps its own artifact API rather than exposing native git. Storing one skill snapshot may mean committing to several repos (the skill, its tools, its snippets). |

## 2. Judgement in brief

**The proposed direction differs significantly from the survey's.** The two agree on the *role*: SBS
as a layer that indexes artifacts and evidence rather than as a file store. They disagree on three
things that determine the architecture:

1. **The backbone.** The proposal makes git the source of truth, with one repo per artifact. The
   survey keeps an SBS-owned, content-addressed bundle as the source of truth, with git as origin and
   mirror.
2. **Identity.** The proposal identifies snapshots by git SHA. The survey uses content digests and
   records git SHAs as provenance only.
3. **The value claim.** The proposal says SBS's value is making a complex structure navigable. The
   survey grounds value in what git *cannot* do (survey §1.3).

**The mental picture, item by item:**

| | Verdict |
|---|---|
| **P1** | **Overstated.** 3 of 8 projects evolve artifacts in git (cap-evolve, Arbor, CORAL), all locally with no remote. Harbor consumes git for skills. GEPA, gskill, SkillOpt and AEH do not use git for artifact persistence. |
| **P2** | **Partly wrong.** Six projects identify artifacts by **content digests they compute themselves**, not by git SHAs. A git commit SHA identifies a whole repo state, not an artifact. Lineage is a **DAG with merges**, not a line. Two projects point at artifacts through *mutable* names: Arbor binds scores to a branch name, and cap-evolve's `best` is `git tag -f`. |
| **P3** | **Real, but in a different shape.** Outside SBS, links between capability artifacts are **by name, resolved at assembly time** (cap-evolve primitives, AEH sub-skills, Harbor skill roots). Snapshot-pinned links live in **separate manifests and lockfiles**, never inside the artifact. Tools and snippets inside a skill are files of one bundle, not separate artifacts. |
| **P4** | **Supported in its core, wrong on "does not evolve".** Evidence binds to a snapshot *plus context* (model, harness, judge, split). It binds to **composites** more often than to single skills. Some evidence relates **two** snapshots. Evidence records have a **lifecycle** (CORAL `pending → scored`, `archived`, `user_best`; Harbor Hub trial rows updated after upload). |
| **P5** | **Supported, and the most valuable new idea here.** Five projects already have a "set of member versions" object: Harbor dataset manifests and trial locks, GEPA candidates, CORAL's `shared_state_hash`, cap-evolve's multi-part candidate. It should generalise beyond skills to a **composition** (prompt, tools, skills, MCP surface, judge). It does not need git to evolve. |

**The design principles, item by item:**

| | Verdict |
|---|---|
| **D1** | **Refute as stated; keep the intent.** No surveyed project uses a repo per artifact. The benefit (an independent version line, shared at different snapshots) comes from content addressing alone. Splitting a skill's tools and snippets into their own repos reintroduces the exact "object graph as truth" defect behind the survey's 0/12 fidelity result (survey §2.3). |
| **D2** | **Refute the form.** Pointers in artifact files force re-versioning that ripples up through every dependent. Commit SHAs differ for byte-identical content, which defeats the evidence cache. Git does not enforce cross-repo pointers, and Harbor cannot consume a raw SHA. Use **content digests in SBS-owned manifests and locks**. |
| **D3** | **Partly support.** An object store suits large, cold, sensitive payloads (trajectories, rollouts, `evidence.jsonl`, bundle blobs). Harbor Hub already does this. It does not suit measurements, which must be queried, or shared mutable state, which needs compare-and-set. Those need a database, as Harbor Hub also shows. |
| **D4** | **Support the role; refute the value claim.** The complexity is created by D1, so "managing it" is not value to any of the eight projects. The claim that the backbone uses technologies "accepted at all the surveyed projects" is false. Value has to be argued per project against a shared git remote (Part VI). |
| **D5** | **Support the own-API part; refute "rather than exposing git".** A git read surface (the survey's mirror, §8.6a) is the zero-change integration for Harbor and the `npx skills` ecosystem. The multi-repo commit problem disappears with content addressing: write the leaves, then move the root pointer last. |

> **Update (Part VIII).** A follow-up proposal combines the survey's evidence plane with git-backed
> per-artifact histories, with git as an internal implementation behind the SBS API. **That combined
> direction makes sense, given six amendments** (§29). The main ones: logical per-artifact
> histories in a shared object database rather than physical repos, and content (tree) identity
> rather than commit SHAs. Under J1, several Part V objections no longer apply (§26). The verdicts
> below are kept as the evaluation of the direction as first proposed.
> **§34** merges the combined direction, the amendments, S1–S3 and the resolved open questions
> into one recommended design.

**Net recommendation [judgement].** Do not adopt the per-artifact-repo backbone. Adopt the proposal's
strongest idea, the **composition**, as a first-class kind in the survey's design:

- an immutable manifest of `(kind, name, content_digest)` pins with `parents[]`;
- evidence bound to the composition's digest;
- a strict separation between *declared* links (by name) and *resolved* links (in a lock).

Back it with three tiers: a content-addressed blob store (object storage is a good fit), a database
index for manifests, measurements and mutable state, and git as origin and mirror (per namespace,
with per-artifact tags). Part VII gives the details. Per project, the composition adds value over the
survey for **CORAL, GEPA, Harbor and cap-evolve**. It adds little for gskill, SkillOpt and AEH, and
nothing for Arbor beyond resolving branch names to commits. The per-artifact-repo backbone would
**regress** cap-evolve and CORAL (Part VI).

---

# Part II — Is this the survey's direction?

## 3. Side-by-side comparison

| Dimension | Survey direction (`skill_optimization_store.md`) | Proposed direction | Same? |
|---|---|---|:-:|
| Role of SBS | "Artifact plane": indexes artifacts and the evidence bound to them (§1.1) | "Critical management layer" over a complex store (D4) | **≈** |
| Source of truth for artifact bytes | SBS-owned immutable, content-addressed **bundle**; tools, snippets and frontmatter are derived projections (§2.3, §8.2) | **Git repositories**, one per artifact (D1) | **✗** |
| Snapshot identity | SBS digests (`content_digest`, `capability_digest`, `harbor_digest`). A git tree hash "equals none of these… recorded as provenance, not used as identity" (§8.2) | Git SHA (P2) | **✗** |
| Explicitly considered alternative | "Make git the source of truth (tree hash = digest), SBS a pure index": rejected as the *only* identity (§4.2, last row) | Essentially this alternative, made more granular | **✗** |
| Git's role | Origin and mirror. "Index and mirror it, not compete with it" (§1.3). One mirror repo per namespace, one directory per skill, a tag per version (§8.6a) | Backbone storage, one repo per artifact (D1–D2) | **✗** |
| Cross-artifact links | `tool_uuids`/`snippet_uuids` become projections of a bundle. Workspaces pin versions (§8.5). "bundle-set/lockfile" is a kind (§8.1) | SHA pointers inside artifact files (D2) | **partial** |
| Higher-level aggregate | Workspace = a named set of pins; "a workspace *is* Harbor's lockfile" (§8.5) | Skill group as a git-evolved artifact (P5) | **partial** |
| Evidence storage | Typed, queryable measurement records plus trajectories, tiered: "measurements are small/hot/queried, trajectories are large/cold/sensitive" (§8.9) | Object store (D3) | **partial** |
| Mutable shared state (budget, running best, live cache) | Central to the value case (§1.3 points 2–3, §10.2) | Not addressed; evidence assumed immutable (P4, D3) | **✗** |
| Where value comes from | What a shared git remote cannot do: seven named limits (§1.3) | Navigating the complexity (D4) | **✗** |
| Native git exposure | Yes, read-only: mirror or smart-HTTP, because Harbor consumes it (§8.6) | No: SBS API instead (D5) | **✗** |
| Own versioned API | Yes, `/v2` (§8.3) | Yes (D5) | **✓** |

## 4. Where they agree and where they differ

**Agreement.**

- SBS should be a management and index layer, not a file server.
- Artifact versions should be immutable.
- Evidence must be bound to an exact version.
- An aggregate above the single skill is needed: the survey's workspace and lockfile, the proposal's
  skill group.
- SBS should have its own API.

**Significant differences.** The proposal is close to the alternative the survey rejected in §4.2
("make git the source of truth"), with a finer grain: one repo per artifact rather than one per
namespace. It also drops the survey's main source of value, shared mutable state (§1.3, §10.2), by
treating evidence as immutable. And it bases the value case on a difficulty the design itself
creates. These are architectural differences, not differences of detail, so the full evaluation
follows.

---

# Part III — The facts, re-verified

All seven external repositories were cloned and read at the survey's pinned commits (Appendix A).
SBS was read at this branch's base (`70137bb`). Every statement below cites a file and line
(Appendix B).

## 5. How each project persists, evolves, links and measures artifacts

### 5.1 Persistence and identity

| Project | What evolves | Persistence | Identity of a snapshot | Uses git for artifacts? |
|---|---|---|---|---|
| **cap-evolve** | A candidate *capability dir*: system prompt and tool code edited together as "one bold, multi-part candidate" | One git repo **per run** (`git init` in the run dir). `git add -A` and commit on every iteration, accepted or rejected. `git tag -f best` on accept. `rollouts/` and `work/` gitignored | `hash_candidate_dir()`, a SHA-256 over content with a declared ignore-set. The git commit is per *run state*, not per candidate | **Yes**, local, one repo per run, no remote |
| **Arbor** | The user's codebase, one **git branch** per idea | Branches in the **user's own project repo**. Idea tree in `.arbor/` (gitignored). Merges with `git merge --no-ff` | `code_ref`, documented as "Git branch name": a **mutable** ref | **Yes**, the user's repo, branches, no push |
| **Harbor** | Tasks and datasets (Hub). Skills are an *input* | Hub: Postgres tables (`job`, `trial` with `rewards`, `status`) plus storage buckets (`packages`, `results`). Skills: fetched from git, `.git` deleted, cached by SHA | Its own `compute_skill_digest` (no mode). Tasks: `sha256:` content hash plus revision plus tags | **Consumes** git (named refs only). The registry itself is DB plus object store |
| **GEPA** | A candidate `dict[str, str]` of named components | `gepa_state.bin` pickle, JSON mirrors, optional `fitness_cache/*.pkl` [survey] | `_candidate_hash()`, SHA-256 over the sorted dict | **No** (no git calls in `src/gepa`) |
| **gskill** | One skill text (`candidate["skills"]`), one per target repo | GEPA run dir; `best_skills.txt` | GEPA's candidate hash | **No** |
| **SkillOpt** | `SKILL.md` and `CLAUDE.md` (Sleep); `ckpt/<bench>/<model>_skill.md` (paper) | Sleep: live files plus staging dir, **immutable backups**, adoption receipts with before/after SHA-256 pins, compare-and-set on the live file. Paper checkpoints: committed to SkillOpt's own GitHub repo | SHA-256 of text or bytes | **Distribution only** (ckpt). Sleep does not use git |
| **CORAL** | The task codebase (agent worktrees); shared skills and notes | Agent worktrees of **one** task repo. `.coral/public/` is a separate local git repo, checkpointed with `git add -A` under `flock` | The worktree commit hash, *plus* `shared_state_hash` = the `.coral/public` checkpoint commit | **Yes**, two local repos, no remote |
| **AEH** | Skills under test; judges | MLflow; CI tags `commit_sha`, `harness_fingerprint` | Skill **by name** in `eval.yaml`. Judge by name [survey §3.8] | Indirectly (CI commit tag) |
| **SBS (today)** | Skills, tools, snippets, MCP servers | One directory per UUID. Optional git via `ShellHook` env commands, one repo for the **whole store**, push per write | Per-object UUID. `PUT` mutates in place [survey M10] | Optional, whole-store repo |

### 5.2 Lineage

| Project | Lineage shape | Where lineage lives |
|---|---|---|
| cap-evolve | DAG: `parents: [str]`, "1 for an edit, 2+ for a merge" | `graph.jsonl`. The **git history is linear**: one commit per iteration on one branch. Git's own parent links do **not** encode candidate lineage |
| Arbor | Tree of ideas, with real git merges into trunk | Idea tree JSON plus git merge commits |
| GEPA | DAG: `parent_program_for_candidate: list[list[...]]` | The pickle |
| CORAL | Single parent: `parent_hash` (code) and `parent_shared_state_hash` (knowledge) | Attempt JSON plus git parents in the task repo |
| SkillOpt | before → after pins per adoption | Adoption receipts plus immutable backups |
| Harbor (tasks) | Revisions, monotonic per package | Hub DB |

### 5.3 Cross-links between artifacts

| Project | Link | How it is bound |
|---|---|---|
| **SBS** | skill → ordered `tool_uuids[]`, `snippet_uuids[]`; tool → `dependencies[]` | By UUID, which is a version id, *if* `PUT` did not mutate in place |
| **cap-evolve (SPA)** | skill tools → the benchmark's **frozen primitives** | "standalone tools in the store that its skill's tools call **by name**" |
| **cap-evolve (library)** | skill → skill pipeline wiring | `needs`/`provides` tokens in frontmatter [survey B.3]: by token, not version |
| **AEH** | evaluated skill → sub-skills | `plugin_dirs` "for sub-skills the evaluated skill calls": by name and directory |
| **Harbor (eval side)** | dataset → tasks | `DatasetTaskRef{name, digest}`: "Task references pin an exact content hash digest" |
| **Harbor (run side)** | trial → skill set | `TrialLock.skills: list[AgentSkillLock{name, source, digest, git_url, git_commit_id}]`, resolved at job time |
| **GEPA** | candidate → component versions | Components inline in the dict. Merge composes component versions from two descendants of a common ancestor |
| **CORAL** | attempt → code commit **and** shared-state commit | Two git SHAs across two repos |
| **Everyone (Agent Skills format)** | skill → its scripts and references | Files inside the same bundle directory |

### 5.4 Evidence binding

| Project | Evidence bound to | Lifecycle |
|---|---|---|
| cap-evolve | `candidate_hash::task_id` (cache) [survey]; gate rows in `graph.jsonl` | Append-only graph; cache rewritten whole [survey] |
| Arbor | Idea node (branch name), `score_split`, `eval_status` [survey] | Node status transitions |
| Harbor | Trial row → `TrialLock` (skill digests, task digest) | Row inserted, then updated (`archive_path` "doubles as the 'finalized' sentinel"; `visibility` updated) |
| GEPA | `(CandidateHash, split, DataId)` [survey] | Legacy keys dropped on load [survey] |
| CORAL | `(commit_hash, shared_state_hash)` | Written `pending` with `score=None`, later filled by the grader; `user_best` set and cleared; `archived` soft-delete |
| SkillOpt | Proposal digest vs live digest | Staged → adopted, with receipt |
| AEH | MLflow run (tags) | MLflow semantics |

## 6. Facts the survey missed or understated

These matter to this evaluation, and §22 suggests correcting the survey where appropriate.

1. **CORAL already binds each attempt to a skill-set snapshot.** `Attempt` carries
   `shared_state_hash` and `parent_shared_state_hash`. The post-commit hook calls `checkpoint()`
   "at submission time (captures agent's current notes/skills)" and stores the returned commit. The
   survey's attempt field list (§5.6) omits both. This is the closest existing instance of the
   proposal's P3 + P4 + P5: evidence → (code SHA in repo A, knowledge SHA in repo B). Two caveats:
   - `checkpoint()` returns `None` when there is nothing to commit, so an attempt submitted when the
     shared state is unchanged records **no** pointer rather than the current HEAD.
   - Nothing in CORAL queries the field. The grader only copies it forward.
2. **Harbor's dataset is a "group of pinned member versions", built without git.** `DatasetManifest`
   is a list of `DatasetTaskRef{name, digest}`. Hub stores packages and results in object-storage
   buckets and queryable state in Postgres rows. The project with the only real registry chose
   **DB + object store** and keeps git upstream: "rather on existing version control platforms and
   then published to the registry, similar to how Docker or PyPI work."
3. **Harbor's skill source is a *directory of skills*.** `_find_skill_dirs` accepts a skill or "a
   root whose immediate child directories each contain SKILL.md", and URL sources default to the
   `skills/` subdirectory. So the natural Harbor unit is **a set of skills at one ref**. A
   one-skill-per-repo layout still works, but needs one `--skill` per repo and the skill under a
   subdirectory.
4. **Arbor binds scores to a mutable branch name.** `code_ref: str | None  # Git branch name`. No
   commit is recorded on the node.
5. **GEPA evolves components individually inside a group.** Reflection mutates components through a
   component selector (round-robin by default), and merge composes per-component versions from two
   lineages. It is the cleanest existing example of "members evolve independently; a group snapshot
   is a choice of member versions". The evidence, though, is bound only to the whole candidate.
6. **cap-evolve's git history is not its lineage.** Commits are linear and include rejected
   iterations. The merge DAG lives in `graph.jsonl`, and `best` is a force-moved tag.
7. **SkillOpt hand-built atomic multi-artifact adoption.** `_write_artifact_batch` publishes "a
   group of artifacts atomically per file, rolling back as a set" with "the manifest last", and
   adoption refuses if "live skill canonical target … changed since staging". This is an existing
   need for D5's multi-artifact write, and it was solved *without* git.

---

# Part IV — Evaluation of the mental picture (P1–P5)

## 7. P1 — git as the way artifacts persist and evolve

**Verdict: overstated. Restate it.**

| Use of git for artifacts | Projects | Count |
|---|---|:-:|
| Evolves artifacts in git | cap-evolve (run repo), Arbor (user's repo, branches), CORAL (task repo worktrees, plus `.coral/public`) | **3** |
| Consumes artifacts from git | Harbor (skills, named refs only) | 1 |
| Distributes via its own GitHub repo, no evolution in git | SkillOpt (`ckpt/`) | 1 |
| No git for artifact persistence | GEPA, gskill, SkillOpt-Sleep, AEH (MLflow; CI commit tag only) | 4 |

Two further facts constrain any design built on P1:

- **No project pushes evolving state to a remote** [survey §1.3, re-confirmed]. Git here is a local
  journal.
- **No project uses git's per-artifact structure.** All three git users keep **one repo per run or
  per project** and many artifacts inside it.

**Suggested restatement.** "Git is the most common single persistence mechanism (3 of 8 for
evolution, 1 more as a consumer). It is always used as one local repo per run or project. Every
project that needs a stable artifact identity computes its own content digest."

## 8. P2 — evolutionary lines of immutable, SHA-identified snapshots

**Verdict: partly wrong. Three corrections.**

1. **The identity is a content digest, not a git SHA.** Six projects have a content digest over an
   artifact (survey §2.1). Five compute their own: cap-evolve `hash_candidate_dir`, GEPA
   `_candidate_hash`, Harbor `compute_skill_digest`, SkillOpt SHA-256 pins, and Arbor's
   protected-path hashes [survey]. Only CORAL uses a git SHA as identity, and it is the SHA of a
   *whole codebase* commit. A git **commit** SHA covers tree,
   parents, author, committer and timestamps, so two byte-identical snapshots committed separately
   get different SHAs. cap-evolve hashes content precisely so that "two byte-identical candidates
   share cache entries under different ids" [survey §5.1]. A git **tree** SHA *is* content-only and
   includes mode. It identifies a directory and could serve as a content address, but it matches no
   digest any consumer computes (survey §8.2).
2. **It is a DAG, not a line.** cap-evolve (`parents`, "2+ for a merge"), GEPA (list of lists) and
   Arbor (git merges) all produce multi-parent lineage. Rejected candidates are snapshots too:
   cap-evolve commits rejected iterations, and GEPA keeps every candidate.
3. **Some pointers to artifacts are mutable names.** Arbor's `code_ref`, the only code pointer on a
   scored node, is a branch name. cap-evolve's `best` is `git tag -f` (its evidence itself is keyed
   by candidate id and hash). Arbor's case breaks P2's immutability exactly where evidence attaches.

**What survives.** Snapshots should be immutable and uniquely identified. Every project agrees, and
the survey already requires it (§8.2).

**Suggested restatement.** "Each artifact has a lineage DAG of immutable snapshots identified by a
content digest. Git commits, where present, are provenance for a snapshot, not its identity."

## 9. P3 — cross-linked artifacts at snapshot granularity

**Verdict: the need is real; the assumed shape is SBS's internal model, not the projects'.**

**Evidence for.**

- SBS's own schema: "A skill is an ordered collection of tools and snippets referenced by their
  UUIDs."
- cap-evolve's SPA skills call shared frozen primitives.
- AEH skills call sub-skills.
- GEPA candidates are compositions of component versions.
- Harbor datasets pin task digests.

**Evidence against the specific shape.**

1. **Outside SBS, capability links are by name, not by snapshot.** cap-evolve's primitives are
   called "by name". AEH resolves sub-skills from `plugin_dirs`. cap-evolve's skill pipeline is
   wired by `needs`/`provides` tokens. Harbor resolves `last-wins` by skill name. Nobody embeds a
   dependency's version identifier inside a skill.
2. **Snapshot-pinned links live outside the artifact,** in a manifest (Harbor `DatasetManifest`) or a
   resolution record (Harbor `TrialLock`, CORAL `Attempt`). The pattern is consistent: **declare by
   name, resolve at assembly, record the resolution next to the evidence.**
3. **A skill's tools and snippets are mostly not separate artifacts.** In the Agent Skills format,
   which every surveyed project uses for skills, scripts and references are files inside the skill
   directory. Splitting them out is the SBS import behaviour the survey measured as the root cause of
   0/12 round-trip fidelity (survey §2.3, M1–M5). Genuinely separate tool artifacts do exist: SBS
   standalone tools, cap-evolve's frozen primitives, MCP tool surfaces. But they are the minority
   case.

**Suggested restatement.** "Artifacts declare dependencies on other artifacts by name. Which
snapshot satisfied each dependency is decided when a composition is assembled, and is recorded in a
lock. Files inside one bundle are not separate artifacts."

## 10. P4 — evidence bound to a snapshot

**Verdict: supported in its core (8 of 8 bind measurements to a version, survey §2.1). Four
corrections.**

1. **Bound to snapshot *plus context*.** Model, harness, judge, dataset and split are part of the
   key. That is the survey's central finding (§1.2): "nothing binds a measurement to an artifact's
   identity *together with its context*." P4 as written leaves this out.
2. **Bound to composites more often than to single skills.**
   - cap-evolve measures the whole candidate dir.
   - GEPA measures the whole dict.
   - Harbor measures a trial with a *set* of skills.
   - CORAL measures (code commit, shared-state commit).

   Per-skill evidence exists only where the skill *is* the whole candidate: gskill, SkillOpt.
3. **Some evidence relates two snapshots.** Paired gates and broke/fixed sets (cap-evolve), with and
   without arms (gskill), Harbor regrade (new verifier × old outputs).
4. **Evidence does evolve, in a narrow sense.**
   - Record lifecycle: CORAL writes `pending` with `score=None`, fills it in later, toggles
     `user_best`, soft-deletes with `archived`. Harbor updates trial rows after upload.
   - Derived and aggregate state is mutable by nature: budgets (`eval_count`), running best, caches,
     recurrence counts.
   - Some knowledge is not bound to a snapshot at all: Arbor findings, CORAL notes, cap-evolve's
     rejected memory are scoped to a project or run.

**Suggested restatement.** "Each measurement is an append-only fact bound to (a composition snapshot,
a context). Records may carry a status lifecycle and mutable labels. Knowledge (findings) and shared
counters are separate kinds with their own scoping."

## 11. P5 — the skill group as an evolving artifact

**Verdict: supported, and the most valuable new element in the proposal.** The survey has the
pieces (workspace, bundle-set/lockfile, §8.1, §8.5) but treats them as views or pins, not as an
evolving, measured artifact with lineage.

**Existing instances of "a group snapshot is a set of member snapshots":**

| Project | Group | Member pins | Evolves? | Evidence bound to group? |
|---|---|---|:-:|:-:|
| Harbor | Dataset | `{name, digest}` per task | yes (revisions) | yes (jobs run datasets) |
| Harbor | Trial skill set | `AgentSkillLock` per skill | per job | yes (trial) |
| GEPA | Candidate | components inline | yes (per-component mutation, merge) | yes, only at group level |
| CORAL | `.coral/public` (skills + notes) | the whole directory, one SHA | yes | yes (`shared_state_hash`) |
| cap-evolve | Candidate dir (prompt + tools) | inline | yes | yes |
| SkillOpt-Sleep | Adopted set (CLAUDE.md + SKILL.md) | before/after SHA-256 | yes | via the gate |

**Corrections.**

1. **Generalise beyond skills.** The groups that are actually measured mix kinds: prompt + tools
   (cap-evolve), arbitrary text components (GEPA), skills + notes (CORAL), CLAUDE.md + SKILL.md
   (SkillOpt). Call it a **composition**, with a skill group as one profile of it.
2. **Group lineage is also a DAG.** GEPA merges compose two lineages' members.
3. **It does not need git.** A group snapshot is a small manifest. Harbor evolves datasets as
   manifests with digests and revisions in a DB. Git adds nothing a manifest with `parents[]` lacks,
   and per-group repos would multiply the repo count further (§12).

---

# Part V — Evaluation of the design principles (D1–D5)

## 12. D1 — one git repository per artifact

**Verdict: refute as stated. Keep the intent (one independent version line per artifact) and
implement it differently.**

**The stated benefit does not require per-artifact repos.** "Evolves on its own and can be shared at
different snapshots" needs two things:

- a per-artifact identity;
- a per-artifact version index.

Content addressing gives both inside any storage layout:

- Git already gives a per-directory content address inside a monorepo (`git rev-parse
  <commit>:<path>`) and per-path history (`git log -- <path>`).
- Harbor gives per-task versioning (digest, revision, tags) with no repo per task.
- Two skills using different snapshots of one tool is just two manifests citing two digests.

**Costs, from the sources.**

1. **No precedent.** None of the eight uses a repo per artifact (§7). The two git consumers of
   skills, Harbor and `npx skills`, are built around a repo with **many** skills under a directory
   (§6.3). One repo per skill means one `--skill` argument per repo.
2. **It reintroduces the defect the survey identified as root.** If a skill's tools and snippets get
   their own repos, the skill bundle is no longer stored as a unit and must be *reassembled* on
   export. That is the "objects are truth, the tree is reconstructed" pattern the survey traced to
   0/12 digest fidelity, 14/81 licence fabrications and 56 silently dropped files (survey §2.2–§2.3,
   App. B).
3. **Repo count grows with granularity.** SBS's default import makes one snippet per file
   (`snippet_mode=Form("file")`). The survey's 81-skill corpus has 759 files. With per-artifact
   repos, importing that corpus alone would create on the order of **800 repos**: one per skill plus
   one per tool or snippet file. Each needs creating, permissions, and a fetch to read. Optimizers
   add artifacts quickly (CORAL agents create skills during a run; GEPA candidates number in the
   hundreds [survey]). **[judgement]** At that granularity git-hosting overhead dominates, and the
   human-navigability problem D4 sets out to solve is created by this choice.
4. **No atomic multi-artifact commit.** Git commits are atomic within one repo only. D5 acknowledges
   this.
5. **It breaks atomic snapshots that work today.** CORAL snapshots all shared state (skills, notes,
   attempts) in **one** `git add -A` under one lock, and the attempt cites that one SHA. Splitting
   skills into their own repos would turn one atomic snapshot into N non-atomic ones. A
   "which knowledge state produced this attempt" pointer would become a vector of N pointers taken
   at slightly different moments.

**Where a per-repo boundary is justified.** Read-access isolation: a reader of a repo can read all
of it (survey §1.3 point 4). Its granularity is the **access boundary**, which is a namespace or
team, not an artifact. The survey's mirror layout is one repo per namespace with per-artifact tags
(§8.6a).

**Recommended form [judgement].**

- One logical version line per artifact: identity = content digest, plus `parents[]` and tags in the
  SBS index.
- Physical git layout per namespace.
- External repos referenced, never absorbed (§20, R3).

## 13. D2 — a complex of small repos linked by SHA pointers in file data

**Verdict: refute the form. Keep the requirement: compositions must pin member snapshots.**

1. **Pointers inside artifact files cause ripple versioning.** If a skill's bytes contain its tools'
   SHAs, every tool edit forces a new skill snapshot, and every skill edit forces a new group
   snapshot. cap-evolve edits tool code every iteration ("edit tool CODE" is a first-class edit), so
   each iteration would mint new versions all the way up the chain. The projects avoid this by keeping
   pins in a **separate** record (Harbor lock and dataset manifest, CORAL attempt). It also changes
   the artifact bytes consumers see: Agent Skills `SKILL.md` has no field for pinned dependencies, and
   adding one breaks the "store frontmatter verbatim" rule (survey §8.2).
2. **Commit SHAs are the wrong pointer for evidence reuse.** They differ for identical content (§8),
   so a cache keyed on them misses whenever the same bytes are re-committed. That defeats the
   evidence cache, which the survey identifies as the largest direct saving (§4, T4).
3. **Git does not enforce cross-repo pointers.** Nothing stops the target repo from being
   force-pushed, garbage-collected or deleted. A SHA in a file is not a retention guarantee.
   Submodules are git's native form of this pattern and still guarantee only the pointer, not that
   the target stays fetchable.
4. **The main consumer cannot use them.** Harbor resolves only **named** refs through `git
   ls-remote` and fails with "No matching ref" for anything else. Every snapshot a consumer must
   fetch needs a tag.

**Recommended form.** Pins are `(kind, name, content_digest)` entries in an **SBS-owned composition
manifest or lock**, with `origin {repo, commit, path}` as provenance. Harbor's
`DatasetTaskRef{name, digest}` is the precedent.

## 14. D3 — an object store for evidence

**Verdict: partly support. Right for payloads, wrong for records and shared state.**

**Fits an object store:**

| Data | Why it fits | Source |
|---|---|---|
| Trajectories (ATIF) | Large, cold, "a bundle, not a document" | survey §5.3 |
| cap-evolve rollouts | Already kept out of git (`rollouts/` gitignored) | `store.py:48` |
| SkillOpt `evidence.jsonl` | Sensitive, needs a retention policy, must not go to git | survey §5.5 |
| Bundle blobs | Content-addressed, deduplicated | survey §8.9 |

Object stores bring per-object ACLs and lifecycle expiry, which address survey §1.3 points 4–5.
**Harbor Hub already does this:** `packages` and `results` buckets for archives and trajectories.

**Does not fit an object store:**

| Data | Why not | Source |
|---|---|---|
| Measurements | Their value is in queries: `agg`, `paired`, `frontier`, `transfer`, `seen`. `seen` is a lookup made before each rollout | survey §8.7 |
| Shared mutable state | Budgets (`eval_count`), running best, cache entries need compare-and-set across writers | survey §1.3 point 2 |
| Record lifecycle | CORAL `pending → scored`, Harbor trial row updates | §10 |

Harbor Hub keeps queryable evidence (rewards, status, config) in **Postgres rows** and uses the
bucket only for payloads, with `archive_path` written last as a "finalized" sentinel.

"Objects are immutable, so evidence fits" rests on P4's "evidence does not evolve", which §10 shows
holds only for raw records.

**Recommended form.** A database for measurement records, manifests, the index and mutable state.
An object store for blobs (artifact bytes and evidence payloads). Records point to payloads by
digest.

## 15. D4 — SBS as the critical management layer

**Verdict: support the role; the value argument as written fails.**

**What is right.** A management layer that indexes and queries artifacts and evidence over a backing
store is the survey's own "artifact plane". It is also the role Harbor Hub plays for tasks.

**What fails.**

1. **The complexity being managed is self-inflicted.** "Practically impossible to navigate" describes
   the D1+D2 backbone, which no project uses. Value has to be measured against what the projects use
   today, and against the fair baseline of a shared git remote (survey §1.3). Simplifying a
   structure that only exists because of this design is not value to any of them.
2. **"Technologies accepted at all the surveyed projects" is false.**
   - Per-artifact git repos: used by none.
   - Git for artifacts: used by 3 (§7).
   - Object stores: used by one, Harbor Hub (through Supabase storage).
   - GEPA uses pickle and JSON, AEH uses MLflow, SkillOpt-Sleep uses files with SHA-256 pins.
3. **The value the survey found does not come from navigation.** It comes from:
   - context-scoped queryable evidence;
   - shared mutable state;
   - live lookup;
   - partial-read authz;
   - retention for sensitive data;
   - safe formats;
   - runtime serving.

   None of these needs the D1/D2 backbone. Several are harder with it, for example atomicity and
   cache keys (§13).

**What survives as a value claim [judgement].** "SBS makes **compositions** and their evidence
first-class and queryable." Questions like "which skill-set snapshot was active for the best
attempts", "which component version persisted across the frontier", or "every trial that ran this
exact skill set" are answerable today only by hand, if at all. Part VI tests this per project.

## 16. D5 — an SBS API rather than native git

**Verdict: support the API, which matches survey §8.3. Refute "rather than exposing native git".
The rationale dissolves under content addressing.**

1. **A git read surface is the zero-change integration** for Harbor (`--skill https://…/tree/<ref>/…`)
   and for the `npx skills` ecosystem (survey §3.2, §8.6). Not exposing it would turn Harbor's
   "0 LOC, works today" into a required adapter, a regression against the survey's plan. The survey's
   answer is to do both: `/v2` as the contract, git as a read-only mirror (§8.11).
2. **The multi-repo commit problem is an artifact of D1.** With content-addressed blobs and
   manifests, publishing a composition means: write the leaf blobs (idempotent by digest), write the
   manifest, then move one pointer under compare-and-set. Unreferenced leaves are garbage. Two
   surveyed projects already use this "root last" pattern without git: SkillOpt ("Callers put the
   manifest last") and Harbor ("`archive_path` doubles as the 'finalized' sentinel"). Over N git
   repos there is no such commit point, and rollback of a pushed commit means a force-push, which
   violates immutability.

---

# Part VI — Value per project, and regression check

## 17. Method

For each project:

- **Today:** what it persists and how, re-verified (Part III).
- **Over the survey:** value the proposed direction adds beyond what the survey already proposes.
  Mainly compositions with lineage, and evidence bound to them.
- **Over a shared git remote:** the fair baseline (survey §1.3).
- **Regression:** a persistence capability the project has today that it would lose by moving to
  the proposed backbone *as stated* (D1–D5).
- **Measure:** how to demonstrate the value, labelled as in survey §3: [definitional], [countable],
  [to-instrument].

## 18. Per-project assessment

### 18.1 cap-evolve

**Today.**

- One git repo per run, a commit per iteration (rejected ones too), `git tag -f best`.
- Lineage DAG in `graph.jsonl`.
- The candidate is one capability dir (prompt + tools), measured as a unit.
- The SPA intervention deploys a skill into SBS next to frozen primitives its tools call by name.

**Adds over the survey.**

- The evidence key gains the **frozen substrate**. Today's cache key `candidate_hash::task_id` covers
  the candidate dir only. The primitives live in SBS outside it, so a cache shared across runs would
  serve hits measured against a different primitive set.
- A composition `{skill@digest, primitives@digest}` makes that key sound. Within one run the
  primitives are frozen, so this matters only across runs.
- Modest, but concrete.

**Over a shared git remote.** cap-evolve commits no primitive identity, so git alone does not close
this.

**Regression under D1–D5 as stated.**

- **Yes.** Splitting a candidate's prompt and tools into separate repos turns one `git diff` per
  iteration into N. The live dashboard shows "per-iteration git diffs" and "the lineage tree".
- `store: command` *replaces* the git backend (survey §9), so the run repo's browsable history is
  lost unless the wrapper also commits.
- The survey's integration has the same exclusivity. The fix is identical: the wrapper commits
  locally, then publishes.

**Measure.**

| Measure | Basis |
|---|---|
| Cache entries whose key covers every artifact that affects the rollout: today 0% across runs (primitives absent), target 100% | [definitional] |
| Rollouts avoided × cost | Unchanged from survey, [to-instrument] |

**Verdict.** The composition adds value. The per-artifact-repo backbone regresses inspectability.

### 18.2 Arbor

**Today.**

- The artifact is a branch of the user's repo.
- The score is bound to the branch *name*.
- Merges are real git merges.
- Experience is deliberately per session.

**Adds over the survey.**

- The proposal's insistence on SHA-pinned snapshots fixes a real gap: record `code_ref → commit` at
  scoring time.
- Today a branch that moves or is deleted after scoring leaves the score attached to code nobody can
  recover.
- This is provenance resolution, which the survey's design supports ("resolved git provenance",
  T1) but did not apply to Arbor.

**Over a shared git remote.** Pushing branches preserves them, but the idea tree still names a
branch, not a commit, so the score is still not bound to the bytes.

**Regression under D1–D5 as stated.**

- **High if SBS tried to own the artifact.** Arbor's artifact is the user's codebase in the user's
  repo, so a per-artifact SBS repo would mean copying it.
- SBS must **reference** `(repo, commit)`, never absorb it.
- With references only: no regression.

**Measure.**

| Measure | Basis |
|---|---|
| Scored nodes whose exact code is recoverable after the run: today 0% guaranteed (branch name only), target 100% | [definitional] |

**Verdict.** Small but clean value from commit resolution. The repo-per-artifact backbone is
inapplicable.

### 18.3 Harbor

**Today.**

- Consumes git skill sources at named refs; a source may be a root of many skills.
- Records a per-trial `TrialLock` with each skill's `(name, digest, git_url, git_commit_id)`.
- Hub = Postgres + buckets; datasets pin task digests.

**Adds over the survey.**

- A trial lock's skill list *is* a skill-group snapshot.
- Importing it as a composition (survey's `jobs:import`, §8.6) gives **group-keyed** queries: every
  trial that ran exactly this skill set, and how the set's score moved across group revisions.
- The survey proposed a skill-keyed join on `(name, digest)`. Group-keyed is a strict extension.

**Over a shared git remote.** Git serves the skills already. It cannot join Hub's lock column to
anything.

**Regression under D1–D5 as stated.**

- **Yes, against the survey's plan.** D5's "not exposing native git" removes the zero-change source
  path.
- D1's one-repo-per-skill turns one `--skill <root>` into one argument per skill.
- D2's raw SHA pointers cannot be passed to Harbor at all.
- Harbor's own persistence is unaffected.

**Measure.**

| Measure | Basis |
|---|---|
| Trials joinable to a composition snapshot: today 0 indexed, target N | [definitional] |
| Harbor LOC changed: 0 | [countable] |

**Verdict.** The composition adds value. The backbone as stated regresses the integration path.

### 18.4 GEPA

**Today.**

- `dict[str, str]` candidates.
- Per-component mutation; merge composes component versions across lineages.
- Pickle state; no git.

**Adds over the survey.**

- Per-component identity and lineage inside a composition. GEPA's merge already reasons "same as
  ancestor" per component in memory, but nothing outside the pickle can query it.
- Stored as compositions of component digests, a run's frontier becomes queryable per component:
  which component versions survive on the frontier, and which component a regression came from.
- **[judgement]** Useful for analysis and cross-run seeding. GEPA does not need it to run.

**Over a shared git remote.** The pickle cannot be shared (survey §3.4). This is the survey's case,
unchanged.

**Regression under D1–D5 as stated.**

- None to GEPA's persistence, since GEPA does not use git.
- Storing short text components as separate git repos would be pure overhead.

**Measure.**

| Measure | Basis |
|---|---|
| Frontier members whose per-component lineage is queryable outside the run: today 0, target all | [definitional] |

**Verdict.** The composition adds analysis value. The backbone is irrelevant to GEPA.

### 18.5 gskill

**Today.** One skill text per target repo, measured with and without, across agents [survey §3.3].

**Adds over the survey.** Little. The skill is the whole candidate. The relevant context link is to
the *target repository* at a commit, which is context, not a composition member. The survey's
transfer matrix covers it.

**Regression.** None. One repo per learned skill is workable for gskill, but users browse catalogues,
and Harbor's default `skills/` root favours one repo with many skills.

**Verdict.** No increment over the survey.

### 18.6 SkillOpt

**Today.**

- Sleep keeps immutable backups, SHA-256 adoption receipts, and compare-and-set against the live
  file.
- It adopts CLAUDE.md + SKILL.md as a set with rollback.
- `evidence.jsonl` is sensitive and stays local.

**Adds over the survey.**

- The adopted set as a composition with lineage (before → after per adoption). This mirrors what
  SkillOpt already writes in receipts.
- Increment: queryable adoption history across users, already in survey §3.6.
- D3's object store with retention fits `evidence.jsonl`, matching survey §1.3 point 5.

**Regression under D1–D5 as stated.**

- **Possible.** CLAUDE.md lives in the user's project, which is often already a git repo the user
  owns. The skill lives under `~/.claude/skills`.
- Per-artifact SBS repos would duplicate a file the user's repo already versions.
- SkillOpt's compare-and-set on the *live* file has no equivalent in git. It must stay in SkillOpt.

**Verdict.** Small increment, already largely in the survey. Reference live files, do not absorb
them.

### 18.7 CORAL

**Today.**

- Attempt → `(commit_hash, shared_state_hash)` plus parent pointers for both, across two local git
  repos.
- The shared-state snapshot is atomic (one `git add -A` under `flock`) but is `None` when nothing
  changed.
- Nothing queries `shared_state_hash`.

**Adds over the survey.** The **largest** increment.

- CORAL already produces composition-bound evidence and lacks only the index.
- SBS indexing attempts by both SHAs answers which knowledge snapshot preceded each improvement, and
  how scores moved as shared skills changed. CORAL's ablation says notes and skills matter (18.6%),
  so these questions are relevant.
- With a content-addressed composition over `.coral/public/skills/*`, per-skill identity comes for
  free while the snapshot stays atomic.
- SBS can also record HEAD where `checkpoint()` returns `None`.

**Over a shared git remote.** Both repos could be pushed and the pointers would resolve. The
remaining gap is query, plus the survey's shared-mutable-state case (`eval_count`, `index.md`).

**Regression under D1–D5 as stated.**

- **Yes.** Per-skill repos split CORAL's one atomic snapshot into N non-atomic ones (§12, cost 5).
- The `flock`, grader and worktree setup assume one filesystem, so moving the shared state into many
  remote repos breaks them.

**Measure.**

| Measure | Basis |
|---|---|
| Attempts with a resolvable knowledge snapshot: today the fraction with non-`None` `shared_state_hash`, target 100% | [countable] on any CORAL run dir |
| Attempts queryable by knowledge snapshot: today 0, target all | [definitional] |

**Verdict.** The strongest fit for the proposal's *picture*, and a regression under its *backbone*.

### 18.8 AgentEvalHarness

**Today.**

- Skill by name.
- Sub-skills via `plugin_dirs`.
- MLflow runs tagged `commit_sha` in CI.

**Adds over the survey.**

- A composition `{skill, sub-skills, judge}` recorded per MLflow run identifies the full closure
  under test.
- In CI today the closure is recoverable only via `commit_sha`, and only if everything lives in that
  one repo.
- Small. Survey §3.8's "weakest case" assessment stands.

**Regression.** None, if SBS federates with MLflow as the survey requires.

**Verdict.** Marginal increment.

## 19. Value summary

| Project | Increment of the *composition* idea over the survey | Effect of the D1–D5 *backbone* as stated | Demonstrable? |
|---|---|---|---|
| **CORAL** | **High**: indexes evidence CORAL already binds | **Regresses** atomic snapshots | yes, countable on existing run dirs |
| **Harbor** | **Medium**: group-keyed trial queries from existing locks | **Regresses** the zero-change integration | yes, definitional |
| **GEPA** | **Medium**: per-component lineage on the frontier | Neutral | yes, definitional |
| **cap-evolve** | **Medium-low**: sound cross-run cache key incl. substrate | **Regresses** per-iteration inspectability | yes, definitional |
| **Arbor** | **Low**: resolve branch → commit at scoring | Inapplicable (must reference) | yes, definitional |
| **SkillOpt** | Low (already in survey) | Risk of duplicating user-owned files | — |
| **AEH** | Marginal | Neutral | — |
| **gskill** | None | Neutral | — |

No project gains from the per-artifact-repo backbone itself. Several gain from the composition model
it was meant to serve.

---

# Part VII — Recommendation

## 20. What to adopt from the proposal, in what form

**[judgement] throughout, grounded in the facts above.**

**R1 — A first-class `composition` kind (from P5).**

- An immutable manifest of `(kind, name, content_digest)` member pins, with `parents[]` and an
  optional `profile` (`skill-group`, `agent-config`, `knowledge-snapshot`).
- Its digest is computed over the canonical member list.
- Measurements cite a composition digest as `subject`, or a single artifact when the artifact is the
  whole subject.
- Precedents: Harbor `DatasetManifest` and `TrialLock`, GEPA candidates, CORAL `shared_state_hash`.
- This generalises the survey's workspace (a *mutable* named set of pins) into the *immutable*
  snapshot a workspace resolves to at evaluation time.

**R2 — Declared links vs resolved links (from P3, corrected).**

- Artifacts declare dependencies by name, optionally with a constraint, in an SBS-side manifest,
  never by rewriting artifact bytes.
- Assembly resolves declarations to digests and records a lock, which is a composition.
- This avoids ripple versioning (§13.1) and matches how every project already binds evidence.

**R3 — Identity = content digest; git = provenance (from P2, corrected).**

- Keep the survey's three digests.
- Record `origin {repo, commit, path}` for anything that came from git.
- Resolve mutable refs at measurement time (Arbor branches, cap-evolve `best`, Harbor already does
  this).
- External repos (Arbor's and CORAL's codebases, gskill's target repos, SkillOpt's CLAUDE.md) are
  **referenced, not absorbed**.

**R4 — A three-tier backbone (from D3, extended).**

1. A content-addressed **blob store**. Object storage is a good fit, and Harbor Hub runs on one. It
   holds artifact bytes and evidence payloads (trajectories, rollouts, `evidence.jsonl`), with
   per-namespace retention and ACLs.
2. A **database** for the index, manifests, measurement records, record lifecycle, labels, and shared
   mutable state with compare-and-set.
3. **Git** as origin and mirror: one repo per namespace, a tag per artifact version, optionally a ref
   per workspace (§21).

This is the survey's §8.9 tiering, made explicit about object storage.

**R5 — Publish = leaves, then root (replaces D5's multi-repo commit).**

- Blobs and member manifests are written idempotently by digest.
- A composition or pointer move is the single compare-and-set commit point.
- This is the survey's atomic publish (B5) and `:apply` (§8.5), with SkillOpt and Harbor as
  precedents.

**R6 — Evidence as facts plus lifecycle (from P4, corrected).**

- Measurement records are append-only facts.
- `status` transitions (`pending → scored`), `archived` and `user_best`-style marks are labels or
  pointer updates with history, never rewrites of the fact.

**Not adopted:**

- per-artifact git repos (D1);
- SHA pointers inside artifact files (D2);
- object store as the *only* evidence store (D3);
- withholding a native git read surface (D5).

## 21. Git features: which ones earn a place, and for what

| Feature | Use case | Evidence | Recommendation |
|---|---|---|---|
| **Tags per artifact version** in the namespace mirror | Harbor needs a named ref for any snapshot it fetches | Harbor `_resolve_sha` via `ls-remote` | **Adopt** (already survey §8.6a) |
| **A branch per workspace or island** in the mirror | Run a candidate set through Harbor with no copy: `--skill …/tree/<ws>/skills` | Harbor's skill root of many skills (§6.3); CORAL islands | **Adopt**, cheap once the mirror exists |
| **Fork** (namespace fork, github-style) | A team or user takes a shared line and evolves it privately while lineage still points upstream, e.g. per-user Sleep evolution of a reference skill | SkillOpt `ckpt/` reference skills plus per-user Sleep; gskill transfer to other agents | **Adopt as an SBS operation** (copy pins, `parents[]` across namespaces), mirrored as a git fork where hosted. Needs no per-artifact repos |
| **Pull request** | Review-then-adopt for one team | SkillOpt "nothing live changes until the user explicitly adopts" | **Adopt via the mirror** for single-team use (survey §8.8) |
| **Merge commits** | Represent multi-parent lineage | cap-evolve, GEPA, Arbor DAGs | **Do not rely on them.** Keep `parents[]` in SBS; cap-evolve's DAG is not in its git history |
| **Submodules** | Cross-repo snapshot pointers | Git's native D2 | **Reject**: the pointer is not a retention guarantee, and no consumer here reads submodules |
| **git notes** for evidence | Attach measurements to commits | — | **Reject**: no query, no context key, awkward to share and merge |

## 22. Changes this implies for the survey

1. **§5.6 (CORAL):** add `shared_state_hash` and `parent_shared_state_hash` to the attempt record,
   and note that CORAL already binds evidence to a knowledge snapshot (§6.1 here). Revise §3.7:
   indexing these pointers is a demonstrable gain on existing run dirs.
2. **§8.1:** promote "bundle-set/lockfile" to a first-class **composition** kind with lineage
   (R1), and make measurement `subject` accept a composition digest.
3. **§8.2:** add declared-vs-resolved links (R2), and resolution of mutable refs at measurement time
   (R3), naming Arbor's `code_ref`.
4. **§8.9:** state the three tiers explicitly (R4). Name Harbor Hub (Postgres + buckets) as the
   precedent.
5. **§4.2, last row:** extend "Make git the source of truth" with the per-artifact-repo variant and
   the reasons in §12–§13 here.
6. **§5.3 (Harbor):** add the dataset manifest's `{name, digest}` pins and the skill-root-of-many
   behaviour.

## 23. Open questions

1. **Composition digest canonicalisation.** Should it cover only member digests, or also member
   *roles* (for example, which component slot in a GEPA dict)? Roles look necessary: GEPA's
   components are named slots.
   **Resolved (§34.17 I5):** roles are part of the composition's tree and canonical manifest, and the digest uses the same rule as any tree.
2. **Granularity of CORAL's knowledge snapshot.** Should SBS treat `.coral/public` as one composition
   (attempts, notes, skills), or split skills from notes? One composition with typed members keeps
   atomicity and still allows per-skill queries.
   **Resolved (I6):** one `knowledge-snapshot` composition (skills + notes); attempts become measurements.
3. **Pins for name-bound dependencies.** When cap-evolve primitives are called by name, who records
   which snapshot answered? The deployer (`:apply`), presumably, but only if every deploy goes
   through it.
   **Resolved (I7):** `:apply` records them in a lock composition; a lock from a deploy that bypassed `:apply` is `unresolved` and excluded from cache hits.
4. **Retention of compositions.** Many compositions are evaluation-time locks that are never reused.
   Should they share artifact retention (forever, since measurements cite them) or measurement
   retention?
   **Resolved (I8):** cited locks are kept by keep-refs; uncited locks expire with their workspace after the gc grace window.
5. **Mirror authority.** This is the survey's open question 9, made sharper: with forks, which side
   wins when a mirrored fork receives a direct push?
   **Resolved (I9):** SBS is authoritative; inbound pushes become proposal branches.

---

# Part VIII — The combined direction

This part evaluates a second proposal: combining the survey's direction (evidence bound to artifacts
and context) with a refined form of the repo-per-artifact direction, used as SBS's persistence
model. Parts I–VII are left as they were, because Part VIII rests on their findings. Where Part VIII
revises a Part V verdict, it says so.

Git mechanics this part relies on were checked on git 2.39.5 **[measured]** (§28.1).

## 24. The combined direction as proposed

The proposal splits responsibilities. The survey's direction supplies **evidence bound to artifacts
and their measurement context**, for reproducibility and for sharing measurements across users and
projects. A repo-per-artifact store supplies **how SBS persists and manages the artifacts**, and is
meant to offer more than today's eight projects need, so that new projects fit too.

The proposal numbers its points R1–R4. They are renamed **J1–J4** here so they do not clash with
§20's R1–R6.

| | Point |
|---|---|
| **J1** | 7 of 8 projects rely on immutable versioned snapshots to give measurements a persistent identity (survey §2.1). Git is one sound way to provide them. If every project goes through the SBS API, git is an implementation detail, not a requirement. |
| **J2.1** | Per-artifact git persistence immediately resolves SBS's core fidelity issue (survey §2.3). Each snapshot is an immutable git version with its own SHA. |
| **J2.2** | A repo per basic artifact (tool, prompt) does not restrict aggregates such as skills. Committing a new skill snapshot may internally commit new snapshots of its tools and prompts. |
| **J2.3** | Projects that evolve tools and prompts individually can share one SBS instance with projects that evolve only skills or skill groups, using the same artifact types and relations. |
| **J2.4** | Immutable snapshots can be shared between concurrent computations, within and across projects. |
| **J2.5** | Composability: from basic elements one can build any aggregate (skill, skill group, or new aggregate types later) on the same foundation. |
| **J2.6** | Sharing: tool-evolving and skill-evolving projects share tools. Git can also have an upstream public presence (GitHub, GitLab). |
| **J3** | Git holds only an artifact's core data (for a tool: script, name, description). The SBS UUID becomes the git SHA. All other metadata (tags, state, …) stays mutable and SBS-managed, bound to the immutable snapshot by its SHA. |
| **J4** | Listing is "heads-only": the HEAD of each artifact's history, rather than every version as a separate UUID. |

## 25. Judgement in brief

**The combined direction makes sense. It is better than either direction alone, provided six
amendments (§29) are made.** Of these, A1 and A2 change the design materially, and A3–A6 make
requirements explicit. In summary:

1. **J1 changes the evaluation materially, and in the proposal's favour.** Part V objected to the
   repo-per-artifact backbone mostly because humans or consumers would face the many-repo complex
   directly (D4, D5) and because pointers lived inside artifact files (D2). With git as an internal
   implementation behind the SBS API, those objections no longer apply. What remains is a sound
   choice: git's object database is a content-addressed, immutable store. It also provides, for
   free, several things the survey otherwise has to build:
   - compare-and-set on version pointers;
   - all-or-nothing multi-pointer updates;
   - branches for concurrent candidates and proposals;
   - exact deduplication;
   - incremental, non-destructive backup;
   - a git read surface for Harbor.
2. **Amendment A1: make each repo logical, not physical.** Give each artifact its own history (its
   own ref namespace) inside a shared object database: one physical repo per namespace or tenant.
   That keeps every benefit claimed in J2. It also restores the atomic multi-artifact commit and
   the cross-artifact referential integrity that physical per-artifact repos lose (§28). Pushing an
   artifact's refs to its own remote still gives it a per-artifact upstream presence (J2.6).
3. **Amendment A2: make the snapshot identity the content (tree) hash, not the commit SHA.** Two
   commits of identical content get different commit SHAs **[measured]**. Binding evidence to commit
   SHAs (J3) would break exactly the cross-run, cross-user sharing of measurements that the evidence
   half of the combined direction exists for.
4. **J2.1 holds only together with the survey's "bundle is the source of truth" decision (A3).** Git
   faithfully stores whatever is committed. The measured defects come from import and export code
   that decomposes and regenerates the skill. Git fixes them only if the skill's original tree is
   committed verbatim and served as committed.
5. **J2.2–J2.6 are supported, with corrections.**
   - Tools and prompts extracted from a skill must be *linked* to the verbatim bundle (same git blob),
     not replace it.
   - Tool identity needs scoped names, with sharing made explicit (A4).
6. **J4 is right as a default, but "HEAD" must mean "promoted", and non-head views must stay
   first-class (A5).** Optimizers act on `best`, frontiers, workspaces and branches, not on HEAD.
7. **The survey's defects.** With A1–A6, the combined direction resolves or simplifies 10 of the 17
   measured defects (M1–M17; 11 once the API is replaced, §34.13) and most of tier T1, and contributes to T2, T3 and T6. It leaves the
   evidence, governance and retrieval work unchanged (§30). T0's import/export fixes become a hard
   prerequisite: committing today's lossy decomposition to git would make the damage permanent.
8. **Value.** Compared with the survey alone, the combined direction adds value for CORAL, GEPA,
   cap-evolve and Harbor, plus SBS's own UI and plugins. It adds little for SkillOpt, gskill and
   AEH, and nothing for Arbor. **With the amendments, no project regresses.** The Part VI
   regressions came from physical repos, pointers inside artifact files, and no git exposure, which
   this design no longer has (§31).

## 26. What the new input changes in Parts IV–VI

| Part V objection | Status under J1 + A1–A6 | Why |
|---|---|---|
| D1 cost 1: no precedent, and consumers expect many skills per repo | **Dissolved** | Consumers read the SBS API or a derived mirror (A6). The internal layout is invisible. |
| D1 cost 2: decomposition reintroduces the fidelity defect | **Remains, with a fix (A3)** | Applies to *any* storage. It disappears once the verbatim tree is the skill's content. |
| D1 cost 3: repo count (~800 for one corpus) | **Dissolved by A1** | 800 ref namespaces in one repo is ordinary for git. 800 physical repos is a hosting problem. |
| D1 cost 4: no atomic multi-artifact commit | **Dissolved by A1** | One physical repo supports all-or-nothing multi-ref transactions **[measured]**. Separate repos do not. |
| D1 cost 5: breaks CORAL's atomic snapshot | **Dissolved by A1** | A composition snapshot is one transaction over many artifact refs. |
| D2.1: ripple versioning | **Reframed** | Pinning *is* the reproducibility requirement. The cost is acceptable once pins live in SBS-owned trees and manifests, not in user-visible files, and new versions are minted only when a composition adopts a new member (§27, J2.2). |
| D2.2: commit SHAs differ for identical content | **Remains, so A2** | Verified **[measured]**. |
| D2.3: git does not enforce cross-repo pointers | **Dissolved by A1 plus keep-refs** | Inside one object database, anything reachable from a ref survives gc. SBS pins what evidence cites (A5). |
| D2.4: Harbor cannot consume raw SHAs | **Remains, handled by A6** | The derived mirror advertises flat, named refs. |
| D3: object store unsuitable for queryable records | **Unchanged** | J3 already keeps metadata outside git. The evidence plane stays a database plus object storage (§20, R4). |
| D4: complexity is self-inflicted | **Dissolved** | Under J1 no human or consumer navigates the repos. |
| D5: not exposing git regresses Harbor | **Dissolved if A6 is kept** | J2.6 already wants an upstream presence. |

## 27. Point-by-point evaluation

### J1 — immutable snapshots via git, as an implementation detail

**Verdict: supported.**

- The count is accurate. "Immutable versioned snapshots" is ✓ for 7 of 8, all but AEH (survey §2.1).
- Git is a sound implementation. Blobs, trees and commits are immutable and content-addressed.
- One property matters for an internal store: **ref updates are compare-and-set.** A ref moves only
  if its current value matches the expected one, and a multi-ref transaction applies all moves or
  none **[measured]**. That is the primitive survey §8.2 calls for ("tags and `latest` are mutable
  pointers moved by compare-and-set").

**What git does not provide** (the survey's work, unchanged):

- query;
- typed evidence;
- context keys;
- partial-read authorisation, which the SBS API must enforce (an internal repo is not an
  authorisation boundary);
- concurrency control on mutable metadata;
- retention policy for sensitive payloads.

**Operational caveats [judgement].**

- Git as a service backend needs care: writers on more than one host need a shared, lock-safe
  filesystem or a single writer service, `gc` must be scheduled, and library access (for example
  libgit2) should replace per-request `git` subprocesses.
- None of this is unusual. Git hosting services run git as a backend.

### J2.1 — git resolves the fidelity issue

**Verdict: necessary-adjacent, not sufficient.**

Git stores bytes and the exec bit exactly. A tree entry records `100755` vs `100644`
**[measured]**, which is exactly what M4 needs. It also stores binaries without decoding, which is
what M3 needs. But the measured defects arise *before* storage, in the import and export code:

| Defect | Cause (survey) | Fixed by git storage alone? | Fixed with A3 (commit the original tree verbatim; export = checkout)? |
|---|---|:-:|:-:|
| M1 0/12 digest fidelity | `SKILL.md` regenerated; trailing newline normalised | ✗ | ✓ |
| M2 licence fabrication | `exporter.generate_skill_md` heuristic | ✗ | ✓ (exporter no longer used) |
| M3 binaries dropped | UTF-8-only `read_from_folder` | ✗ | ✓ (import reads bytes) |
| M4 exec bits | export writes `0644` | partly (git keeps them) | ✓ |
| M5 frontmatter destroyed | `parse_skill_metadata`, `strip_frontmatter` | ✗ | ✓ (`SKILL.md` verbatim; lenient parse for indexing only) |

Committing the *current* decomposed representation (tool objects, snippet objects, regenerated
`SKILL.md`) into git would make M1–M5 **immutable**. Hence T0 and A3 before any migration (§32).

### J2.2 — committing a skill entails committing its tools and prompts

**Verdict: supported, with a correction about what is canonical.**

**The correction (A3).** The skill's snapshot is the full, verbatim bundle tree: every file, path and
mode, including `LICENSE.txt`, fonts and binaries that are neither tools nor prompts. Tool and prompt
artifacts are linked projections of files in that tree, not replacements for them.

**Git makes the link free and verifiable.** A blob's SHA depends only on its bytes. So the
`scripts/run.sh` blob inside skill snapshot `S` and the script blob inside tool snapshot `T` are
*the same object* whenever the bytes are equal. "`S` contains `T`" is checkable by comparing one
hash, with no pointer stored in the skill's files. This keeps Agent Skills bundles byte-identical
to what consumers expect, which matters because `SKILL.md` has no pinned-dependency field (§13).

**Three requirements this creates.**

1. **Classification must be explicit and conservative.** Today `.py`, `.sh` and `.bash` become tools
   and `_mean`, a private helper, became a public tool (survey M5, B.2). A wrong projection no longer
   damages the bundle, but it still advertises a wrong tool surface. Only declared or confidently
   detected entry points should become tool artifacts.
2. **Bidirectional edits.** A tool-centric project commits `T'`. Skills that contain `T` are not
   changed. A new skill snapshot `S'` (with `T'`'s bytes at the same path) is minted only when
   someone adopts `T'` into that skill. This is the "pinned by default" policy reproducibility
   requires. SBS can answer "which skill snapshots contain an outdated `T`" with one query.
3. **Deduplication is exact.** If one of a skill's 9 files changes, one blob and one tool or prompt
   line move, and the rest are shared (survey §8.9).

### J2.3 — mixed granularity in one instance

**Verdict: supported by the facts.**

Element-level evolvers exist:

- cap-evolve's `tools` capability edits tool code, and its `system-prompt` capability edits prompts;
- GEPA mutates individual components through a component selector (§6.5);
- MCP tool surfaces are evolved as a unit (survey §5.1).

Skill-level evolvers exist too: gskill, SkillOpt, CORAL skills.

**Caveat.** In every project that measures, evidence is bound to the composite, not the element
(§10). Per-element histories give per-element *lineage and reuse*. Per-element *credit* is still
inferred: from paired parent/child compositions, or from GEPA's "same as ancestor" merge logic.

### J2.4 — sharing snapshots across concurrent computations

**Verdict: supported.**

- Immutable snapshots can be pinned by many workspaces at once (survey §8.5).
- Concurrent *writers to the same artifact* map naturally to **branches** of that artifact's
  history: each candidate is a branch, and promotion is a compare-and-set fast-forward of HEAD.
- Concurrent writers to *different* artifacts touch different refs and never contend. CORAL today
  serialises all shared-state commits behind one `flock` (§5.1).

### J2.5 — composability

**Verdict: supported, and it is the same conclusion as §11 and §20 R1.**

- A composition (skill group, agent configuration, knowledge snapshot) is itself an artifact with a
  history.
- Its snapshot is a git tree whose entries are the member trees. Git subtrees are referenced by hash,
  so a composition snapshot shares all member objects.
- New aggregate types are new composition profiles. This matches survey §8.1's requirement that
  kinds be open.

### J2.6 — sharing across projects, and upstream presence

**Verdict: supported, with one layout caveat.**

- Cross-project sharing of a tool line works through cross-namespace pins.
- An artifact's history can be pushed to its own remote, a per-artifact upstream.

**The caveat.** Harbor and `npx skills` consume **repositories of many skills** (§6.3), so the
*default* public layout should be a derived per-namespace or per-workspace mirror (A6). Per-artifact
remotes are an option, not the default.

### J3 — git for core data; mutable metadata bound by SHA

**Verdict: supported in principle. It is the survey's "immutable content + mutable,
producer-attributed labels" (§10.1). Three corrections.**

1. **Which SHA (A2).** "SBS UUID becomes git SHA" must mean the snapshot's **content identity**: the
   tree hash, or the survey's `content_digest`. It must not mean the commit SHA.
   - Two commits of one tree differ **[measured]**.
   - Re-import, revert, and two optimizers independently producing the same candidate would all
     mint new identities.
   - The survey's X1 experiment ("how often [two optimizers] independently produce the same
     `capability_digest`") and the shared evaluation cache both depend on equal content having equal
     identity.

   The commit SHA remains the identity of a *version event*: parents, author, time, message. Survey
   §8.2's `capability_digest` and `harbor_digest` are still needed, because neither equals a tree
   hash.
2. **"Core data" must be the artifact's own bytes, not an SBS serialisation of them.**
   - For a skill, `name` and `description` *are* core data, but as they appear in `SKILL.md`, not as
     separate fields. Storing them separately is what destroyed frontmatter (M5).
   - For a standalone tool or prompt with no file format of its own, a small canonical manifest
     (`tool.json` next to the script) is reasonable. Its canonical form must be fixed, or identical
     tools hash differently.
3. **Mutable metadata still needs concurrency control.** Tags, state and labels stay in SBS's
   database. Lost updates there (M9) and the plugin write race (survey §4.1) are not fixed by git,
   so ETag/If-Match and atomic additive label merge (survey T0, T2) remain required.

### J4 — heads-only listing

**Verdict: right as the default view. Incomplete as the only view (A5).**

- It fixes real clutter. Today every version is a separate UUID in listings, and re-import duplicates
  tools by name (M6).
- SBS already keeps `name → HEAD` (`LookupCache`, survey §6.3).

But the projects act on versions that are not "latest committed":

| Project | Version that matters | Is it HEAD? |
|---|---|---|
| cap-evolve | `best` (accepted). Rejected iterations are committed too (`store.py`) | no, unless HEAD moves only on accept |
| GEPA | every frontier member (`program_at_pareto_front_valset`) | no, several are "current" at once |
| CORAL | `user_best`, leaderboard top | no |
| SkillOpt | the adopted skill. Proposals are staged, not live | yes, if HEAD = adopted |
| Workspaces | whatever is pinned in `runA` | no |

So:

- **HEAD must mean "promoted"** (accepted, adopted, approved). Candidates live on branches.
- Listings need ref-aware views: by tag, by workspace, by branch, by frontier query.
- Every snapshot that a measurement cites must stay resolvable even when no branch reaches it. Git
  `gc` eventually prunes unreachable objects, so SBS must hold **keep-refs** for cited snapshots.
  This implements survey §8.2's rule, "yank, never history rewrite."

## 28. The key implementation choice: physical vs logical per-artifact repositories

| Property | Physical repo per artifact (D1 as first proposed) | **Logical**: ref namespace per artifact, one object database per namespace or tenant (A1) |
|---|---|---|
| Independent history per artifact | ✓ | ✓ (`refs/artifacts/<kind>/<name>/…`, histories need not share roots) |
| Atomic commit of a skill plus its tools and prompts | ✗ (atomic only within one repo) | ✓ one ref transaction, all-or-nothing **[measured]** |
| Atomic composition snapshot (CORAL's need) | ✗ | ✓ |
| Dedup of identical files across artifacts | ✗ per repo (unless object alternates are shared) | ✓ one object per content |
| Pointer integrity (a pinned snapshot stays fetchable) | ✗ the other repo may be gc'd, rewritten or deleted | ✓ with keep-refs |
| Repo count for the survey's 81-skill corpus | ~800 | 1 per namespace |
| Per-artifact upstream (GitHub) | native | push that artifact's refs to its own remote |
| Read-isolation boundary | per artifact | per namespace or tenant (survey §1.3 point 4), with the SBS API enforcing finer rules |
| Fork (github-style) | repo fork | copy refs across namespaces; objects are shared or copied by hash |

**[judgement]** Logical per-artifact repos keep everything J2 asks for and avoid the costs in §12.
Keep the physical layout an internal detail (J1), and change it later if scale demands.

### 28.1 What was measured

All on git 2.39.5, in a scratch bare repository:

1. **Same tree, two commits.** Committing the same tree twice (same author and message, one second
   apart) produced two different commit SHAs (`741f3d4…`, `f2fd3a5…`).
2. **Mode is part of the tree.** The tree entry was recorded as `100755 blob … run.sh`, so the exec
   bit is part of the content identity.
3. **Multi-ref transactions are all-or-nothing.**
   - A `git update-ref --stdin` transaction (`start` / `create` × 2 / `prepare` / `commit`) created
     two artifact refs together.
   - A second transaction tried two updates: moving one ref with the correct old value, and creating
     a ref that already existed. It failed at `prepare`.
   - **Neither** ref moved: the first stayed at its old commit.

### 28.2 How separate histories share one repository

A git repository is not "one source tree". It is two things:

- an **object store** of blobs, trees and commits, all addressed by hash;
- a set of **refs**, which are named pointers to commits.

"One tree" is a property of a *checkout*: a working directory shows one commit at a time. SBS needs
no checkout. It writes and reads objects directly through git's plumbing commands, using a **bare
repository** (one without a working directory).

A ref may point to a commit whose history shares nothing with any other ref. Each artifact gets its
own refs, for example `refs/artifacts/tool/compute_score/heads/main`. Its commits hold a tree
containing **only that artifact's files**, with the artifact at the root. Its first commit has no
parent. Familiar uses of the same mechanism:

- orphan branches such as `gh-pages`;
- `refs/notes/*`;
- the `refs/pull/<n>/head` refs GitHub keeps for pull requests.

This is **not** a monorepo with all artifacts in one folder tree and a branch per artifact. In that
layout every commit would still carry every artifact's files, and each branch's history would mix
everything.

A composition (skill, skill group) is a tree whose entries are its members' trees, referenced by
hash, so no files are copied. Pinning a different member version means writing a new composition
tree.

Demonstrated in a scratch bare repo **[measured]**:

```
refs/artifacts/tool/compute_score/heads/main   25fa544 tool v2 ← ca1f33b tool v1
refs/artifacts/prompt/system/heads/main        252f81c prompt v1
refs/artifacts/group/airline/heads/main        tree = {compute_score/ → tool v1's tree, system/ → prompt v1's tree}
git merge-base <tool ref> <prompt ref>          → none (independent roots)
```

**Consequences.**

- An ordinary `git clone` fetches only `refs/heads/*` and `refs/tags/*`, so these refs stay internal.
  Consumers get the derived mirror (A6).
- An artifact can still be published alone by pushing its refs to its own remote, where they become
  that repo's `main`.
- Repository-wide work (garbage collection, repacking) scales with everything stored in the
  namespace's repo. That is one reason the physical layout is per namespace or tenant, not global
  (§33, question 4).

## 29. Required amendments

| | Amendment | Resolves | Grounds |
|---|---|---|---|
| **A1** | Logical per-artifact histories (ref namespaces) in one object database per namespace or tenant | atomicity, integrity, dedup, repo count | §28 [measured]; CORAL's atomic snapshot (§5.1) |
| **A2** | Snapshot identity = content (tree hash or `content_digest`). Commit SHA = version-event id. Evidence and caches keyed by content plus context. Keep `capability_digest` and `harbor_digest` | cross-run, cross-user evidence sharing; survey X1 | §28.1 [measured]; cap-evolve's content hashing "so two byte-identical candidates share cache entries" (survey §5.1) |
| **A3** | A skill's snapshot is its verbatim bundle tree. Tools and prompts are linked projections (same blob), classified conservatively. No exporter regenerates anything | M1–M5 | survey §2.3, App. B |
| **A4** | Scoped artifact names (`<ns>/<skill>/<tool>` by default). Sharing a tool across skills is an explicit pin, not a name collision | M6 duplicate-by-name; SPA's name-bound primitives | survey M6; `spa_env.py:1025-1031` |
| **A5** | HEAD = promoted version. Candidates on branches. Ref-aware listings (tag, workspace, branch, frontier). Keep-refs for every snapshot evidence cites | J4 vs best, frontier, workspaces; gc safety | §27 J4 table |
| **A6** | A derived, read-only git surface: a per-namespace or per-workspace tree in consumer layout (`skills/<name>/`), advertised under flat named refs (Harbor parses exactly one path segment as the ref). Optional per-artifact remotes | Harbor, `npx skills`, J2.6 | `harbor/skills.py:97-100`, `:266-291`; §6.3 |

## 30. Coverage of the survey's defects and gaps

"Resolved" means resolved by the combined direction with A1–A6. "Survey work" means it still needs
the survey's own design work, which the combined direction keeps.

### 30.1 Measured defects (survey §6.5)

| ID | Defect | Status | How |
|---|---|---|---|
| M1 | 0/12 bundle digest fidelity | **Resolved** (A3) | verbatim tree; export = checkout |
| M2 | licence fabrication | **Resolved** (A3) | no regenerated frontmatter. The heuristic must still be deleted in existing releases (T0, §34.16) |
| M3 | silent binary loss | **Resolved** (A3) | bytes in, blobs stored |
| M4 | exec bits lost | **Resolved** | the tree records `100755` [measured] |
| M5 | frontmatter destroyed | **Resolved** (A3) | `SKILL.md` verbatim; lenient index-only parse |
| M6 | re-import duplicates tools | **Resolved** (A2, A4) | equal content = same snapshot, so re-import is a no-op; scoped names |
| M7 | inert cascade delete | **Simplified** | delete becomes yank (ref removal, keep-refs for cited snapshots); dependency order irrelevant for immutable objects |
| M8 | no ETag/PATCH | **Partly**: content side by immutability | metadata side: survey T2 |
| M9 | lost update | **Partly**: refs are compare-and-set [measured] | metadata side: survey T2 |
| M10 | `PUT` mutates in place | **Resolved** | every change is a new commit and snapshot |
| M11 | response shape varies | Resolved by the new API | one response envelope (§34.13) |
| M12 | body unretrievable | Survey work | retrieval (§8.9) |
| M13 | full-scan list | Survey work | externalised index. Heads-only shrinks the default scan |
| M14–M15 | slow search and writes | Survey work | async indexing. Git writes are not the bottleneck |
| M16 | read stall under writes | Survey work | caused by index persistence on the write path |
| M17 | near-dup yield 0 | Unchanged | exact dedup becomes free; near-dup is unrelated |

**11 of 17 resolved or simplified** (M1–M7, M10, M11 and the content side of M8–M9). M11 counts only
because §34.13 replaces the API outright. The other six are index and retrieval work.

### 30.2 Survey tiers (§4)

| Tier | Effect of the combined direction |
|---|---|
| **T0** five defects | Licence, binaries and frontmatter are subsumed by A3 in the new release, and still needed in existing releases (§34.16). The cascade becomes moot. The plugin race is untouched. **T0 becomes a hard prerequisite for migration** (§32). |
| **T1** identity | **Largely delivered by the combined direction:** immutable versions, blobs, dedup, original bytes, tags and refs, git provenance (upstream commits can be preserved on import), and a git surface. `capability_digest` and `harbor_digest` still need computing. |
| **T2** concurrency | Content and pointer side delivered (ref compare-and-set, multi-ref transactions). Metadata ETag/If-Match and async indexing remain. |
| **T3** isolation | Workspaces = sets of ref pins. Candidates = branches. `:apply` = one ref transaction. Namespace isolation is still API-enforced. |
| **T4** evidence | Unchanged (database plus object storage, §20 R4). Bound to content identity (A2). |
| **T5** knowledge | Unchanged. Staged proposals map to branches (SkillOpt, survey §8.8). |
| **T6** scale and ops | Incremental, non-destructive **backup** = `git push` or bundle to another remote (replaces the purging restore, B14). Dedup is free. Index externalisation remains. |
| **T7** governance | Unchanged. The repo is not the authorisation boundary; the API is. |

### 30.3 Capability gaps (survey §7)

| Status | Gaps |
|---|---|
| **Delivered or simplified** | C1 (with A3), C3 (refs, git remote), C4 (atomic publish), C9 in part (workspaces as ref sets; namespaces still API-level), C14 in part (dedup, backup), C22 (proposals as branches) |
| **Unchanged** | C2 kinds; C5–C7 measurements, cache and seals; C10–C13 labels, retrieval, provenance fields, surfaces; C15–C21 authz, frontier, transfer, counterfactuals, budget, migration, judges; C23–C25 |

## 31. Value per project, revisited

Each row compares the combined direction (with A1–A6) against the survey's direction alone, and
checks for regression against what the project persists today.

| Project | Gain over the survey alone | Regression? | Demonstration |
|---|---|---|---|
| **CORAL** | **High.** Shared skills as per-skill histories. Each attempt cites one composition snapshot written in one ref transaction, which keeps CORAL's atomicity and adds per-skill identity. Fixes the `None` pointer when nothing changed (§6.1). Multi-machine sharing without a host-local `flock` | **No**, provided CORAL's local mode stays and SBS is a sync target | Attempts with a resolvable knowledge snapshot → 100% [countable]. Score changes attributable to a specific skill change [definitional] |
| **GEPA** | **Medium.** Components as prompt-artifact histories, candidates as compositions, merges as compositions with two parents whose members come from two lineages. Exactly GEPA's own model (§6.5) | **No** (GEPA has no git) | Per-component lineage of frontier members, queryable outside the run [definitional]. Component reuse across runs [countable] |
| **cap-evolve** | **Medium.** Frozen primitives as shared tool snapshots pinned in every candidate composition: element identity is actually needed here (J2.4, J2.6), and it replaces the client-side `Protection`. Parallel candidates as branches of one skill history. Per-tool lineage across runs | **No**, if the `store: command` wrapper keeps the local run commit. The earlier per-iteration diff concern is gone: SBS diffs two composition trees | Parallel candidates 1 → K, and orphans 18 → 0 (survey). Cross-run cache key covering the primitives 0% → 100% [definitional] |
| **Harbor** | **Medium.** SBS serving git (survey §8.6b) becomes cheap because SBS already *is* a git store; A6 only derives the layout. Group-keyed trial queries via compositions imported from `TrialLock` | **No**, with A6 | `harbor run --skill https://sbs/git/<ns>/tree/<ref>/skills` resolves [countable]. Harbor LOC changed: 0 |
| **SBS itself** | **Medium.** Heads-only listings end version clutter. `skill-optimizer`'s `_optimized(1)` names and `extra.optimization.source_skill_uuid` become versions with parents in one history. `git push` backup replaces purge-on-restore | n/a | Duplicate-named versions in listings → 0 [countable]. Restore without purge [definitional] |
| **SkillOpt** | **Low-medium.** Proposal = branch; adopt = compare-and-set fast-forward of HEAD, the same check as Sleep's "changed since staging" (`staging.py:1416`). Adoption history aggregated across users. `evidence.jsonl` to object storage with retention | **No**, provided the live file stays where it is: SBS cannot compare-and-set a user's live file, so Sleep's own check stays | Sleep proposals indexed across users [definitional] |
| **gskill** | **Low.** Per-repo learned skills with an optional per-artifact upstream (J2.6). Otherwise the survey's catalogue and transfer matrix | No | as survey §3.3 |
| **AEH** | **Marginal.** Judge prompts as prompt-artifact histories. `{skill, sub-skills, judge}` composition per MLflow run | No (federate with MLflow and its Prompt Registry) | as survey §3.8 |
| **Arbor** | **None beyond §18.2.** Arbor evolves the user's codebase, which stays external and referenced. The gain remains resolving `code_ref` to a commit at scoring time | No | as §18.2 |

**Net.** The combined direction's increment over the survey is concentrated where **element-level
identity and atomic compositions** matter: CORAL, GEPA, cap-evolve's primitives, Harbor's git
surface, and SBS's own catalogue. Where a project's unit is one skill or one external codebase,
the evidence half carries the value, as in the survey.

### 31.1 Why some projects show little or no increment

**The table above measures increment, not total value.** Its "gain" column is what the combined
direction adds *on top of the survey's direction*. The combined direction keeps the survey's
evidence plane whole (§24, §32 step 5), so every project keeps the value the survey assigned it.
No project's total value falls to zero:

| Project | Survey value, retained (survey §3) | Increment from the combined direction (§31 table) |
|---|---|---|
| **Arbor** | findings reusable across machines and projects; `distill_abstract` moved off the run budget; contamination fields queryable | none beyond resolving `code_ref` to a commit (§18.2) |
| **gskill** | a catalogue of repo-scoped learned skills with a per-agent transfer matrix, negative cells kept; with/without pairs stored as counterfactuals | low |
| **SkillOpt** | the 52-cell transfer matrix as a live query; Sleep proposals indexed across users; central retention for `evidence.jsonl` | low-medium |
| **AEH** | judges as versioned artifacts; a cross-repo regrade-staleness index; measurements joined to MLflow runs | marginal |

**Why the increment is small for these four.** The combined direction adds three things:

1. element-level identity (tools and prompts with their own histories);
2. atomic compositions;
3. git primitives: compare-and-set refs, branches, a git surface.

Each of these pays off only when a project has **several independently evolving parts inside SBS**.
These four don't:

| Project | Its unit of evolution | Why the three additions don't apply |
|---|---|---|
| **Arbor** | the user's own codebase, as a git branch (§5.1) | Nothing to decompose and nothing SBS owns. As designed, SBS only references it (§18.2). |
| **gskill** | one skill text per target repo (`candidate["skills"]`) | Nothing to decompose. The context that matters, the target repo, is not an SBS artifact today. |
| **SkillOpt** | one learned skill per user (`managed_skill_name`), plus `CLAUDE.md`, both live files outside SBS (§18.6) | Sleep already has its own immutable backups, SHA-256 pins and compare-and-set on the live file. Git adds no new guarantee. |
| **AEH** | skills under test and judges, named in one `eval.yaml` | The judge is the high-leverage artifact. One repo's CI already recovers it through `commit_sha`, and MLflow's Prompt Registry already versions judge prompts (survey §3.8). |

In short, these four projects already have the persistence half the combined direction adds. The
value the survey found for them lies in the evidence half, which is unchanged.

### 31.2 What is missing, and would restore a significant increment

Three additions to the combined direction are grounded in these projects' sources. Each uses the
same git machinery (§28.2), and the first two are demonstrable with a count. **[judgement]** on
significance, as marked.

**S1 — External-repository lines (Arbor, gskill; also CORAL's and cap-evolve's target code).**

*What it is.* A read-only artifact kind for a repository SBS does not own:

- SBS **fetches the referenced commits into the namespace's object store** under
  `refs/external/<repo>/…` and keeps them with keep-refs (A5).
- A composition can pin one as a member, for example `{skill@digest, target@commit}`.
- The user's repo stays authoritative. SBS holds immutable copies of exactly the commits that some
  evidence cites.

*Arbor.*

- Today a score is bound to a branch name (`idea_tree.py:45`).
- Arbor deletes worktrees, not branches. But a `needs_retry` node continues "its preserved branch"
  (`prompts.py:410`), so a branch can move after an attempt.
- With S1, each scored attempt is pinned to an immutable, retained commit. Two idea nodes become
  comparable as a tree diff. The survey's "`code_ref` branches published as versions" (§3.5) becomes
  concrete.
- Measure: scored attempts whose exact scored code is retrievable from SBS, later and on another
  machine: today 0 guaranteed, target 100% [definitional].
- Significance: **moderate**. It makes Arbor's scores reproducible by construction. It does not
  change Arbor's search.

*gskill.*

- A learned skill is defined by its target repository. gskill names the target by its SWE-smith repo
  name (`load_and_split_data(repo, …)`), and the output is a `best_skills.txt` in a run dir.
- With S1, a catalogue skill is the composition `{skill@digest, target@commit}`. As the target repo
  advances, SBS can list skills whose transfer evidence predates the current target commit by N
  commits, and queue them for re-measurement.
- Measure: catalogue skills with a recorded target commit, 0 → all [definitional]. Stale skills
  detected per target advance [countable].
- Significance: **moderate to high** for a public catalogue, because "does this skill still help on
  today's version of my repo?" is the first question a user would ask. *Whether* skills actually
  degrade as targets move is a hypothesis that this feature would let someone test.

**S2 — Evaluator compositions (AEH; also Harbor verifiers, cap-evolve's `score()`).**

*What it is.* An evaluation definition as a composition:

```
{ eval spec (eval.yaml), judge prompts, judge code modules,
  dataset ref, skill under test, sub-skills (plugin_dirs) }
```

Every measurement and MLflow run cites the composition's digest (via `producer.run_ref`, survey §8.7).

- **Regrade-staleness** becomes a structural query: the judge member differs between the cited
  composition and the current HEAD.
- **Judges shared across repos** become one judge line pinned by many evaluator compositions (J2.6),
  rather than copies in each repo's `eval.yaml`.

*AEH.*

- Today the closure under test is recoverable only in CI, through `commit_sha`, and only for files in
  that one repo. Sub-skills under `plugin_dirs` may live elsewhere (`eval.yaml:26`).
- Measure: runs whose full closure (skill, sub-skills, judges, dataset) is attributable, today ≈0%
  directly, target 100% [countable]. Measurements flagged stale when a shared judge changes
  [definitional].
- Significance: **moderate, and only if judges or sub-skills are shared across repos or teams.**
  Within one repo, survey §3.8's "a few lines in AEH" assessment still stands.

**S3 — Forks with upstream tracking (SkillOpt; also gskill).**

*What it is.* Fork an artifact line into another namespace. SBS records the fork point and tracks
divergence: how many commits each side has made since their common ancestor. Upstream versions are
offered to the fork as **staged proposals** (branches), never applied directly.

*SkillOpt.*

- Sleep evolves one learned skill per user and project, and the paper's reference skills are shared
  (`ckpt/<bench>/gpt5.5_skill.md`).
- With S3, a team's base skill is forked per user, and Sleep evolves each fork. SBS then supports two
  operations:
  1. **Upstream → forks:** propose a newer base version to each fork, as a branch that Sleep's own
     held-out gate must pass before the user adopts it. This keeps Sleep's "nothing live changes
     until the user explicitly adopts it."
  2. **Forks → upstream:** surface edits that recur across many forks, matched by identical or
     near-identical diff hunks, as candidates for the base skill.
- The second is the git-native form of the survey's "recurrence aggregated across users" (§3.6).
- Measure: forks per base, upstream proposals adopted, and recurring cross-fork edits found
  [countable].
- Significance: **a hypothesis**. It depends on Sleep being deployed per user against shared base
  skills, which the survey identified as the deployment case for SkillOpt (§3.6) but which is not
  shown in SkillOpt's repo today.

**Considered and not recommended: per-project experience lines for Arbor.** Making each session's
`EXPERIENCE.md` a commit in a per-project history would contradict Arbor's deliberate per-session
scoping ("Experience lives **per session**, not in a global library", `docs/self-evolution.md:71`).
The survey's scoped findings kind already provides opt-in cross-session reuse without that.

**Effect on the §31 table, if S1–S3 are adopted:**

| Project | Increment without S1–S3 | With S1–S3 | Driver | Standing |
|---|---|---|---|---|
| Arbor | none | moderate | S1 | demonstrable |
| gskill | low | moderate to high | S1 (target staleness), S3 | demonstrable; staleness value is a hypothesis |
| AEH | marginal | moderate *if* judges are shared across repos | S2 | conditional |
| SkillOpt | low-medium | medium | S3 | hypothesis |

S1 and S2 fit tiers T1 and T7 respectively (external provenance; judges as artifacts, C21). S3
needs T5's staged proposals first.

## 32. Updated recommendation and sequencing

**[judgement]** Adopt the combined direction with A1–A6. Re-sequence the survey's tiers as follows.

1. **T0 first, unchanged, and now mandatory.** Delete the licence heuristic, read bytes, make delete
   correct-or-`409`, parse leniently, and fix the plugin merge. Without this, migrating into git
   makes today's damage immutable.
2. **T1 becomes "the git-backed artifact store":**
   - one object database per namespace;
   - per-artifact ref namespaces;
   - verbatim bundle trees with linked tool and prompt projections;
   - tree-hash identity plus the survey's three digests;
   - the composition kind;
   - HEAD = promoted, branches, tags, keep-refs;
   - the derived git surface (A6).

   Migration follows survey §8.12: re-import from `extra.origin` where possible, preserving upstream
   commits. Otherwise label reconstructed versions `provenance.reconstructed: true`.
3. **T2:** metadata ETag/If-Match and async indexing. The content side comes from T1.
4. **T3:** workspaces as ref sets and `:apply` as one ref transaction. Smaller than in the survey,
   because T1 supplies the primitives.
5. **T4–T7 unchanged.** The evidence plane (database plus object storage) binds to content identity
   (A2).

**Success measures carried over:**

- bundle digest fidelity 0/12 → 12/12 on the App. B corpus;
- M6 re-import duplicates 3 → 6 tools becomes 3 → 3;
- a two-ref publish where one compare-and-set fails leaves both refs unchanged (the §28.1 test, as a
  regression test);
- Harbor resolves a skill from SBS's git surface.

## 33. Open questions raised by the combined direction

1. **Tree hash or `content_digest` as the primary key?** The tree hash is free and git-native. The
   `content_digest` is independent of git's hash function, and git's own move from SHA-1 to SHA-256
   object formats is still in progress. Keeping `content_digest` primary, with the tree hash indexed,
   avoids tying long-lived identities and evidence keys to git's object format.
   **Resolved (I1, I24):** `content_digest` is primary and the tree hash is indexed; repos use SHA-1 for host compatibility.
2. **How are tools classified?** Declared entry points only, or also detected ones? A wrong
   projection no longer damages the bundle, but it still publishes a wrong tool surface.
   **Resolved (I10):** declared files, plus conservatively detected entry points labelled `detected`, with `_`-private names excluded.
3. **Canonical manifests for standalone tools and prompts.** Agree on one canonical `tool.json` and
   `prompt` layout, or identical artifacts hash differently.
   **Resolved (I11):** `tool.json` + `src/`, and `prompt.md` + `prompt.json`; SBS canonicalises only its own JSON.
4. **Physical layout under scale.** One object database per namespace is the proposal. When does a
   namespace need splitting, and how are cross-namespace pins kept resolvable? (Pin by copying
   objects, or shared object alternates.)
   **Resolved (I12):** one repo per namespace; cross-namespace pins copy objects (T16); oversized namespaces are split by moving lines. The ref-store envelope beyond 200k refs stays open (§34.18 Q1).
5. **Upstream sync direction.** When an artifact has a per-artifact GitHub upstream (J2.6) and
   someone pushes there directly, does SBS import it as a branch (proposal) or fast-forward HEAD?
   This sharpens survey open question 9.
   **Resolved (I9):** inbound pushes become proposal branches; HEAD never moves automatically.

## 34. Recommended solution

This section merges everything above into one design:

- the combined direction (§24);
- amendments A1–A6 (§29);
- additions S1–S3 (§31.2);
- resolutions of the open questions in §23 and §33, and of the survey's §11 questions that concern
  persistence.

The evidence plane is the survey's design (§8.7–§8.10), kept by reference. What is new here is how
artifacts are stored, identified, versioned, composed, retained and published, and how evidence
binds to them.

Every mechanical claim below was tested with a prototype against the survey's 81-skill corpus and
Harbor's own resolution code. 40 of 40 checks pass (§34.15). Two test findings changed the design:

- **I3:** internal repositories must never be served over git.
- **I4:** garbage collection must never prune with a zero grace window.

**Contents of §34:**

| | |
|---|---|
| 34.1 | Ground rules |
| 34.2 | Architecture |
| 34.3 | Artifact model |
| 34.4 | Identity |
| 34.5 | Physical layout |
| 34.6 | Operations and their commit points |
| 34.7 | Projections and dependencies |
| 34.8 | Listing and views |
| 34.9 | Concurrency, consistency and retention |
| 34.10 | Binding evidence |
| 34.11 | The git surface |
| 34.12 | Security |
| 34.13 | API and migration |
| 34.14 | Per-project integration and value |
| 34.15 | Verification |
| 34.16 | Delivery plan |
| 34.17 | Issues and resolutions (decision log) |
| 34.18 | Remaining open questions |
| 34.19 | Mutable state without a database |

### 34.1 Ground rules

| | Rule | From |
|---|---|---|
| **G1** | **One API; git is internal.** The SBS API is the only way to read or write artifacts, metadata and evidence. Internal git repositories are never served over the git protocol. | J1, I3 |
| **G2** | **Bytes are the truth.** A snapshot is an artifact's exact file tree. Nothing is regenerated, normalised or inferred on the way in or out. | survey §2.3, A3 |
| **G3** | **Identity is content.** A snapshot is identified by `content_digest`. A git commit identifies a *version event*, not a snapshot. | A2 |
| **G4** | **Immutable snapshots, mutable pointers, mutable labels.** Content never changes. Pointers (HEAD, named pointers) move only by compare-and-set. Labels and state live in per-snapshot metadata refs in git, under compare-and-set (§34.19). | J3, survey §8.2 |
| **G5** | **Every artifact has its own line; compositions pin members.** Aggregates of any kind are compositions of member snapshots. | J2, P5, A1 |
| **G6** | **Declared vs resolved.** Dependencies are declared by name. The snapshot that satisfied each one is recorded in a lock when the composition is assembled. | §20 R2 |
| **G7** | **Evidence binds to content plus context, and whatever evidence cites is retained.** | survey §1.2, A5 |
| **G8** | **Git outward is derived; git inward is a proposal.** Mirrors are rebuilt from the store. Anything arriving from outside becomes a proposal, never a silent change. | A6 |
| **G9** | **Reference what you don't own.** Foreign repositories are pinned, not absorbed. | S1 |

### 34.2 Architecture

```
  clients:  REST API · SDK · sbs CLI · MCP · UI · vMCP / vNFS · plugins
                                   │
                    ┌──────────────┴──────────────┐
                    │  SBS API  (authz, validation, ETag/If-Match, quotas)
                    └──┬────────────┬─────────────┬───────────────┬──────────┐
             artifact service   evidence        mirror service   indexers   reconciler
             (git plumbing)     service         (git push)       (async)    (rebuilds
                    │               │                │               │       indexes)
        namespace repos (bare,   evidence store   mirror repos     derived indexes
        internal, one per ns):   (append-only     (public, push-   (in-memory; optional
        content + metadata refs   files: folder    populated only)  SQLite file; vector
                                  tree or object                    store)
                                  store)
```
| Store | Authoritative for | Derived from it, and rebuildable |
|---|---|---|
| **Namespace repos** (git, one bare repo per namespace) | snapshot content, version events (commits), line pointers (refs), retention refs, and **mutable metadata**: labels, lifecycle state, line declarations, workspaces (metadata refs, §34.19) | — |
| **Evidence store** (a folder tree; or an S3-compatible object store, same layout) | measurement, finding and run records (append-only files); evidence payloads (trajectories, rollouts, `evidence.jsonl`), content-addressed; budget counters | — |
| **Configuration files** | ACLs, namespace policies (today's approach, `access_control_config.yaml`) | — |
| **Derived indexes** (in memory, optionally persisted to an SQLite file; the vector store) | nothing | ref and digest index, link records, lineage edges, query and facet indexes, search |
| **Mirror repos** | nothing | everything (rebuilt from namespace repos) |

**No database is required** (§34.19). Every authoritative byte lives in git or in plain files. An
SQLite file, if used, is a disposable cache: deleting it costs only a rebuild.

### 34.3 Artifact model

**Kinds.** The registry is open (survey §8.1). Each kind registers four things:

- a layout validator;
- projection rules;
- a default `digest_ignore` scope;
- a schema for its derived labels.

| Class | Kind | Snapshot content | Stored in |
|---|---|---|---|
| Capability | `skill` | the **verbatim** bundle tree: `SKILL.md` and every file, mode-exact | namespace repo |
| Capability | `tool` | canonical layout: `tool.json` plus source files (§34.7) | namespace repo |
| Capability | `prompt` (today's snippet) | `prompt.md` verbatim, plus an optional `prompt.json` | namespace repo |
| Capability | `toolset` | `tools.json` (MCP tool surface) | namespace repo |
| Composition | `composition`, with profiles `skill-group`, `agent-config`, `knowledge-snapshot`, `evaluator`, `lock` | `.sbs-composition.json` plus one subtree per member | namespace repo |
| External | `external-repo` | none of its own. Pinned commits of a foreign repository | namespace repo (`refs/external/…`) |
| Eval | `task`, `dataset` | by reference to Harbor where it exists (survey §8.1, open question 3) | metadata refs (references only) |
| Evidence | `measurement`, `trajectory`, `finding`, `run` | survey §8.7–§8.8 | evidence store |

**Lines and names.**

- An **artifact line** is identified by `<namespace>/<kind>/<name>`, unique per `(namespace, kind)`.
- Projected lines are scoped under their skill (`<skill>/<entry>`, A4). A shared tool is an explicit,
  separately named line.
- Names use git-refname-safe characters, `[a-z0-9._-]` per segment, with `/` used only for scoping.
- For a skill, the line name is the identity. A `name` in `SKILL.md` frontmatter is indexed. If it
  disagrees with the line name, the snapshot gets a `name-mismatch` warning label rather than being
  rejected (I22).

**Refs of a line** (all in the namespace repo; ref scheme verified in T6–T8):

| Ref | Meaning | Moves |
|---|---|---|
| `refs/artifacts/<kind>/<name>/heads/main` | **HEAD = the promoted snapshot** | compare-and-set fast-forward, or a merge commit (§34.6) |
| `refs/artifacts/<kind>/<name>/heads/<branch>` | candidates (`cand/<id>`), proposals (`proposal/<id>`), workspace branches (`ws/<ws>`) | compare-and-set |
| `refs/artifacts/<kind>/<name>/pointers/<p>` | mutable named pointers set by producers: `best`, `user_best`, … | compare-and-set |
| `refs/artifacts/<kind>/<name>/tags/<version>` | immutable version names | create-only |
| `refs/keep/<kind>/<name>/<commit>` | retention for snapshots cited by evidence or by retention policy | create; deleted only by an admin operation |
| `refs/external/<host>/<org>/<repo>/<commit>` | pinned commits of a foreign repository (S1) | create |
| `refs/meta/<kind>/<name>/<digest>` and `…/_line` | mutable metadata of one snapshot (labels, lifecycle state) and of the line (declarations), as commit chains (§34.19) | compare-and-set |
| `refs/meta/workspace/<ws>` | a workspace's pins | compare-and-set |

**Version events.**

- A commit's tree is the snapshot.
- Its parents are the lineage within the line. Several parents mean a merge (cap-evolve, GEPA,
  Arbor).
- The author is the producer and the committer is SBS.
- Lineage across lines is recorded as portable commit trailers, and indexed as typed edges (survey
  §8.2) by the derived index:
  - `Sbs-Derived-From:` for a projection source, a fork origin, or GEPA merge sources;
  - `Sbs-Origin: <url>@<commit>:<path>` for imported upstream content;
  - `Sbs-Producer:`.

**Compositions.**

- A composition snapshot is a tree containing `.sbs-composition.json` plus one subtree per member,
  named by role.
- The manifest is canonical JSON: UTF-8, keys sorted, compact separators, trailing LF.
- It lists `{role, kind, line, content_digest, tree}` per member, sorted by role.
- Role names use GEPA's own component-name character set, `[A-Za-z0-9_.-]` (`state.py:42`).
- Because members are subtrees, a composition **keeps every member reachable**, so no extra retention
  refs are needed (T8a). Writing a composition copies no files.

### 34.4 Identity

| Question | Identifier | Definition | Verified |
|---|---|---|---|
| Is this the same content? (snapshot primary key; evidence subject) | `content_digest` | `sha256` over entries sorted by path bytes. Each entry is `path\0mode\0bytes\0`, with `mode ∈ {100644, 100755}`. A composition's digest uses the same rule over its own tree. | T2, T3 |
| Is this the same capability, ignoring scratch files? (evaluation cache) | `capability_digest[<scope>]` | `content_digest` after removing the paths in a **registered** ignore scope. The scope id is part of the value (`cap:<scope>:sha256:…`), so two clients' scopes never collide (I13) | T14 |
| Is this the same as an upstream directory? (provenance) | git tree hash (indexed) | the snapshot's tree. For a verbatim import it **equals** `git rev-parse <upstream-commit>:<path>` | T1, **T13: 81/81 corpus skills** |
| Which Harbor lock entry is this? | `harbor_digest` | re-implementation of Harbor's `compute_skill_digest` | **T10b: equal for every skill** |
| Which version event? | commit SHA | git commit | T2 (differs for equal content) |
| Which artifact? (stable handle) | line id | `<namespace>/<kind>/<name>`. Snapshots within it are addressed by `@<tag \| pointer \| branch \| content_digest>`. There are no UUIDs | — |

**Object format (I24).** Namespace repos use git's SHA-1 object format, the format git hosts accept
for mirrors. Evidence and caches are keyed by the SHA-256 `content_digest`, which is independent of
git's object format. So git's SHA-1 → SHA-256 transition never touches evidence keys. (Resolves §33
question 1.)

### 34.5 Physical layout

**Namespace repos.**

- One bare, internal git repo per namespace.
- A namespace is the ownership, authorisation, quota and retention boundary (survey §8.5). A tenant
  may own many namespaces.
- Configuration:
  - `gc.auto=0`, so SBS schedules gc;
  - no git daemon or HTTP endpoint (G1, I3);
  - `transfer.hideRefs` on internal refs, as defence in depth only (it is not a confidentiality
    boundary; T9b).
- Measured envelope on one repo (§34.15):

| Operation at 200k refs | Time |
|---|---|
| single-ref compare-and-set | 2.9 ms |
| lookup | 2.6 ms |
| 10-ref transaction | 4.0 ms |
| delete one packed ref | 110 ms |
| `pack-refs` (maintenance) | 31.5 s |

**Workspaces** are a separate axis from namespaces (survey §11 question 2, I14). A workspace is:

- a metadata ref (`refs/meta/workspace/<ws>`, tree `pins.json`) holding a set of pins, each `line → content_digest` (marked `immutable`) or
  `line → ref` (`floating`);
- optional `ws/<ws>` branches;
- a **lock composition** written at every `:apply`, which records what the pins resolved to.

A workspace may pin across namespaces (I12).

**Cross-namespace pins copy objects.**

- The pinned snapshot's objects are copied into the pinning namespace (`pack-objects | index-pack`)
  and given a keep-ref.
- Each namespace is then self-contained and can be deleted independently. A pinned snapshot survived
  deletion of its source namespace (T16).
- Shared object alternates were rejected: they make namespaces mutually dependent for gc and
  deletion.
- Splitting an oversized namespace uses the same copy, then moves the lines' refs and leaves redirect
  entries in the old namespace's metadata. (Resolves §33 question 4.)

**Mirror repos** are separate bare repos, one per published surface (§34.11). They are populated only
by `git push` from a namespace repo. A push transfers only objects reachable from the pushed refs
(T9d).

**Evidence store and derived indexes** as described in §34.19. Evidence payloads are reached only
through the API. On an object store, the API issues short-lived signed URLs (I26).

### 34.6 Operations and their commit points

**Each write has exactly one commit point**: a git ref transaction, or, for evidence, an atomic
file rename. Everything written before the commit point is invisible until it happens. If the operation
fails, those objects are pruned after the gc grace window (I4).

| Operation | Steps | Commit point | On conflict or failure |
|---|---|---|---|
| **Import** (folder, zip, or git URL `repo@ref:path`) | 1. read bytes; 2. validate (§34.12); 3. secret-scan; 4. write blobs with no filters (T15); 5. build tree; 6. compute digests; 7. if `content_digest` equals HEAD, no-op (fixes M6); 8. compute projections; 9. one commit per line with `Sbs-Origin`; 10. labels-at-create as metadata commits | one ref transaction: the skill line, every changed projection line, and their metadata refs (T6a) | any stale ref → nothing moves (T6b), `409` with current heads |
| **Publish candidate** | as import, onto `heads/cand/<id>` | ref transaction | `409` |
| **Promote** | move `heads/main` from the expected old commit: fast-forward, or a merge commit if diverged and the policy allows | compare-and-set (T7: one winner of 8 racing) | `409` with current HEAD |
| **Tag** | create `tags/<v>` | create-only | `409` if it exists |
| **Set pointer** (`best`, `user_best`) | compare-and-set on `pointers/<p>` | compare-and-set | `409` |
| **Compose / lock** | write manifest plus member subtrees; commit on the composition line | ref transaction (with any members created in the same call) | `409` |
| **Apply workspace** | resolve pins → write the lock composition → move `ws/*` refs and the workspace's metadata ref | one ref transaction | ref `409` aborts the whole apply |
| **Propose** (tool-centric edit, upstream change, inbound mirror push, enrichment plugin) | create `heads/proposal/<id>` on the target line | ref create | — |
| **Fork** (S3) | copy objects into the target namespace; first commit's parent = the upstream commit; record `Sbs-Derived-From` | ref transaction | — |
| **Upstream proposal for a fork** | three-way merge of upstream into the fork (`git merge-tree --write-tree`) → a proposal branch | ref create | conflict → proposal marked `conflicted` with the conflict list, never auto-resolved (T17, T17b) |
| **Pin external** (S1) | fetch the foreign commit into `refs/external/…` | ref create | upstream unreachable → `502`, nothing recorded |
| **Record measurement** | ensure a keep-ref for each subject commit (idempotent) → write the record file (temp file, fsync, rename) under the subject's directory and the cache path (§34.19) | atomic rename | a stray keep-ref from a crash is reclaimed by the reconciler if no record cites it |
| **Yank** | lifecycle state `yanked` in the snapshot's metadata ref. HEAD moves back to the previous promoted commit if policy says so. Refs are kept. | one ref transaction (metadata + HEAD) | — |
| **Delete line** (admin) | delete the line's `heads/`, `pointers/` and `tags/`. Keep-refs of cited snapshots stay | ref transaction | — |
| **Redact** (admin; secrets, legal) | rewrite the affected line's history without the offending blob → new commits → delete old refs and keep-refs → mark evidence on affected snapshots `provenance.redacted` with old→new digest mapping → prune that namespace under a maintenance lock | ref transaction, then maintenance gc | — |
| **Mirror sync** | build the derived tree (members are existing trees, no copying; T9) → commit → flat tag and branches → push | `git push` to the mirror repo | retried; the mirror is never authoritative |
| **Backup** | `git bundle create --all` per namespace (content and metadata), plus an incremental copy of the append-only evidence store. Derived indexes are not backed up | — | restore by clone of the bundle plus a file copy; indexes rebuild; non-destructive (T19) |

### 34.7 Projections and dependencies

There are two ways a skill relates to a tool. Both are needed, and they must be kept apart.

**1. Bundled files: projections (A3, J2.2).** A file inside the skill's tree that is a tool or a
prompt gets an *owned* projection line, `<skill>/<entry>`.

- The projection's script blob **is** the skill's blob (T5), so the link is checkable by hash. The
  link record (`skill snapshot, path ↔ projection snapshot`) lives in the derived index and is
  re-verifiable.
- **Skill → projection:** a new skill snapshot that changes the file advances the projection line in
  the **same** ref transaction.
- **Projection → skill:** a tool-centric commit on a projection line (on a branch) creates a
  *proposal* on the skill line that replaces the blob at that path. Adoption is an explicit
  promotion, so pinned compositions never change underneath their users.
- **Classification (I10).** Projections are created for:
  - files declared as tools or prompts, in frontmatter or in the skill line's declarations (metadata ref);
  - entry points detected by the kind's rules (today's `.py`, `.sh`, `.bash`), labelled
    `projection: detected`, and excluding `_`-prefixed private modules (survey M5).

  Other files stay plain bundle files and get no line of their own. A wrong projection affects only
  the advertised tool surface, never the bundle. (Resolves §33 question 2.)
- **Stable projection identity (I17).** A projection's `tool.json` holds only declared, stable fields
  (`entry`, `language`, declared `name`). Derived metadata (parameters parsed from docstrings,
  descriptions, embeddings) are **labels** attributed to the deriver and its version. Upgrading a
  deriver therefore never changes a tool's identity.

**2. Runtime dependencies: declared and resolved (G6).** Some tools are called by name and are not
present in the bundle, such as cap-evolve's frozen primitives (`spa_env.py:1025-1031`).

- The skill **declares** them in its line declarations, kept in a metadata ref (`depends: [{kind: tool, line, constraint}]`).
- **`:apply` resolves them and records the result in the lock composition.**
- A deployment that bypassed `:apply` produces a lock with `resolution: unresolved`. Its measurements
  are excluded from cache hits, because their key is incomplete. (Resolves §23 question 3.)

**Standalone tools and prompts (I11).**

- A tool is `tool.json` plus source files under `src/`. A prompt is `prompt.md` (bytes verbatim)
  plus an optional `prompt.json` (variables, role).
- SBS canonicalises **only its own JSON manifests**: UTF-8, keys sorted, 2-space indent, trailing
  LF. User-authored files are never touched.
- Identical tools therefore hash identically. (Resolves §33 question 3.)

### 34.8 Listing and views

- **Default listing is heads-only** (J4). There is one row per line, showing the HEAD (promoted)
  snapshot, plus counts: versions, open branches, measurements.
- **Other views:**
  - `?at=<tag|branch|pointer|digest>`;
  - `?workspace=<ws>`;
  - `?versions=all` (history);
  - `?state=`;
  - frontier and best-by-metric, which are evidence queries returning snapshot lists (survey §8.7).
- **Promotion policy per namespace or line:**

| Policy | When HEAD moves | Who uses it |
|---|---|---|
| `manual` | only on an explicit promote | — |
| `on-gate` | the producer promotes after its own gate passes | cap-evolve accept, SkillOpt adopt, Arbor merge |
| `on-publish` | every publish promotes | the default for a namespace with no policy set. It keeps today's experience that a write is immediately what everyone sees |

- `best`, `user_best` and the like are **pointers**, not tags: they move, so they must not be
  immutable tags (I16).

### 34.9 Concurrency, consistency and retention

**Concurrency.**

- Content and pointers are protected by ref compare-and-set and all-or-nothing multi-ref
  transactions (T6, T7).
- Each namespace has one writer: an in-process queue, with git's lock files as the backstop. Reads
  run concurrently. Across hosts, one leader per namespace takes writes (I27, untested here).
- Labels and lifecycle state are protected the same way. Each snapshot's metadata ref is updated by
  compare-and-set: the ETag is the metadata commit, and If-Match is the expected old value.
  Additive label merge is a server-side read–merge–CAS loop with retry (survey B3, B4; T20).

**Consistency (I15).**

- Content, pointers and metadata are all in git, so one ref transaction commits them together.
  There is no second store to reconcile for them.
- Evidence is written after its keep-refs (§34.6). A crash between the two leaves only a stray
  keep-ref.
- Derived indexes are updated by the writer after each commit point. Each index records a
  watermark (the refs and evidence files it has seen). A reconciler can rebuild any index from the
  repos and the evidence store at any time. A crash leaves indexes *late*, never *wrong*.

**Retention.**

- A snapshot is retained if it is reachable from any `heads/`, `pointers/`, `tags/`, `keep/` or
  `external/` ref, or is a member of a retained composition (T8a, T8b).
- Unreferenced candidates are pruned (T8c), **unless** the producer's retention policy is `all`. In
  that case every candidate commit gets a keep-ref. cap-evolve needs this, because it audits rejected
  iterations (I23).
- Evaluation-time lock compositions that no evidence cites expire with their workspace, after the gc
  grace window (I8; resolves §23 question 4).

**Garbage collection (I4).**

- SBS runs gc per namespace under a maintenance lock, with git's default prune grace (2 weeks).
- A just-written, unreferenced object survives a default gc (T12), so an in-flight publish is safe.
- **`--prune=now` is forbidden** except for Redact. In testing it deleted the trees of a publish
  that had not yet reached its commit point.

### 34.10 Binding evidence

The measurement record, statuses, queries and seals are the survey's (§8.7, §8.10), with these
bindings:

- **`subject`** is a `content_digest`. Provenance is recorded alongside: line and commit.
- For anything assembled, the subject is the **lock composition's** digest. That makes the frozen
  substrate, sub-skills and shared tools part of the key.
- **`context`** includes the harness, model and agent, plus:
  - the **evaluator composition digest** (S2: judge prompts, judge code, eval spec);
  - the dataset version and split;
  - for repo-scoped artifacts, the **external target commit** (S1).
- **The evaluation cache (`seen`)** is keyed by
  `capability_digest[scope] + task + dataset + split + hash(context)`, with split mandatory (survey
  C24).
- **Every subject a record cites gets a keep-ref before the record is written** (§34.6). A
  measurement therefore can never outlive its subject.
- **Paired and counterfactual records** (`control_of`, broke/fixed) reference two subjects. Both are
  kept.

### 34.11 The git surface

- **Mirrors only** (A6, G1). Internal repos are never served, because under git's default protocol
  v2, any object in a served repo can be fetched by SHA (T9b). This holds even with `hideRefs` set
  and `uploadpack.allow*SHA1InWant=false`. A push-populated mirror holds only what was pushed (T9d).
- **Layouts:**

| Surface | Contents | Refs |
|---|---|---|
| Namespace catalogue | `skills/<name>/` for promoted skills | `main`, plus flat tags `<ns>-r<N>` |
| Workspace view | `skills/<name>/` for the workspace's pins | branch `ws-<name>` |
| Per-artifact upstream (J2.6) | the line's own history | `main`, plus tags `v<N>` |

- Ref names are a single segment from `[a-z0-9._-]`, because Harbor treats exactly one URL path
  segment as the ref (T18).
- **Harbor:** `harbor run --skill <mirror-url>/tree/<ns>-r<N>/skills` was resolved by Harbor's own,
  unmodified `skills.py` (over file transport), finding every skill (T10a). Each skill's digest
  equals SBS's `harbor_digest` (T10b) and the upstream directory's digest (T10c).
- **`npx skills`:** the `.well-known` index serves archives of the **verbatim** tree, so its `sha256`
  covers original bytes. Today it covers a regenerated export (survey §2.2).
- **Inbound (I9).** Pushes to a per-artifact upstream (for example GitHub) are imported by the sync
  job as **proposal branches** on the line, never as moves of HEAD. SBS is authoritative. (Resolves
  §23 question 5, §33 question 5 and survey question 9.)

### 34.12 Security

- **Authorisation** is the API's job, and it is the only boundary (G1). The internal repo is not an
  authorisation boundary (T9b). Mirrors are created per visibility level.
- **Secrets.** Content is immutable, so secrets are scanned **before** the commit point (survey
  §8.10). A secret that gets through is removed only by **Redact** (§34.6), which marks affected
  evidence (I19).
- **Import validation (I18).**
  - Path traversal and absolute paths are rejected.
  - **Symlinks** are rejected and reported. Git can store them, but they can point outside the bundle
    when materialised.
  - **Empty directories** are reported (git cannot store them).
  - Paths that differ only in case are rejected, because they break checkouts on case-insensitive
    filesystems.
  - Size limits apply per file and per bundle. There is no LFS.
  - Every skipped item is listed in `ignored_files` with a reason, which fixes survey M3's silent
    loss.
  - None of these cases occurs in the 81-skill corpus (T13 skipped nothing).
- **Licences** are preserved as bytes. A declared licence is indexed verbatim and never inferred
  (survey T0).
- **Commit metadata** carries producer ids and trailers only, never user content.

### 34.13 API and migration

**One API, with no version label (I20).**

- The recommended solution ships as a **new major release** of skillberry-store with a single,
  unversioned API: no `/v1` or `/v2` path prefixes, and no version negotiation. This replaces
  survey §8.12's plan to freeze `/v1` beside a new `/v2`.
- Users of today's API stay on the existing releases, which remain available. The new release does
  not serve the old endpoints, UUID addressing, or the old response shapes (survey M11).
- After the release, the API evolves **additively**: new fields, kinds and endpoints. A breaking
  change means another major release, under the same rule.
- Everything in §34 uses this one API: resources are addressed by line id plus a selector (§34.4),
  listings are heads-only by default (§34.8), and responses always use one envelope.

**What this costs the consumers that exist today.**

- cap-evolve's SPA/blackbox intervention is pinned to SBS 0.2.1 (survey §3.1). It keeps working
  against that release.
- Moving it to the new release means rewriting the adapter's store calls against the new API. This
  is the survey's seam 2 (§9): one `:apply` call replacing `reset_store_to_skill()`. The ~325
  workaround lines are deleted rather than ported.
- cap-evolve's zero-code seam, `store: command` with the `sbs` CLI (§34.14), targets the new release
  from the start.
- SBS's own UI and plugins move with the release.

**Migration of existing data (I21)** is an offline tool. It reads a store from an existing release
(its data directory or its JSON backup) and writes a new store, after T0:

1. For each skill whose `extra.origin` is re-fetchable, **re-import verbatim** from upstream. The
   resulting tree hash must equal the upstream tree (the T13 property), which **proves** fidelity.
2. Otherwise, reconstruct through the existing exporter and label the result
   `provenance.reconstructed: true` (survey §8.12).
3. Version chains (`parent`) become commits on one line, in order. Tools and snippets become
   projections or standalone lines. Tags become labels on the matching snapshot.
4. Each migrated snapshot carries its old UUID as a `legacy_uuid` label. The new API never accepts it
   as an address, but logs, exports and results that cite old UUIDs can still be traced to the new
   artifact.

### 34.14 Per-project integration and value

This consolidates survey §3 and §9 with §18, §31 and S1–S3.

| Project | Seam on their side | Store features used | Value: survey + combined + S | First demonstration |
|---|---|---|---|---|
| **cap-evolve** | `store: command` wrapper (0 LOC), later the SDK `:apply` | skill line with candidate branches; frozen primitives as shared tool lines pinned `immutable`; one lock composition per candidate; cache by `capability_digest[cap-evolve]`; retention `all`; `best` pointer | atomic apply, parallel candidates, cross-run cache, broke/fixed; primitives in the cache key; per-tool lineage | `spa_env.py` workaround ~325 LOC → 0; orphans 18 → 0; parallel candidates 1 → K |
| **Harbor** | none (mirror URL); `jobs:import` | mirrors, `harbor_digest`, `TrialLock` → lock composition | skill-keyed and group-keyed trial queries; SBS-native skills runnable with no external mirror | `harbor run --skill <sbs-mirror>/tree/<ns>-r1/skills` (T10, file transport) |
| **GEPA** | `optimize_anything` callback | one prompt line per component; candidates as compositions with roles = component names; merges as compositions with two parents | cross-run cache, safe typed state, frontier queries; per-component lineage | frontier members with queryable per-component lineage |
| **gskill** | publish step after a run | skill line per target; external line for the target repo (S1); forks per agent (S3) | catalogue plus transfer matrix; target-staleness detection | catalogue skills with a recorded target commit, 0 → all |
| **SkillOpt** | Sleep's staging target = a proposal branch | skill line, proposals, compare-and-set promote, forks (S3), object store for `evidence.jsonl` | live transfer matrix; proposals across users; retention; fork/upstream | Sleep proposals indexed across users |
| **CORAL** | `.coral/public` sync target | per-attempt `knowledge-snapshot` composition (skills + notes) in one transaction; attempts → measurements; external line for task-repo commits | cross-run notes and attempts, multi-machine, one budget; per-skill identity with an atomic snapshot | attempts with a resolvable knowledge snapshot → 100% |
| **Arbor** | a plugin for findings and `code_ref` | findings; external lines pinning each scored commit (S1) | findings library, mining off the budget; reproducible scored code | scored attempts whose code is retrievable → 100% |
| **AEH** | judges published; runs cite an evaluator composition | prompt lines for judge prompts; evaluator compositions (S2); MLflow `run_ref` | judge versions, regrade index; closure attribution and shared judges | runs with full closure attributable → 100% |
| **SBS itself** | — | heads-only views, versioned optimizer output, bundle backup | fidelity, no version clutter, non-destructive restore | duplicate-named versions in listings → 0; App. B fidelity 0/12 → 12/12 |

**CORAL's knowledge snapshot (I6).** `.coral/public` mixes knowledge with evidence. SBS stores
attempts as measurements in the evidence store and puts skills and notes in the `knowledge-snapshot`
composition. Each attempt's record cites the composition digest, which replaces `shared_state_hash`.
The `None`-when-unchanged gap (§6.1) disappears, because the current composition is always citable.
(Resolves §23 question 2.)

### 34.15 Verification

The prototype and its checks are committed at
[`artifact_evolution_store_verify/`](artifact_evolution_store_verify/) (`gitstore.py`,
`test_design.py`, `test_nodb.py`). Run with `CORPUS=<clones> python3 test_design.py` and
`python3 test_nodb.py`.

**Environment:**

- git 2.39.5, Python 3.11.2, one host.
- Corpus commits: `anthropics/skills` `dbd4588`, cap-evolve `c2a4aa6`, Arbor `7cdaf1f`, Harbor
  `15da91c`, `obra/superpowers` `bb92a77`.
- **Result: 34/34 pass** (`test_design.py`), plus **6/6** (`test_nodb.py`).

| Check | Claim | Result |
|---|---|---|
| T1 | verbatim import of 6 Anthropic skills (incl. 54 fonts, a tarball, exec scripts) gives exactly the upstream tree hash | pass |
| **T13** | the same for **every** `SKILL.md` directory in the survey's five-repo corpus (files compared are those upstream tracks; the clones were clean) | **81/81** |
| T2 | identical content: same tree, different commit SHAs. `content_digest` is order-independent | pass |
| T3 | the exec bit changes both the tree hash and `content_digest` | pass |
| T4 | 54 `.ttf` fonts and a `.tar.gz` round-trip byte-exact | pass |
| T15 | CRLF and a missing final newline are stored byte-exact (no filters) | pass |
| T5 | a projected tool's script blob is the same object as in the skill | pass |
| T6a, T6b | multi-line publish commits together; one stale compare-and-set aborts all | pass |
| T7 | 8 concurrent promotions from one base: exactly 1 wins | pass |
| T8a–c | a composition keeps its members through gc; a keep-ref keeps a branchless cited snapshot; an unreferenced snapshot is pruned | pass |
| T12 | default gc keeps a fresh unreferenced object (in-flight publish) | pass |
| T9a | `transfer.hideRefs` hides internal refs from `ls-remote` | pass |
| **T9b** | **finding:** under protocol v2 a hidden ref's commit *is* fetchable by SHA from the internal repo | confirmed; also with `uploadpack.allow*SHA1InWant=false` (manual run) |
| T9c, T9d | protocol v0 refuses it; a push-populated mirror does not contain it | pass |
| T10a–c | Harbor's unmodified resolver finds all 6 skills at a flat mirror tag; Harbor's digest = SBS `harbor_digest` = the upstream directory's digest | pass |
| T18 | Harbor parses one path segment as the ref (`tree/ws/runA/skills` → ref `ws`) | pass |
| T11 | 20k lines: list 24 ms; 10-ref transaction 4 ms | pass |
| — | 200k refs (manual run): compare-and-set 2.9 ms, lookup 2.6 ms, 10-ref transaction 4.0 ms, delete 110 ms, `pack-refs` 31.5 s, `packed-refs` 13.8 MB | measured |
| T14 | `capability_digest` equal under a declared ignore scope; `content_digest` differs | pass |
| T16 | a cross-namespace pin (object copy) resolves after the source namespace is deleted | pass |
| T17, T17b | an upstream edit merges cleanly into a diverged fork; a conflicting edit is detected, not auto-resolved | pass |
| T19 | `git bundle --all` backup restores every ref, incl. `refs/artifacts` and `refs/keep`, identically | pass |
| T20–T23 | mutable state without a database: concurrent metadata compare-and-set, label write latency, content-addressed cache lookup, per-subject and cross-subject scans, locked counters (`test_nodb.py`, 6/6) | pass; numbers in §34.19 |

**Not tested here:**

- smart-HTTP transport (Harbor ran over `file://`, using the same `upload-pack`);
- multiple hosts and leader election;
- libgit2 in place of the CLI;
- the reconciler and persisted derived indexes (not built; their underlying file and ref mechanics are T20–T23);
- incremental bundles.

These are implementation-phase checks (§34.16).

### 34.16 Delivery plan

| Phase | Content | Exit criteria |
|---|---|---|
| **0 = survey T0**, in the **existing** release | delete the licence heuristic; read bytes; correct-or-`409` delete; lenient parse; atomic plugin label merge. Users who stay on existing releases need these fixes, and they make the migration tool's reconstruction path (§34.13 step 2) less lossy | survey T0 measures |
| **1 — store core** | namespace repos; verbatim import; the four digests; lines and refs; projections; compositions; keep-refs; gc policy; the unversioned API with heads-only listing; the migration tool | App. B fidelity **0/12 → 12/12**; T13 **81/81** in CI; M6 re-import **3 → 3** tools; T6b, T7, T8, T12 as regression tests |
| **2 — consistency and git surface** | metadata refs with ETag/If-Match; atomic file writes everywhere (I29); reconciler; mirror service; `npx` archives of verbatim trees | lost update → `412`; reconciler rebuilds every index from repos and evidence alone; Harbor resolves from an SBS mirror over HTTPS |
| **3 — workspaces** | pins, `:apply` with lock compositions, candidate branches, pointers, promotion policies; cap-evolve seam | `spa_env.py` workaround LOC → 0; parallel candidates 1 → K |
| **4 — evidence (survey T4)** | measurements bound to `content_digest` and lock compositions; keep-ref-before-insert; cache | reproduce cap-evolve's published numbers from store queries; report rollouts avoided × cost |
| **5 — S1–S3 and knowledge (survey T5)** | external lines, evaluator compositions, forks with upstream proposals; findings | the §31.2 measures |
| **6 — scale and governance (survey T6–T7)** | leader per namespace; replicas; redact; seals; quotas | ref-store envelope at 10⁶ refs per namespace (§34.18 Q1); ACL conformance |

### 34.17 Issues and resolutions (decision log)

| # | Issue | Resolution | Grounds |
|---|---|---|---|
| I1 | Which hash identifies a snapshot? | `content_digest` is primary. The tree hash is indexed. The commit is the version event. | A2; T2 |
| I2 | Physical or logical repo per artifact? | Logical: ref namespaces per line, in one bare repo per namespace | A1; §28 |
| I3 | Internal objects are fetchable by SHA under protocol v2 despite `hideRefs` | Never serve internal repos. Mirrors are separate and push-populated | **T9b–d** |
| I4 | gc with `--prune=now` deleted a not-yet-committed publish | Default grace window (2 weeks). `--prune=now` only for Redact, under a maintenance lock | **T8c, T12** |
| I5 | Composition digest canonicalisation and roles (§23 Q1) | Roles are part of the composition tree and manifest. The digest uses the same rule as any tree | §34.3 |
| I6 | CORAL snapshot granularity (§23 Q2) | One `knowledge-snapshot` composition (skills + notes). Attempts become measurements | §34.14 |
| I7 | Who records pins for name-bound dependencies (§23 Q3)? | `:apply` writes the lock. Bypassing it gives `unresolved`, which is excluded from cache hits | §34.7 |
| I8 | Retention of compositions (§23 Q4) | Cited locks are kept by keep-refs. Uncited locks expire with their workspace after the gc grace | §34.9 |
| I9 | Mirror authority and direct pushes (§23 Q5, §33 Q5, survey Q9) | SBS is authoritative. Inbound pushes become proposal branches | §34.11 |
| I10 | Tool classification (§33 Q2) | Declared files, plus conservatively detected ones labelled `detected`. `_`-private names excluded | §34.7; survey M5 |
| I11 | Canonical layouts for standalone tools and prompts (§33 Q3) | `tool.json` + `src/`; `prompt.md` + `prompt.json`. SBS canonicalises only its own JSON | §34.7 |
| I12 | Physical layout under scale (§33 Q4) | Per namespace. Cross-namespace pins copy objects. Split by moving lines | **T16**; measured envelope |
| I13 | `capability_digest` scope: store policy or client-declared (survey §11 Q1)? | Registered scope ids. A kind's default scope, or a client-registered scope, named inside the digest value | **T14** |
| I14 | Workspaces vs namespaces (survey §11 Q2) | A separate axis. Workspaces pin across namespaces via I12 | §34.5 |
| I15 | Consistency between authoritative stores and indexes | Content, pointers and metadata share one ref transaction. Evidence follows its keep-refs. Indexes are derived, watermarked and rebuildable | §34.9 |
| I16 | Producers need moving names (`best`) but tags must be immutable | `pointers/` refs moved by compare-and-set; tags create-only | §34.3 |
| I17 | Derived tool metadata would change tool identity when derivers change | Only stable declared fields in `tool.json`. Derived fields are attributed labels | §34.7 |
| I18 | Symlinks, empty dirs, case-colliding paths, large files | Reject or report with a reason in `ignored_files`; size limits; no LFS | §34.12; T13 (none in corpus) |
| I19 | Secrets in immutable history | Scan before the commit point. Redact rewrites the line and marks evidence | §34.6 |
| I20 | API versioning and backward compatibility | One unversioned API in a new major release. No compatibility layer for the old API: existing users stay on existing releases. Additive evolution afterwards; breaking changes need a new major release | §34.13 |
| I21 | Moving data from existing releases | An offline migration tool: verbatim re-import where `extra.origin` allows, otherwise reconstruction labelled as such. Old UUIDs kept as `legacy_uuid` labels for traceability, never as addresses | §34.13 |
| I22 | Line name vs frontmatter `name` | The line name is the identity. A mismatch is labelled, not rejected | §34.3 |
| I23 | cap-evolve audits rejected candidates; default gc would prune them | Per-producer retention policy `all` or `cited` | §34.9; `store.py:12-13` |
| I24 | Git object format | SHA-1 repos for host compatibility. Evidence keys are SHA-256 `content_digest` | §34.4 |
| I25 | Ref names consumers can use | Restricted charset; mirror refs are a single segment | **T18** |
| I26 | Access to evidence payloads | Only via the API. On an object store, short-lived signed URLs | §34.5 |
| I27 | Writers on several hosts | One leader per namespace takes every write for that namespace, including budget counters. Read replicas. Untested here | §34.9; Phase 6 |
| I28 | Is a database required for mutable state? | No. Metadata in git refs, evidence as append-only files, counters as locked files under the per-namespace leader, indexes derived. An SQLite file is an optional, disposable index; a database server is only a deployment option for many stateless API hosts | §34.19; T20–T23 |
| I29 | SBS's file writes are not atomic today (`file_handler.py:190-233` writes in place), contrary to survey §6.1 and §6.3 | Every file write is temp file + fsync + rename. Required, because files become authoritative for evidence | §34.19 |
| I30 | One metadata ref per line becomes a hotspot under concurrent plugins (T20: 73 retries for 16 writers) | Shard metadata by snapshot (`refs/meta/<kind>/<name>/<digest>`), so writers to different snapshots never contend | §34.19; T20 |
| I31 | Evidence records need a lifecycle (`pending → scored`, `archived`), but record files are append-only | The record file is immutable. Lifecycle lives in a sidecar state file, replaced atomically under the namespace writer | §34.19 |

### 34.18 Remaining open questions

These need data rather than decisions:

1. **Q1 — ref-store envelope beyond 200k refs per namespace** (content, keep and metadata refs together). Measured up to 200k (§34.15). Before
   general availability, measure at 10⁶, including `pack-refs` and deletes. If needed, evaluate git's
   newer `reftable` ref backend (not available in the git 2.39.5 used here) or more namespace
   splitting.
2. **Q2 — mirror throughput under Harbor fan-out.** Harbor resolves once per job and caches by SHA
   (`skills.py:140-187`), so the load is likely low. Benchmark over smart HTTP.
3. **Q3 — do learned skills degrade as their target repos move?** This is the hypothesis S1 makes
   testable for gskill.
4. **Q4 — do per-user forks produce recurring edits worth upstreaming?** S3's hypothesis for
   SkillOpt.
5. **Q5 — cross-optimizer knowledge transfer** (survey X2). Unchanged.

### 34.19 Mutable state without a database

**Short answer: a database is not required.** Every piece of mutable state in §34 can live in git
metadata refs or in plain files, and all of it was tested (T20–T23 below). An index over evidence
*is* required once queries span many records. That index can stay derived and disposable: in memory,
as SBS does today, or in an embedded SQLite file. A database server is warranted in only one
deployment case (point 4 below), and even then not as the source of truth.

**What SBS does today [read, at `70137bb`].**

- Mutable state is a folder tree: one directory per object UUID, holding JSON files.
- At boot, in-memory caches (`DictCache`, `LookupCache`, `DependencyManager`) are built by a full
  scan (`modules/object_handler.py:211-225`).
- Locks are in-process thread locks (`fasteners.ReaderWriterLock`).
- The only specialised engine is the vector store (FAISS, Chroma or LanceDB; `pyproject.toml:105-107`).
- **Correction to survey §6.1 and §6.3:** writes are *not* atomic. `FileHandler.write_file` opens the
  target with `open(path, "wb")` and writes in place (`modules/file_handler.py:190-233`). `os.replace`
  is used only in `plugins/config.py` and `tools/publish_refs.py`. This is I29.

**Where each kind of mutable state lives:**

| Mutable state in §34 | Properties needed | Where it lives | Mechanism | Tested |
|---|---|---|---|---|
| Labels and lifecycle state of a snapshot | concurrency control; atomic additive merge; producer attribution; history | metadata ref per snapshot: `refs/meta/<kind>/<name>/<digest>`, tree `labels/…`, `state.json` | compare-and-set with retry; every change is a commit, so history and attribution are automatic | T20, T21 |
| Line declarations (`depends`, declared tools, promotion policy) | concurrency control | `refs/meta/<kind>/<name>/_line` | compare-and-set | T20 mechanism |
| Workspaces and pins | concurrency control; atomic with the lock composition | `refs/meta/workspace/<ws>`, tree `pins.json` | in the same ref transaction as the lock | T6 mechanism |
| Measurements, findings, runs | append-only; point lookup; per-subject aggregation | evidence store: `measurements/<subject>/<record>.json` | temp file, fsync, rename | T22b |
| Evaluation cache (`seen`) | O(1) lookup before each rollout | `seen/<k[:2]>/<k>.json`, where `k = sha256(cache key)`: the path *is* the key | atomic rename; existence test | T22a |
| Record lifecycle (`pending → scored`, `archived`) | single-writer updates | sidecar `<record>.state.json` | atomic replace by the namespace writer (I31) | standard mechanism |
| Budgets and counters | atomic increment by many writers | `budgets/<scope>` | `flock` under the per-namespace leader | T23 |
| Cross-subject queries (frontier, transfer, filtered search, facets), lineage edges, link records | fast queries over everything | derived index | in memory (as today), optionally persisted to an SQLite file (stdlib `sqlite3`); the vector store for semantic search | T22c shows the cost without an index |
| ACLs and namespace policies | rarely written | configuration files | as today | — |

**Measured (`test_nodb.py`; git 2.39.5, git CLI, one host):**

| Check | Result |
|---|---|
| T20: 16 concurrent label writers on one metadata ref (compare-and-set with retry) | no lost update; 16 history commits; **73 retries** |
| T21: sequential label writes through the git CLI | **22 ms per write (~45/s)**. SBS today manages 2–3 writes/s (survey M15) |
| T22a: `seen` lookup by content-addressed path | **16 µs** per lookup, no index |
| T22b: `agg(subject)` over one subject's directory | 400 records in **17 ms**; 0.29 ms per record write |
| T22c: a query across all subjects, without an index | 20,000 records in **797 ms**, growing linearly |
| T23: `flock`-protected counter, 8 processes × 250 increments | 2000 of 2000 increments kept |

**What keeping metadata in git buys, compared with a database.**

1. **Labels-at-create commit atomically with content.** They go in the same ref transaction. With a
   database there is a second consistency domain, which needs an outbox and reconciliation (I15).
2. **Every label and state change is a commit.** That gives the producer-attributed, timestamped
   history the survey asks of enrichment (§10.1) with no extra machinery.
3. **One backup artifact** per namespace (T19), plus an append-only evidence directory.
4. **No new service to run.** Today's operational model (a folder tree plus a vector store) is
   preserved.

**Costs and limits.**

1. **Write latency.** 22 ms per label write through the CLI is 15–20× today's throughput but far
   below a database's. Most of it is process start-up; a git library such as libgit2 would remove
   it (untested).
2. **Contention.** One hot metadata ref per line made 16 writers retry 73 times (T20). Sharding per
   snapshot means plugins enriching different snapshots never contend (I30). Writers on the *same*
   snapshot still retry, which is correct behaviour.
3. **Queries need an index.** Without one, a cross-subject query is a full scan: 797 ms at 20k
   records, so roughly 40 s at 10⁶ by linear extrapolation. Frontier, transfer, filtered search and
   facets therefore need the derived index. That is no worse than today, where in-memory caches are
   already indexes, but those caches are *authoritative* and rebuilt by a full scan at every boot.
   Here the index is derived, carries a watermark, and can be persisted so boots do not rescan.
4. **Several hosts.** Git ref locks and `flock` assume one filesystem. The design sends every write
   for a namespace through one leader (I27), so counters and compare-and-set stay local to that
   host. **The one case for a database server** is a deployment with many stateless API hosts that
   all need the same fresh cross-namespace index. There, Postgres can hold the derived index, as a
   deployment profile. Content, metadata and evidence stay where they are.
5. **Evidence on an object store.** An object store has no rename, but a single-object PUT is
   atomic, so record files map one-to-one. Scans become LIST calls, which makes the derived index
   matter sooner.

**Decision rule.**

- Start with in-memory derived indexes, as today, but derived rather than authoritative.
- Persist them to an SQLite file once boot-time rebuilds or cross-subject queries become slow.
  Because the file is a cache, it can be deleted and rebuilt at any time.
- Add a database server only for the multi-host profile in point 4.

None of these steps changes the authoritative layout, so they can be taken independently and in any
order the deployment needs.

---

# Appendix A — sources and pinned commits

| Source | Commit | Read for this document |
|---|---|---|
| **skillberry-store** | `70137bb` (this branch's base) | `schemas/skill_schema.py`, `fast_api/skills_api.py`, `tools/shell_hook.py`, `docs/github_using_hooks.md`, `docs/design/skill_optimization_store.md` |
| **cap-evolve** | `c2a4aa6` (`store.py` unchanged at HEAD `476d431`) | `core/cap_evolve/{store,cache,graph}.py`, `docs/{ARCHITECTURE,ADAPTER_CONTRACT}.md`, `skills/interventions/llm-proxies/spa/scripts/spa_env.py` |
| **Arbor** | `7cdaf1f` (= HEAD) | `src/coordinator/idea_tree.py`, `src/coordinator/tools/git_ops.py`, `.gitignore`, `docs/self-evolution.md`, `src/recall.py` |
| **Harbor** | `15da91c` (`skills.py`, `lock.py`, `utils/git.py` unchanged at HEAD `4b94505`) | `src/harbor/skills.py`, `src/harbor/utils/git.py`, `src/harbor/models/job/lock.py`, `src/harbor/models/dataset/manifest.py`, `src/harbor/models/package/dataset_task.py`, `src/harbor/storage/supabase.py`, `src/harbor/upload/{storage,db_client}.py`, `docs-mintlify/news/harbor-registry.mdx` |
| **GEPA + gskill** | `d771eb2` | `src/gepa/core/{state,adapter}.py`, `src/gepa/proposer/merge.py`, `src/gepa/strategies/component_selector.py`, `src/gepa/gskill/gskill/{swe_fitness_fn.py,evaluate/claude_code_skills.py}` |
| **SkillOpt** | `79124b3` | `skillopt_sleep/{staging,config}.py`, `ckpt/` |
| **CORAL** | `0123dfb` (= HEAD) | `coral/hub/{checkpoint,attempts,skills,notes}.py`, `coral/hooks/post_commit.py`, `coral/types.py`, `coral/workspace/{repo,worktree}.py`, `coral/grader/daemon.py` |
| **AgentEvalHarness** | `55357f7` | `eval.yaml`, `agent_eval/ci_context.py` |

# Appendix B — citation index

| § | Claim | Source (file:line at the pinned commit) |
|---|---|---|
| 5.1, 7 | cap-evolve: git is the default backend; one repo per run dir; every iteration committed | cap-evolve `core/cap_evolve/store.py:4-14`, `:41-48`, `:64-72` |
| 5.1, 8 | cap-evolve: `best` is a force-moved tag | `store.py:71` (`tag -f`); callers `gepa.py:705,878` |
| 5.1 | cap-evolve: rollouts and work gitignored | `store.py:48` |
| 5.2, 8 | cap-evolve: lineage is `parents[]`, 2+ for a merge, in `graph.jsonl` | `core/cap_evolve/graph.py:15`, `:94-119` |
| 5.1, 10 | cap-evolve: candidate is a multi-part capability dir; components materialised into it | `docs/ARCHITECTURE.md:64`; `docs/ADAPTER_CONTRACT.md:46`; `core/cap_evolve/rundir.py:9` |
| 8 | cap-evolve: content digest over the candidate dir | `core/cap_evolve/cache.py:36` |
| 5.3, 9 | cap-evolve: frozen primitives called by name | `spa_env.py:1025-1031` |
| 5.1, 6.4 | Arbor: `code_ref` is a git branch name | Arbor `src/coordinator/idea_tree.py:45` |
| 5.2 | Arbor: merges are `git merge --no-ff` | `src/coordinator/tools/git_ops.py:523` |
| 5.1 | Arbor: `.arbor/` gitignored; experience per session | `.gitignore:29`; `docs/self-evolution.md:71` |
| 6.3 | Harbor: URL sources default to `skills/` subdir | Harbor `src/harbor/skills.py:41`, `:74-121` |
| 6.3 | Harbor: a source may be a root of many skills; last-wins by name | `skills.py:125-137`, `:394-428` |
| 13 | Harbor: refs resolved by `git ls-remote`; "No matching ref" otherwise | `skills.py:266-291`; `src/harbor/utils/git.py:4-26` |
| 5.1 | Harbor: own skill digest | `skills.py:214-223` |
| 5.3, 11 | Harbor: trial lock carries a list of skill locks | `src/harbor/models/job/lock.py:143-162`, `:191` |
| 6.2, 11 | Harbor: dataset manifest pins tasks by digest | `src/harbor/models/dataset/manifest.py:24-31`, `:161-175` |
| 6.2, 14 | Harbor Hub: buckets `packages`, `results`; Postgres `job`/`trial` rows; `archive_path` as finalized sentinel | `src/harbor/storage/supabase.py:16`; `src/harbor/upload/storage.py:14`; `src/harbor/upload/db_client.py:105-128`, `:543`, `:569-591` |
| 6.2 | Harbor: "task is the atomic unit"; datasets are "collection of tasks at specific versions"; develop on VCS, publish "similar to how Docker or PyPI work" | `docs-mintlify/news/harbor-registry.mdx:18`, `:82`, `:84` |
| 5.1 | GEPA: candidate is `dict[str, str]`; hash over sorted items | GEPA `src/gepa/core/adapter.py:12`; `src/gepa/core/state.py:37-39` |
| 5.2 | GEPA: multi-parent lineage | `state.py:232-233` |
| 6.5 | GEPA: per-component merge across lineages; component selector | `src/gepa/proposer/merge.py:118-193`; `src/gepa/strategies/component_selector.py:10` |
| 5.1 | GEPA: no git in `src/gepa` | `grep` for git subprocess calls under `src/gepa`: none (one comment in `oa/proposers/claude_code_agent.py:50`) |
| 5.1 | gskill: candidate is a single `skills` string, installed as one `SKILL.md` | `src/gepa/gskill/gskill/swe_fitness_fn.py:199-206`; `evaluate/claude_code_skills.py:95` |
| 6.7 | SkillOpt: never mutates live files; adopt after backup | SkillOpt `skillopt_sleep/staging.py:1-7` |
| 6.7 | SkillOpt: group publish, manifest last, rollback as a set | `staging.py:589-597` |
| 6.7 | SkillOpt: refuses if live target changed since staging | `staging.py:1416` |
| 18.6 | SkillOpt: one managed skill | `skillopt_sleep/config.py:90`, `:191` |
| 5.1 | CORAL: `.coral/public` is a local git repo; `git add -A` under `flock` | CORAL `coral/hub/checkpoint.py:1`, `:68-110` |
| 6.1 | CORAL: `checkpoint()` returns `None` when nothing to commit | `checkpoint.py:104` |
| 6.1 | CORAL: attempt fields incl. `shared_state_hash`, `parent_shared_state_hash` | `coral/types.py:162-172` |
| 6.1 | CORAL: shared state checkpointed at submission and stored on the attempt | `coral/hooks/post_commit.py:186-224` |
| 6.1 | CORAL: grader copies the field forward | `coral/grader/daemon.py:526`, `:619` |
| 10 | CORAL: attempts keyed by commit, rewritten for `user_best` and `archived`; `pending` with `score=None` | `coral/hub/attempts.py:50-58`, `:76-100`, `:102-135`, `:293-313` |
| 5.1 | CORAL: agent worktrees of one task repo | `coral/workspace/repo.py:80-115`; `coral/workspace/worktree.py:21` |
| 5.3 | AEH: skill under test by name; sub-skills via `plugin_dirs` | AEH `eval.yaml:11`, `:26` |
| 5.1 | AEH: CI tags `commit_sha`, `harness_fingerprint` | `agent_eval/ci_context.py:65-76` |
| 5.3, 9 | SBS: a skill is an ordered list of tool and snippet UUIDs | SBS `src/skillberry_store/schemas/skill_schema.py:9-24` |
| 12 | SBS: default import makes one snippet per file | `src/skillberry_store/fast_api/skills_api.py:534` |
| 5.1 | SBS: git persistence via shell hooks into one repo, push per write | `docs/github_using_hooks.md` |
