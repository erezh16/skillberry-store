# Skillberry Store as the Artifact Plane for Agent Optimization and Evaluation

**Status:** design study for review. Not an implementation plan.
**Date:** 2026-09-27 · **Revised:** 2026-10-04. The baseline is now a *shared git remote*, not
local git (§1.3, §1.5). Every source claim was re-checked at its pinned commit (Appendix A.5).
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

> **Make SBS the place an agent artifact's *evidence* lives once it has to outlive the run that
> produced it — next to, and indexing, the git repo the artifact itself may already live in.**

Not a file store, not a replacement for git, and not another experiment tracker. An **artifact plane**: immutable,
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

Identity is not what is missing. A git commit or tree hash is a content address that stays valid
outside the run, and a shared GitHub repo already carries artifacts across runs and people. Harbor
already consumes skills that way. The question is unanswerable for a narrower reason: **nothing binds
a measurement to an artifact's identity *together with its context*** (model, harness, judge,
dataset, split), **and nothing indexes those bindings across runs, repos and producers so they can be
queried.** SBS adds its own problem on top: the artifacts it holds have no faithful identity at all
(§2.2). Everything in this document follows from fixing those two things.

### 1.3 The scope of the claim: value tracks what git cannot do, not boundaries crossed

Local git and a shared git remote are different baselines, and the second is the fair one. A shared
remote already crosses most of the boundaries that might be thought to need a store:

| Boundary | Local git + a directory | Shared git remote (GitHub, GitLab) | What a store adds on top |
|---|---|---|---|
| more than one **run** | `--resume`, copy a run dir | commit the run state; the tree hash is the identity; merge commits give multi-parent lineage | an eval cache keyed by artifact **and context**, with a live lookup |
| more than one **person or team** | — | repo permissions, PR review, signed commits, CODEOWNERS | per-object and partial-read authz (sealed splits), quotas, audit |
| more than one **agent / model / harness** | — | measurement files under `measurements/<digest>/`; a CI job builds the transfer matrix | the matrix as a query, with many contributors appending, and **negative** cells kept |
| more than one **optimizer** | — | a meeting point for **append-only** records (attempts, notes, candidates) | shared **mutable** state: budget, running best, cache (§10.2) |

None of the optimizers whose persistence was checked pushes its run state to a shared remote.
cap-evolve `git init`s a fresh repo per run with no remote. Arbor gitignores its own `.arbor/`
experience. CORAL checkpoints `.coral/public` into a local repo under a host-local `flock`
**[read]**. Only Harbor *consumes* a remote, for skills. So the shared-git option is available but
unused for run state.
Where it would fail if tried can be read straight from the sources, and those failure points are
where a store is actually needed:

1. **Context-scoped, queryable evidence.** cap-evolve's cache key is `candidate_hash::task_id`, with
   no model, intervention or environment. GEPA's disk cache key has no evaluator or model either.
   Within one run that is safe. Shared across people, a hit silently returns a score measured under
   a different model.
2. **Many writers on shared mutable state.** cap-evolve's `eval_cache.json` is one JSON line,
   rewritten on every put, so any two contributors produce a merge conflict. CORAL's `eval_count`
   is an unlocked read-modify-write integer. GEPA's `gepa_state.bin` is a monolithic pickle. Git
   merges append-only, one-file-per-record data well (CORAL's attempts, GEPA's `fitness_cache/`). It
   cannot merge a counter, a running best or a budget.
3. **Live lookup.** Every cache studied is loaded once at startup. With git, another contributor's
   entries become visible only after a pull and a restart, so concurrent runs cannot share hits.
4. **Partial-read access control.** Anyone who can read a repo can read all of it. A sealed test
   split that the optimizer must not see therefore needs a separate repo per seal boundary.
5. **Large, sensitive or expiring data.** SkillOpt's `evidence.jsonl` holds every prompt and reply.
   It is gitignored and documented as sensitive local data. Trajectories are large and may contain
   secrets. Git history cannot expire them, and a pushed secret has been published.
6. **Unsafe formats.** GEPA's resume state and disk cache are pickles. A shared repo of pickles means
   code execution on every consumer, for anyone with push access.
7. **Serving to agents at runtime.** Discovery, MCP and vMCP serving, and sandboxed tool execution
   are SBS's existing job. A repo serves bytes to a cloner, not capabilities to an agent.

**[judgement]** SBS earns its place where one of those seven applies. Below that line, a shared
repo is the right tool, and SBS should **index and mirror it, not compete with it**. That means
recording resolved commit SHAs, serving original bytes, and mirroring a namespace to a git remote
(§8.6). Above that line, nothing in this landscape is even trying.

### 1.4 Why this is the moment

All eight projects are actively crossing those boundaries, and each is straining against the
single-run assumption its storage was built on **[read]**:

- **GEPA** replaced monotonic iteration counters with random ids specifically so its state survives
  *"agent-swarm engines that propose candidates without a shared clock."*
- **CORAL** added multi-island runs with migration between islands.
- **SkillOpt** ships seven host integrations, and its headline result is 52
  `(model, benchmark, harness)` cells.
- **gskill**'s entire result is **cross-agent transfer**: skills learned with a simple agent on a
  small model (Mini-SWE-Agent + gpt-5-mini) improve Claude Code.
- **Arbor** scopes experience deliberately: *"Experience lives per session, not in a global
  library"*, so that *"an unrelated task never inherits another domain's tricks."* That is a design
  choice. A store has to respect it, not "fix" it.

### 1.5 The vision, made demonstrable: zeros against the right baseline

Each project below has a capability whose value is zero today, but most of them are zero only
against **what each project does today**. Measured against a **shared git remote
plus conventions**, which is the fair baseline from §1.3, most of them stop being zeros. Git can do
them by convention, and the store adds query and enforcement. Only the zeros that survive the git
baseline can be proven by a single demonstration, without paired runs or a significance test.

| Project | Capability | Today | With a shared git remote | Survives? |
|---|---|:-:|---|:-:|
| **cap-evolve** | candidates evaluable in parallel against one store | **1** | irrelevant. The blocker is SBS itself (global names, re-import duplicates every tool **[measured]**) plus SPA binding one skill on fixed ports | **yes, but it is an SBS defect, not a store-vs-git win** |
| **GEPA, cap-evolve** | eval-cache hits between *independent, concurrent* runs | **0** | GEPA's `fitness_cache/*.pkl` is file-per-entry and merges cleanly. But it is pickle (code execution from the repo), its key has no evaluator or model, and it is loaded once at start. cap-evolve's cache conflicts on every write | **yes** (needs §1.3 points 1–3, 6) |
| **CORAL** | several machines in one shared memory, with one budget | **1 machine** | attempts and per-experiment notes merge cleanly. `eval_count`, the shared `index.md` and the heartbeat files do not, and `flock` is single-host | **yes** (point 2) |
| **Harbor** | skills sourced from a versioned remote | **0 from SBS** | **works today from any git host**, with no change on either side | **no.** What survives is the reverse direction: Hub stores each trial's lock, skill digests included, but offers no skill-keyed query |
| **Arbor** | findings reused across machines and projects | **0** | a shared repo of `.arbor/sessions/` (append-only folders) covers *same project, many machines* cheaply | **partly.** Ranked cross-project recall that respects Arbor's scoping is a hypothesis |
| **gskill** | repo skills published with a per-agent transfer matrix | **0** | a repo of learned skills plus a committed matrix CSV is a working catalogue | **weakly.** The increment is many contributors appending to one queryable matrix |
| **SkillOpt** | Sleep proposals shared across machines | **0** | one team, one repo: commit or open a PR from `.skillopt-sleep/staging/` (proposals already carry SHA-256 digests) | **weakly.** The increment is cross-user aggregation and central retention for `evidence.jsonl`, which must not go to git |
| **AEH** | measurements attributable to a **judge version** | **≈0% directly** | in CI, runs are already tagged `commit_sha`, so the judge version is recoverable as `(commit, judge name)`. Local runs get nothing | **weakly.** The increment is a cross-repo regrade-staleness index |

**Three strong zeros survive the git baseline.** They are the cross-run cache, multi-machine shared
state, and parallel candidates (an SBS fix). All three depend on shared mutable state or on SBS's own
contracts, not on identity or distribution. The other five are real but **incremental over git**.
They have to be argued as query, enforcement and convenience, not as "impossible today". The
distinction matters because §6 shows this document's whole spine is about *not* producing numbers
that look right but aren't, and that standard applies to the document's own baselines too.

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
| Lineage with multi-parent merge | ✓ | ✓ | – | ✓ | ✓ | – | – | – | 4 |
| Stored negative knowledge | ✓ | ✓ | – | ✓ | – | ✓ | ✓ | – | 5 |
| Cross-agent / harness transfer measured | – | – | – | – | ✓ | ✓ | – | – | 2 |
| Pareto / multi-objective archive | – | – | – | ✓ | – | ✓ | – | – | 2 |
| Eval cache keyed by candidate digest | ✓ | – | – | ✓ | – | – | – | – | 2 |
| **Default persistence** | git | git | git→registry | pickle+JSON | files | files | dirs+worktrees | **MLflow** | — |

Two observations carry the argument.

**The two unanimous primitives are the two SBS lacks entirely.** A measurement must be bound to an
artifact version; the test split must be sealed. Eight of eight concluded they could not work
without these.

**Nobody has an artifact registry, and the de facto one is a git repo.** Seven persist to files or
local git. AEH uses MLflow, which is run-centric. Harbor has a real registry, but Hub registers
only **tasks and datasets** (`PackageType = "task" | "dataset"`) and calls itself a publishing
target *"rather than a development platform."* For agent-side artifacts, the working registry today
is GitHub. Harbor sources skills from any git host, and SkillOpt ships its reference skills in its
repo. That covers distribution and identity. It does not cover evidence bound to context, or shared
mutable state (§1.3). That is where the hole is, and the two most sophisticated systems strain
hardest against it: GEPA's pickle, and CORAL's host-local, symlinked directory.

### 2.2 SBS has the right shape and the wrong contracts

Measured on a running instance at `main` (`c6e72ce`), and on **81 real published skills from five
independent projects** (~760 files; Appendix B) **[measured]**. Re-checked in code at `da481ee`
(Appendix A.5). #326 made exported frontmatter and ZIPs deterministic and added a `sha256` digest for
npx-published skills. That digest covers the *regenerated* export, not the source, so every row
below still holds:

| Finding | Value |
|---|---|
| Round-trip **bundle digest** fidelity (bytes + modes) | **0 / 12** — never achieved, even for a single-file skill |
| Skills whose licence is replaced by, or given, a fabricated `Proprietary` claim | **14 / 81 (17%)**: 13 Apache-2.0 skills relabelled, plus 1 given a licence it never declared. Verified live on Anthropic's `algorithmic-art`, whose `LICENSE.txt` **is Apache-2.0** |
| Skills losing an executable bit on a script they invoke | **15 / 81 (18.5%)**, 38 files |
| Skills silently losing a binary payload (`ignored_files: []`) | **3 / 81**, 56 files (54 fonts, a component tarball, a showcase PDF) |
| Skills losing frontmatter keys | **36 / 81 (44%)**; 7 key families destroyed. **4 more lose *all* frontmatter**, `name` and `description` included, on a YAML parse error |
| Concurrent read-modify-write | **lost update reproduced on the first attempt**, both writers got `200` |
| One concurrent writer's effect on a paged read | **7.1 ms → 699.5 ms (99×)** |
| `DELETE …?delete_tools=true` | **`200`** with `deleted_tools: []`, 18 objects orphaned |
| Retrieval of a query quoting an artifact's own body verbatim | distance **1.713** vs **1.898** for an unrelated query — no signal |

### 2.3 The one architectural decision everything follows from

> **Make the immutable, content-addressed *bundle* the source of truth, and make tools, snippets,
> params and frontmatter *derived projections* over it.**

Today it is the reverse: objects are truth and the file tree is reconstructed from `file:<path>`
tags, with `SKILL.md` frontmatter regenerated from `name` + `description`. **Every** fidelity,
digest, versioning, caching and interop defect above follows from that single choice. So does the
fact that nothing SBS serves can be traced back, byte for byte, to its source. A git remote holding
the same skill gives Harbor a working, digest-locked source today. SBS does not.

---

## 3. What each project gains

The justification, per project. Each number carries its basis: **[measured]** this study,
**[definitional]** zero by construction, **[countable]** trivially counted once built,
**[to-instrument]** no baseline yet — say so rather than guess.

### 3.1 cap-evolve — the live consumer, and the largest measured saving

**Today.** cap-evolve already uses SBS (via the SPA intervention). Because two candidates cannot
coexist, the adapter **wipes the whole store per candidate**. `reset_store_to_skill()` makes at least
7 fixed HTTP calls, plus per-object GET/DELETEs, inside two retry-until-no-progress loops, followed by
an SPA restart. It relies on a hardcoded list of dependent object kinds *derived by reading SBS's
source at tag 0.2.1*. A client-side `Protection` class, with a before/after tripwire, keeps the
benchmark's frozen primitives from being deleted. An interrupted run once left vMCP servers behind
and failed **every rollout of a 50-task × 10-trial arm (coverage 0/50)** **[read]**. (At cap-evolve
HEAD the intervention is renamed `spa` → `blackbox` and the file `blackbox_env.py`. It is still
pinned to SBS 0.2.1.)

**Gains.** One atomic workspace `:apply`. Parallel candidates, *given* SPA resolution that is
workspace-aware and multi-port, since SPA's one-skill binding is SPA's property, not SBS's. A shared,
context-scoped eval cache. Broke/fixed/protect-set as a service. `memory_skill: sbs`. And, first, an
integration needing no cap-evolve code change, through the `store: command` backend it already ships
(§9: it needs a small wrapper, and it *replaces* git persistence rather than adding to it).

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Store-management workaround LOC in `spa_env.py` | ~325 (~160 executable), plus ~120 patching SBS/SPA source at the pinned tag | 0 | [countable] |
| Objects orphaned by one cascade delete | **18** | 0 | [measured] |
| Candidates evaluable in parallel | **1** | K | [definitional], given workspace-aware SPA |
| Store calls per candidate deploy | ≥7 + O(objects) + 2 retry loops + restart | **1** | [countable] |
| cap-evolve LOC changed for record-keeping | — | **0** (a shell wrapper) | [countable] |
| Rollouts avoided by cache hits | — | instrument | [to-instrument]. cap-evolve's own cache serves **GEPA train minibatches only** (1 trial). A 20-iteration × 50-task × 10-trial run's 10,000 rollouts are mostly val-gate trials it never serves. Report *rollouts avoided × cost*, not a guessed rate |

**Against a shared git remote.** cap-evolve has nothing to displace. Its cross-run features are
`--resume`, `--reuse-baseline` from a local path, and cost calibration by globbing local `run_*`
dirs. Committing `eval_cache.json` to a shared repo fails in specific ways:
- It is one JSON line, rewritten on every put, so every pair of contributors conflicts.
- Its key `candidate_hash::task_id` omits model, intervention and environment.
- Errored rollouts are cached and replayed, so one person's infra failure becomes everyone's result.
- It is loaded once at start.
- It carries no split.

Each fix (a context-scoped key, append-only records, live lookup, split-aware access) is a store
feature. cap-evolve has also already seen an API key committed into a results file **[read]**,
which a push would publish.

### 3.2 Harbor — git already works; the gain is the evidence join

**Today.** Harbor resolves a skill source given as a **named git ref** (branch, tag or HEAD) with
`git ls-remote`. A raw commit SHA is rejected. Harbor then sparse-fetches that commit, computes **its
own** digest, and records `{name, source, digest, git_url, git_commit_id}` per trial in `lock.json`.
In Harbor's words: *"This ensures that job results are fully traceable back to the exact skill content
that was used, even if the branch has since moved."* **Any git host already satisfies that
contract**, so a GitHub repo of skills works today with no change on either side. SBS does not,
because it exposes no git remote **[read]**.

**Gains, honestly scoped.** Outbound sourcing alone is *not* a reason to adopt SBS, since mirroring
the skills to a git host achieves it (§8.6). Two gains survive:
- **A skill-keyed evidence panel.** Hub stores each trial's lock, skill digests included, but offers
  no skill-keyed query or index, unlike tasks, which have `task_content_hash`.
  `jobs:import` would close that loop.
- **SBS-native skills runnable without a mirror**, once SBS serves original bytes.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Skills runnable from SBS | **0** (from any git host: works) | works | [definitional] |
| Harbor code changed | — | **0** | [countable] |
| SBS `harbor_digest` == Harbor's computed digest | n/a | exact match | [countable]. Requires reimplementing Harbor's `compute_skill_digest` (§8.2). A git tree hash and SBS's own digest will never match it |
| Trials joinable to a capability version by skill digest | **0** indexed (the data is in Hub's lock column) | N | [definitional] |

**Demo:** `harbor run --skill https://sbs/git/<ns>/tree/<ref>/<skill> -a claude-code -m …`, then show
the lock's digest resolving back to an SBS ref. Today the command parses, but nothing answers at that
URL. `<ref>` must be a named ref with no `/`, and cannot be `sha256:…`. **The same command against a
GitHub mirror of the skill works today.** That is the honest baseline, and it is why the git mirror
in §8.6 comes first.

### 3.3 gskill — the most legible new product, if it beats a repo and a CSV

**Today.** gskill turns any GitHub repo into a learned skill. Test resolve rates with Mini-SWE-Agent
on gpt-5-mini: Jinja 55%→**82%**, Bleve 24%→**93%**. Transferred to Claude Code: Bleve Haiku
79.3%→**98.3%**, with duration **173s→142s**. The output is
`best_skills.txt` in a run directory; transfer to each new agent is re-measured by hand; nothing is
catalogued **[read]**. So the question users actually have — *"is there a learned skill for my repo,
and does it help my agent?"* — has nowhere to be asked.

**Gains.** A catalogue of repo-scoped learned skills, each with a measured per-`(agent, model,
harness)` transfer matrix that includes duration and keeps **negative** entries. Claude Code +
Sonnet 4.5 on Jinja went 100%→**98.5%** while duration fell 254s→225s. A store that hid that would be
lying.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Repos with a published, addressable learned skill | **0** | N | [definitional] |
| Skills carrying a per-agent transfer matrix | **0** | N | [definitional] |
| With/without pair stored as a first-class counterfactual | **0** | N | [definitional] — gskill already runs both arms; the pairing ends up in a chart, not a record |

**Against a shared git remote.** A repo of learned skills with a committed matrix CSV is a working
catalogue, and a reasonable first step. The store's increment is a matrix that many contributors
append to and query, with negative cells kept and context fields enforced.

**[judgement]** This is still the most legible artefact of this design for an outside audience, and
it needs no new science. But the git version is the bar it must clear.

### 3.4 GEPA — a shared cache, and an escape from pickle

**Today.** `EvaluationCache` is keyed `(CandidateHash, split, DataId)`, which is the right key. It
lives inside `GEPAState`, is pickled into `gepa_state.bin`, and **is reused across runs only by
resuming or copying one run directory**. gskill's `--resume` does exactly that. `optimize_anything`
also has a separate disk cache, off by default: `fitness_cache/<cand>_<example>.pkl`, keyed by
candidate hash and example *content* hash, with no split. There is no cache shared between
*independent* runs, users or frameworks. Resume state is a **pickle**: unqueryable, unshareable, and
unsafe to load from an untrusted source **[read]**.

**Gains.** A shared digest-keyed cache that spans independent runs, users and frameworks and is
scoped by evaluator, model and split. Typed, queryable state. Pareto-frontier queries as a service.
GEPA already computes the digest SBS needs (`sha256` over the sorted candidate dict), so only the
cache's *scope* changes.

**Against a shared git remote.** `fitness_cache/` is file-per-entry and content-named, so it merges
cleanly. A git-shared copy *almost* works as a crude cross-run cache today. It is unsafe and
unsound: loading pickles from a shared repo is code execution for anyone with push access, and the
key carries no evaluator, judge, model or split. It is also stale after startup, because it is read
once at adapter init. `gepa_state.bin` cannot be shared at all, being one binary blob rewritten
every iteration.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Cache hits between runs that share no `run_dir` lineage | **0** | >0 | [definitional] |
| Resume state queryable / shareable / safe to load untrusted | no/no/no | yes/yes/yes | [countable] |
| Frontier queryable outside the producing run | **0** | yes | [definitional] |

**Inherits GEPA's own warning:** `split` must be a *mandatory* cache key. GEPA drops split-less
legacy entries rather than serving them, because they cannot be distinguished from contaminated
ones. A shared cross-team cache turns that from a local bug into cross-team corruption.

### 3.5 Arbor — extends a scope Arbor chose deliberately

**Today.** `distill_abstract` mines findings from a finished run with an LLM at finalize time. It is
**off by default**, being *"the only part that spends extra LLM calls."* Experience *"lives per
session, not in a global library"*, and Arbor presents that as a design choice, not a limitation.
Recall reads only `<cwd>/.arbor/sessions/*/EXPERIENCE.md`, rejects symlinked session folders, and
`.arbor/` is gitignored. `[xN]` is a lexical dedupe over each bullet's first 80 characters, so it
counts recurrence within one project's local history only **[read]**.

**Gains.** Mining moved off the run budget. A findings library scoped by project and dataset, which
keeps the isolation Arbor deliberately wants, with recurrence counted **across machines and runs of
the same project**, plus opt-in cross-project recall with real relevance ranking instead of keyword
overlap. `code_ref` branches published as versions. Contamination metadata as queryable fields.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Finalize-time LLM calls when `distill_abstract` is on | N | **0** (moved to store enrichment) | [countable] |
| Findings reusable across machines / projects | **0** / **0** | K / K | [definitional] |
| Refuted directions a later run avoids | — | count | [to-instrument] — a *count*, robust at small n |

**Honest limit.** Arbor already reuses experience *within* a project on one machine. A shared repo
of `.arbor/sessions/` folders would cover *same project, many machines* cheaply. The folders are
append-only, so they merge without conflict, though recall's symlink check would need a real
checkout. The store's increment is ranked, scoped cross-project recall and budget relief, not reuse
itself.

### 3.6 SkillOpt — a paper table becomes a live query

**Today.** The 52 `(model, benchmark, harness)` cells exist as a **paper table** (+23.5 points direct
chat, +24.8 in Codex, +19.1 in Claude Code on GPT-5.5). The reference artifacts are six GPT-5.5
skills in git, one per benchmark: `ckpt/<benchmark>/gpt5.5_skill.md`. SkillOpt-Sleep stages
proposals as files under the project's `.skillopt-sleep/staging/`. Each proposal carries a SHA-256
digest, and the live baseline is re-checked at adopt time. There is no shared index or sharing
mechanism **[read]**.

**Gains.** The transfer matrix as a live query. Artifacts addressed by `(benchmark, model)` with
digests. Sleep proposals as *staged* store proposals, aggregated across users and projects, with
evidence retention enforced centrally rather than by a README warning.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| `(model, benchmark, harness)` cells queryable | **0** (a paper table) | 52 | [definitional] |
| Sleep proposals indexed across users / projects | **0** | N | [definitional] |

**Honest limit.** For the *paper* checkpoints, git is already adequate. For one team on one repo,
committing a staged proposal, or opening a PR from it, is a *better* review-then-adopt gate than a
store. The gain is the deployment case: Sleep running per user and per project, with recurrence
aggregated across users. `evidence.jsonl`, which holds every prompt and reply, must not go to git at
all.

### 3.7 CORAL — knowledge that survives the run

**Today.** `.coral/` is created by `coral start`: one directory, one machine, one run. Islands scope
attempts, notes and skills *within* a run, with migration between them. Skills can be carried into a
new run by hand, through `agents.skills`. Notes and attempts cannot. CORAL improves 3–10× faster than
fixed evolutionary baselines. Its ablation shows the knowledge artifacts contribute causally:
turning off notes and skills costs 18.6% on the kernel task. That reuse stops at the run boundary
**[read]**.

**Gains.** Islands as durable store workspaces with the migration policy preserved; attempts as
measurements (its record already carries commit hash, producer, parent, status, feedback); notes as
findings with cross-run recurrence; agents distributed across machines.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Notes and attempts surviving a run boundary | **0** (skills: manual re-seed) | K | [definitional] |
| Machines in one shared memory | **1** | N | [definitional] |

**Against a shared git remote.** `.coral/public` is already a local git repo. Attempts, keyed by
commit hash, and per-experiment notes would merge cleanly over a remote. Several things would not:
- `eval_count`, an unlocked read-modify-write integer, could not enforce a global budget.
- The shared `index.md` and `_connections.md` would conflict on every concurrent append.
- Heartbeat and log files inside `public/` would churn.
- `flock`, the grader and the worktree symlinks all assume one filesystem.

That residue is shared mutable state, the case §1.3 says a store is for.

**Honest limit.** CORAL measured reuse *within* a run. That cross-run reuse helps similarly is the
plausible **hypothesis**, not a result.

### 3.8 AgentEvalHarness — version the thing that defines every reward

**Today.** AEH's judge engine already runs as the verifier inside Harbor trial containers
(`reward.py → reward.json`). Its `/eval-train` *design*, mostly marked planned, would feed those
rewards through NeMo Gym to GRPO weight updates. Judges are entries in `eval.yaml`, prompt files, or
Python modules, with no first-class identity. AEH records no judge digest, and MLflow feedback cites a
judge only by name. In CI, runs are tagged `commit_sha`, `repository_url` and `harness_fingerprint`,
so a judge version is recoverable *indirectly*, as `(commit, judge name)`. Local runs without a
snapshot get nothing. Nothing maps a judge change to the runs that need regrading **[read]**.

**Gains.** Judges as versioned, digested, measured artifacts. Regrade-staleness labels **across
repos and runners**. Skills-under-test with identity. Measurements joined to MLflow runs via
`producer.run_ref`.

| Measure | Baseline | Target | Basis |
|---|---|---|---|
| Measurements attributable to a judge version | **≈0% directly** (CI runs: via `commit_sha`) | 100% | [countable] |
| Regrade candidates identified when a judge changes | **0** | all affected | [definitional] |

**Honest limit.** This is the weakest case. AEH already has MLflow and git. Logging a hash of the
resolved judge config into every run is a few lines in AEH, not a service, and MLflow's **Prompt
Registry**, with immutable versions and aliases, already covers judge-prompt versioning. The store's
increment is a cross-repo regrade-staleness index. SBS must federate with MLflow, never replace
it.

### 3.9 Where SBS would *not* be the best answer

| Case | Better tool | Why |
|---|---|---|
| One run, one machine, one person | **git + a directory** | Simpler, offline, byte-exact, zero ops. Seven of eight chose it for good reasons |
| Many runs or people, one team, append-only records, nothing sealed or sensitive | **a shared git remote** | Identity (commit/tree hash), lineage, review, per-repo authz, and Harbor already consumes it. Index and mirror it (§8.6), don't replace it |
| Judge / prompt versioning inside an MLflow shop | **MLflow Prompt Registry** | Immutable versions + aliases, already deployed. SBS references it |
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
| **T0** | **Five wrong-output defects** | §4.1 | Stop emitting incorrect data. Two are correctness/legal; one drops payloads silently; one discards all metadata; one silently loses concurrent writes | licence fidelity **83%→100%**; dropped binaries **3 skills/56 files→0**; cascade success-with-nothing-done **yes→no**; lost-update race **reproducible→`412`** | **S** |
| **T1** | **Artifact identity** — original bytes kept, blobs, content/capability/Harbor digests, immutable versions, resolved git provenance, a git mirror | §8.1–8.3, §8.6 | **The keystone.** Nothing attaches to an artifact until it has a stable identity. Unblocks Harbor, the eval cache, measurement subjects, enrichment attribution, cross-optimizer duplicate detection. The git mirror is the cheap first step: it makes SBS a Harbor source through any existing git host | bundle-digest fidelity **0/12 → 12/12** (App. B corpus); SBS `harbor_digest` = Harbor's; Harbor runs a skill from an SBS mirror; cap-evolve integrates at **0 LOC changed** | **L** |
| **T2** | **Concurrency safety** — ETag/If-Match, PATCH, atomic publish, async indexing | §8.4 | Makes the store **safely shareable**. Today two writers silently lose each other's work and one writer stalls every reader | lost update → **`412`**; read-under-write **99× → ≤2×**; partial import reported as success **yes→no** | **M** |
| **T3** | **Isolation** — namespaces, workspaces, declarative `:apply` | §8.5 | **Biggest win for the live consumer.** Ends store-reset-per-candidate; enables parallel evaluation | **~325 LOC deleted** from `spa_env.py`; orphans **18→0**; parallel candidates **1→K** | **M** |
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
| **Licence fabrication** | `exporter.generate_skill_md` emits `license: Proprietary. LICENSE.txt has complete terms` whenever any imported text-file snippet's path contains "license" (case-insensitive). Verified live on `algorithmic-art`, which declares `license: Complete terms in LICENSE.txt` over an **Apache-2.0** `LICENSE.txt`. 14/81 of the corpus altered (4 more are correct only by coincidence) | **Never synthesize a licence.** Preserve the declared value verbatim; emit none if none is declared. Delete the filename heuristic |
| **Silent file loss** | `read_from_folder` opens every file as UTF-8 and drops what fails to decode; `ignored_files` reported `[]` while 56 files vanished | **Report every skipped file** with its reason. Interim fix ahead of T1: read bytes and store undecodable files as opaque blobs |
| **Inert delete cascade** | `?delete_tools=true` returns **200** with `deleted_tools: []`, orphaning 18 objects, because the skill is still its own tools' dependent and `ObjectInUseError` is caught as a warning | **Correct-or-`409`.** Delete in dependency order (the store can compute it); if something genuinely blocks, `409` naming it. Never succeed having done nothing |
| **Destructive frontmatter parse** | `parse_skill_metadata` returns `None` on any YAML error, discarding **all** metadata, including `name` and `description`, which fall back to the folder name and a stock string. Agents write malformed YAML: **4/81** corpus skills (all cap-evolve, with an unquoted `: `) hit this | **Lenient, non-destructive parse**, like the flat `key: value` fallback CORAL uses for *notes* (its own `SKILL.md` parser is as destructive as SBS's), and keep the raw block verbatim |
| **Plugin write race** | `emit_event` runs handlers **concurrently** (`create_task` per handler). `evaluator` and `security` each read the whole object, `await` an LLM call, then write the stale whole object back. Last writer wins on tags *and* `extra`. `StoreAPI.update_*_tags` is an unlocked read-modify-write too, though with no `await` inside it, so it races only with threadpool writers | **Atomic additive merge** server-side, and plugins write labels through it rather than whole objects. Producer-namespaced labels are then conflict-free by construction |

**[judgement]** Treat the licence item as urgent independently of this document: the store is making a
false proprietary-licence assertion about third-party content on 14 of 81 skills in a real corpus,
including Apache-2.0 material published by Anthropic. It is a one-line heuristic to delete.

### 4.2 Why not the obvious alternatives

| Tempting first move | Why not |
|---|---|
| **Retrieval** — visible, demo-friendly, embarrassing baseline | Attaches to nothing stable and improves no decision yet. Body embeddings over artifacts whose bytes change under `PUT` produce an index that silently describes content that is gone. Do it at T5–T6 |
| **Measurements** — the strategic differentiator | A measurement needs a stable subject. Recorded against today's mutable objects it is invalidated by any later `PUT`, undetectably. T1 first |
| **The enrichment pipeline** — ~70% built, looks cheap | Enrichment writes are writes, and one writer stalls readers 99×; a backfill would be an outage. Needs T0's merge fix, then T1–T2 |
| **The cross-optimizer demo** — the most differentiated idea | Needs T1–T4 plus a comparability agreement (T7). The reachable subset is the §10.2 knowledge plane at T5 |
| **LLM-judgement enrichment** (quality/security scores) | Weakest demonstrable value, highest honesty hazard (§10.1). Fix the fact/judgement distinction before promoting it at all |
| **Scale** | Nothing is worth scaling until it is correct and identifiable. T6 gates *promoting* T5/§10, it is not a growth project |
| **Make git the source of truth** (tree hash = digest), SBS a pure index | Right for provenance and distribution, and adopted in part: record resolved commit SHAs, keep original bytes, mirror namespaces to a git remote (§8.6). Wrong as the *only* identity: a git tree hash equals neither Harbor's digest (which ignores mode and hashes per-file hex digests) nor cap-evolve's scoped digest. Git also gives no compare-and-set across objects, no partial-read authz, and no TTL. SBS computes its own digests and treats git as origin and mirror |

---

# Part II — The technical case

## 5. How the eight systems persist agent artifacts today

All **[read]**. Commits: cap-evolve `c2a4aa6`, Arbor `7cdaf1f`, Harbor `15da91c`, GEPA `d771eb2`,
SkillOpt `79124b3`, CORAL `0123dfb`, AEH `55357f7`.

### 5.1 cap-evolve — candidate dirs, git, and a JSONL event spine

Optimizes system prompts, **tool code**, MCP tool surfaces and whole skill packages against the
user's own eval, with a val-only significance gate and a sealed test split. **Default persistence is
git**: a fresh repo per run (`git init`, identity `cap-evolve@local`, no remote), committed after
every iteration, accepted or rejected, so the process is `git log`-browsable. `rollouts/` and
`work/` are gitignored. `store.py` also ships an exclusive alternative, a `command` backend for
*"user-supplied shell commands for commit/snapshot, e.g. to push a skill to a skills store
(`npx skills publish …`) or any external versioning system."* It fires on accepted iterations only.
That is an empty socket waiting for exactly this.

```
.capevolve/run_<ts>/
  state.json  splits.json          # atomic write + file lock: carry the seal and budget
  graph.jsonl                      # candidate DAG: parents[] (2+ = merge), cluster_ids,
                                   #   edit_kind, micro_tests, subset, status, gate row
    rejected.jsonl history.jsonl     # negative + positive memory
  eval_cache.json  baseline.json   # GEPA train-minibatch cache (one JSON line)
  candidates/<id>/  rollouts/  events.jsonl  screens/  wiki/  JOURNAL.md
<per-iteration optimizer workdir>/
  LEDGER.md PROCESS.md RUNMAP.md prior_iterations/   # earlier iterations of *this* run
```

Four mechanisms worth importing:

- **`cache.hash_candidate_dir()`** — SHA-256 over sorted relative paths + content, with no file
  mode, *excluding* a declared ignore-set (`MEMORY.md`, `JOURNAL.md`, `.git`, `trajectories/`,
  `prior_iterations/`, …). Two byte-identical candidates share cache entries under different ids.
  **A digest with a declared scope.** A store that hashes everything produces digests cap-evolve
  cannot use. The cache it keys serves GEPA train minibatches only (1 trial), not the val gate.
- **`integrity.py`** — content-hash manifest of protected paths, verified before the gate decides,
  with precise semantics: *"Tamper means the score is not data … folding a 0.0 into the mean would
  poison both the split mean and the paired gate."* Host-independent by design, because a Claude Code
  hook *"does nothing when the optimizer is Codex, Gemini, a bare shell, or anything else."*
- **`memory_skill: wiki`** (opt-in; the template default is `md-files`) — a weakness graph: nodes with `status`, `affected_tasks`,
  `related[] {slug, why}` (typed, annotated edges), solution cards with diffs, and an append-only
  **Rejected Store Memory**. Plus a **freeze rule** — `affected_tasks` may only grow in the discovery
  iteration *"because solutions are scored against that exact task set"* — i.e. field-level
  immutability enforced by a paragraph addressed to an LLM.
- **`footprint.py`** — which val tasks an edit could causally reach, so out-of-footprint tasks are
  Δ=0 *by construction* rather than noise. Notably it **abstains** (returns `None`) rather than
  manufacture confidence; on the measured run 5 of 7 edits had no available restriction.

**What consuming SBS costs it today.** `spa_env.py` is 1473 lines at the pin (1539 as
`blackbox_env.py` at HEAD). About 325 of them (~160 executable) are store-object management that
exists only because of SBS behaviour. Another ~120 patch SBS and SPA source logging at the pinned
tag. The comments name the causes:
- The cascade is unusable: it runs while the skill is still its own tools' dependent, and the error
  is swallowed.
- Deletes need retry-until-no-progress, because there is no topological order.
- The dependent-kind list was derived by *reading SBS's source*.
- `Protection` spares the frozen primitives from deletion client-side.
- `_list()` handles two response shapes.

Tags are written by GET→mutate→PUT. That is the doc's diagnosis (no PATCH, M8), not one of
cap-evolve's comments. Finally, the SPA skill states that **"SPA serves exactly ONE skill"**, so
every candidate deploy resets the entire store and restarts SPA. That binding, and SPA's fixed ports,
belong to SPA rather than SBS.

### 5.2 Arbor — one JSON tree, git branches, per-session experience

Durable state is the **Idea Tree**: one JSON file (canonical) plus generated Markdown. A node carries
`hypothesis`, `status` (pending/running/done/needs_retry/merged/pruned), `insight` (direct *and*
back-propagated), `score` + **`score_split`** (dev|test), `test_score` (set only at merge),
**`code_ref`** (a git branch), `related_work`, `grounding` (citations), **`eval_status`**
(scored|skipped|failed_to_run|tampered), `stop_reason`, `attempt`.

Three things stand out. **The split travels with the number** (`score_split`), and `eval_status`
distinguishes *scored* from *skipped* from *failed_to_run*. **Artifacts are git branches** in isolated
worktrees. The coordinator's merge tool always re-runs `eval_cmd_test` on the source branch in a
fresh worktree (*"The LLM cannot bypass this"*), so it never trusts a branch's self-reported score.
The MCP merge path re-runs only when no `test_score` is supplied. **Retrieval leads with negative knowledge**:
`get_constraints_block()` emits TREE SHAPE → ROOT INSIGHT → *"PRUNED LESSONS (N — these directions
FAILED. Do NOT re-propose any idea that shares the same hidden assumption…)"* → VALIDATED FINDINGS.

`protected_paths` are hash-verified at runtime; on mismatch the dev score is **discarded** and the
branch becomes unmergeable. Contamination is declared per benchmark (`release_date`, `is_public`,
`canaries[]`) and assessed at INIT. Self-evolution writes typed findings — *leverage* and *pitfall* —
into `findings.jsonl`, consolidated into `EXPERIENCE.md`, and reused at intake with `[xN]`
recurrence ranking. By design, *"experience lives per session, not in a global library."* `.arbor/`
is gitignored and nothing is pushed: git holds code branches only, locally.

### 5.3 Harbor — the registry half, built, and aimed elsewhere

Task package → isolated environment → agent → verifier → result artifacts, fanned out across
tasks × agents × models × attempts. **Harbor already is a versioned artifact registry, for tasks and
datasets only** (`PackageType = "task" | "dataset"`). The task is the atomic unit, and a dataset is a
collection of tasks *at specific versions*. *"Every published task or dataset is versioned by its
digest, a revision number, and optional tags."* Package refs resolve as `org/task@tag`,
`@revision` and `@sha256:<hash>`. Hub stores datasets, tasks, jobs, trials and trajectories. Its
scope is explicit: a publishing target *"rather than a development platform"*, so you publish from
VCS, *"similar to how Docker or PyPI work."*

**Skills are a job input with lockfile provenance**, and that is the integration point that
matters most. Harbor resolves git skill sources once per job. Per trial, it uploads each skill to
`/harbor/skills/<name>` and hands the directory to the agent integration. The three source forms:
- a local path;
- `<org/repo>[@ref]`, which is GitHub shorthand defaulting to the `skills/` subdir;
- `https://<any-host>/…/tree/<ref>/<path>`.

`<ref>` is a **git refname**, resolved by `git ls-remote`, so a branch, tag or HEAD works and a raw
SHA does not. These are not Hub's `@revision`/`@sha256:` package refs. Harbor sparse-fetches the
resolved commit, deletes `.git`, and caches it under
`~/.cache/harbor/skills/<host>/<org>/<name>/<sha>/`. Each trial's `lock.json` records `name`,
`source` (the cache path), **`digest`**, `git_url` and `git_commit_id`.

Harbor computes the digest itself: SHA-256 over component-sorted `(relative path, hex SHA-256 of
content)` pairs. **File mode is not included.** It is neither a git tree hash nor any canonical form
proposed here. **This is the contract a skill source must satisfy. Every git host already does; SBS
does not, because it serves no git remote.** Hub uploads each trial's full lock, skill digests
included, but indexes skills by nothing.

Three more mechanisms to import: **leaderboards** separate the artifact from the claim about it
(rows of `{metadata, metrics, status, trial_ids[]}`, with *"Hub intentionally does not calculate row
scores from linked trials"*), edited **transactionally** with `--dry-run` and timestamp-based
optimistic concurrency; **regrade** re-scores recorded outputs with an updated verifier and no agent
re-run, which only works because artifact collection is manifested; and **metrics are code**
(`metric.py` shipped in the dataset's `[[files]]`).

**ATIF v1.8** is a mature versioned trajectory interchange format — `schema_version`, `trajectory_id`,
`session_id` (run-scoped), `agent{name, version, model_name, tool_definitions}`, `steps[]` with
`source`/`tool_calls`/`observation`/`metrics` (incl. prompt and completion token ids, the latter *"avoiding retokenization drift"*), `final_metrics`, `subagent_trajectories[]`. Two structural consequences: a trajectory can be
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
  `program_at_pareto_front_valset: {val_id: {candidate_idx}}`, persisted in the pickle and
  optionally mirrored, keyed by iteration id, to `pareto/{instance,objective,cartesian}_front.json`
  (`write_agent_state`, off by default). GEPA *selects* by sampling the frontier, so this is
  load-bearing.
- **Independent validation of the concurrency argument**: iteration ids became random 8-hex strings
  *"so the `iterations/` tree stays collision-free under concurrent/parallel proposal backends — where
  a monotonic `state.i` counter is neither unique nor necessarily available (e.g. agent-swarm
  engines …)."*
- `iterations/<id>/` dirs are **immutable once `meta.json` exists**; `validation_schema_version` is
  persisted; `parent_program_for_candidate` is a list *of lists* (multi-parent merges).
- Weakness: resume truth is a **pickle** (`gepa_state.bin`, cache included). It is unqueryable,
  shareable only by copying the whole run dir, and unsafe from an untrusted source.
  `optimize_anything`'s optional disk cache is `fitness_cache/*.pkl`: one pickle per entry, keyed by
  candidate and example-content hashes, with no split, evaluator or model.

`oa/ensemble.py` composes **multiple engines over one task** with five strategies:
- `sequential` (*"Monotonic: an engine that regresses doesn't poison the chain"*);
- `adaptive_sequential`, which rotates on score plateaus and shares one budget;
- `parallel`, `best_of` and `vote`.

The engines include Claude-Code-driven ones (`autoresearch`, `meta_harness`). Budget semantics are
explicit: *"Budgets are pre-partitioned per config; if an engine finishes early its leftover budget
is not redistributed,"* with `adaptive_sequential` as the stated exception. `oa/eval_server.py` is *"the single choke point for evaluation, budget, and
tracking,"* exposing both an in-process call and **`POST /evaluate` for external/black-box engines**,
returning **429** when the budget is exhausted. See §10.2.

**gskill** (inside GEPA): SWE-smith mines a repo into ~300 verifiable tasks; GEPA optimizes a skill
against train ~200 / val ~50 / test ~60 in the blog's experiments (CLI default test size: 100).
Results in §3.3. Three consequences: skills are
agent-agnostic transferable artifacts; **transfer is not uniformly positive**; and **duration is a
co-metric that sometimes moves alone** — a single-scalar reward would call the saturated runs
worthless.

### 5.5 SkillOpt — a skill is a trained parameter; Sleep is the enrichment pipeline

Treats the skill document as *"the trainable state of a frozen agent"* (README), trained with epochs,
minibatches, a textual learning-rate budget, a **rejected-edit buffer** and an epoch-wise slow/meta
update, gated on held-out validation. Deployed artifact: a 300–2,000 token `best_skill.md` adding
**zero inference-time model calls**. Results span 52 `(model, benchmark, harness)` cells — **harness
is a distinct dimension from model and agent**. Artifacts are keyed by what they were trained for:
`ckpt/<benchmark>/<model>_skill.md`.

**SkillOpt-Sleep** is §10.1's pipeline, shipped:

```
harvest transcripts (Claude Code / Codex / Copilot / Cursor / Pi / OpenCode / Devin)
  → mine recurring tasks → replay → consolidate (reflect → bounded edit → GATE on held-out tasks)
  → stage proposal → (you) adopt
```

Two elements **correct** my earlier design: **review-then-adopt** — *"nothing live changes until the
user explicitly adopts it"*, meaning enrichers should be able to *propose*, not only write. The
other is the optional `gate_no_regression` (default off), where *"a missing task result or
non-finite task score also blocks the candidate."* Its `evidence.jsonl` is on by default and holds
redacted, truncated copies of every prompt and reply. It comes with an instruction to *"treat it as
sensitive local data and apply an appropriate retention policy"* and an honest caveat that outbound
prompts are *"not currently guaranteed to be secret-free."* SkillOpt's own repo gitignores
`.skillopt-sleep/`.

SkillOpt distributes itself as **seven host integrations**. Two of them (Copilot, Devin) are MCP
servers exposing seven `sleep_*` tools each, and DeepSeek Harness exposes the same seven as native
tools. That is exactly the surface shape §8.11 proposes, so that plan is not speculative.

### 5.6 CORAL — multi-agent co-improvement of a shared skill set, working

Long-running agents *"explore, reflect, and collaborate through shared persistent memory,
asynchronous multi-agent execution, and heartbeat-based interventions"* in isolated git worktrees.
SOTA on 10 tasks, with **3–10× higher improvement rates at far fewer evaluations** than fixed
evolutionary baselines. On Anthropic's kernel task, four co-evolving agents took the best known score
from 1363 to **1103** cycles. The gains are attributed to *"**knowledge reuse** and multi-agent
exploration and communication."* The ablation confirms the first half: turning off notes and skills
costs 18.6%.

```
.coral/
├── public/                            # shared; symlinked into every agent's worktree
│   ├── attempts/<commit-hash>.json    # MEASUREMENTS
│   ├── notes/                         # FINDINGS: per-experiment files + shared index.md, _connections.md
│   ├── skills/<name>/SKILL.md         # SHARED, AGENT-WRITABLE SKILLS
│   └── eval_count                     # global budget counter (unlocked read-modify-write)
└── private/                           # grader venv + answer keys — agents CANNOT read
```
(Multi-island runs keep this state under `.coral/islands/<id>/`. `.coral/public` is a local git
repo, checkpointed with `git add -A` under a host-local `flock`. There is no remote.)

An attempt is `{commit_hash, agent_id, title, score, status, parent_hash, timestamp, feedback}`, with
a single parent, and `status ∈ {pending, improved, baseline, regressed, crashed, timeout, reverted}`.
That is §8.7's measurement record
almost field for field, including crash/timeout as statuses distinct from a score.

Three mechanisms are new to this study. **Scope isolation as a *search* strategy**: islands exist so
each *"can explore a different region of the solution space without immediately converging on the same
ideas"* — diversity maintenance, not just collision avoidance. **Migration between scopes**, policied:
*"selects strong agents … without worsening island roster balance."* **Two-level visibility**: scoped
from inside a worktree, aggregated across islands from outside. And the access model is a precise
specification of what §10.2 needs — own worktree read/write, **sibling worktrees read-only**, shared
area read/write, **evaluation internals no access** . It is enforced by a workspace guard hook, and in CORAL's Docker session by OS-user isolation
(opt-in on the host), under which agents cannot read the grader venv *"not even via Bash."*

Its limitation is the gap: one directory, one machine, one run.

### 5.7 AgentEvalHarness — judges are reward models; MLflow is the incumbent

Evaluates skills and agent capabilities from one declarative `eval.yaml`:
`analyze → generate cases → run → judge → trace in MLflow → optimize`, running locally, in **Harbor
containers**, or on EvalHub. LLM + code judges, pairwise A/B, thresholds, and *"`/eval-optimize` proposes skill fixes from failures and re-runs."*

**The judge is a reward model.** The judge engine already runs as the *verifier* inside Harbor trial
containers (`reward.py → reward.json`). The `/eval-train` design, mostly marked planned, has NeMo Gym
wrap that as an agent and feed `(trajectory, reward)` to NeMo RL / TRL / Unsloth / VeRL for GRPO/DAPO
weight updates, with NeMo Gym's `collect_rollout_details: true` capturing token ids. **[judgement]** This makes the evaluator/judge arguably the highest-leverage
artifact in the stack and the least version-controlled: change the judge and you change every reward,
every gate decision, and every trained model.

**MLflow is an incumbent worth naming.** AEH persists to MLflow: experiments, datasets, hierarchical
traces, per-judge feedback, and in CI, `commit_sha`/`harness_fingerprint` tags. GEPA logs to W&B and
MLflow (`use_wandb` / `use_mlflow`). The honest boundary is a layering, not a competition:

| Layer | Incumbent | Keyed by |
|---|---|---|
| execution substrate | Harbor, AEH | job / trial |
| run tracking | **MLflow, W&B** | experiment → run |
| **artifact registry** | **— the gap** | **artifact version digest** |

MLflow is run-centric. Its Model Registry versions models, and its **Prompt Registry** versions
prompts, with immutable versions and aliases. That covers a judge's prompt, but not a skill as a
content-addressed multi-file bundle whose identity outlives any run, and not the evidence bound to
it. AEH uses neither registry today. SBS measurements should be **joinable to** an MLflow/W&B run id, never
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
- **Git-aware import and a digest-addressed publish protocol.** `import-anthropic` with
  `source_type=url` does a shallow, blobless, sparse `git clone` of a GitHub path and records
  `extra.origin`. `npx skills add` (#326) is served from an open `.well-known/agent-skills/index.json`
  whose entries carry `sha256` digests. Both are the seed of "index and mirror git" (§8.6), with two
  gaps: `extra.origin` records the branch name as typed, not the resolved commit, and the published
  archive is a regenerated, lossy export rather than the original bytes.

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
every file as UTF-8 and **drops** what fails to decode. (`is_text_file()`'s 19-extension allowlist
gates only `snippet_mode="paragraph"`; in `"file"` mode — the API default — every file is processed, so
`.csv`/`.sql`/`.jsonl` survive and the losses are specifically **binary** files.)

### 6.3 Persistence, indexes, runtime

- **Storage**: local filesystem, one directory per UUID. Atomic writes; path-traversal validated.
- **Git persistence is not a backend** — it is `ShellHook`, env-var-templated
  `subprocess.run(shell=True)` fired per write, typically `git add && commit && push`. No transaction,
  no atomic multi-object commit. And what it commits is SBS's object store, one directory per UUID,
  not the skill as published, so a pushed repo is not one Harbor or `npx skills` could consume.
- **Caches — all in-process and authoritative**: `DictCache` (every object dict in memory),
  `LookupCache` (name → HEAD), `DependencyManager` (reverse deps, rebuilt at boot by full scan). None
  persisted.
- **Semantic index**: FAISS/Chroma/LanceDB over **descriptions only**, 384-dim MiniLM, default `k=5`,
  `similarity_threshold=1` as an L2 *distance* ceiling (squared L2 on the default FAISS backend).
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
| **M2** | **Licence fabrication.** `algorithmic-art` declares `license: Complete terms in LICENSE.txt`; the file is Apache-2.0; SBS emits `license: Proprietary. LICENSE.txt has complete terms`. 14/81 of the corpus altered |
| **M3** | **Silent binary loss.** `web-artifacts-builder` lost `scripts/shadcn-components.tar.gz`, `canvas-design` 54 `.ttf` fonts, `theme-factory` its `theme-showcase.pdf`, all with `ignored_files: []` |
| **M4** | **Executable bits lost** on 38 files across 15/81 skills, every one a script the skill invokes (exports set every entry to `0644`) |
| **M5** | Frontmatter reduced to `{name, description}`: 36/81 skills lose keys, and 4 more lose all frontmatter on a YAML error. `_mean`, an underscore-private helper, became a first-class public tool — contradicting the contract cap-evolve's SPA skill documents and relies on |
| **M6** | Re-importing the same skill dir creates a new skill version **but duplicates every tool and snippet by name** (3 tools → 6, 6 snippets → 12); `GET /tools/compute_score` silently resolves to one |
| **M7** | `DELETE /skills/{n}?delete_tools=true&delete_snippets=true` → **200**, `deleted_tools: []`, 18 objects orphaned. The cascade is inert and reports success |
| **M8** | No `ETag`, no `Last-Modified`; `PATCH` → **405** on tools/skills/snippets. Full-replace `PUT` is the only mutation |
| **M9** | **Lost update reproduced first attempt**: two barrier-synchronised GET→modify→PUT cycles both returned 200; one write vanished silently. This is precisely `spa_env`'s tagging pattern — and the tag it writes is the `FROZEN_TAG` that protects the benchmark's primitives from deletion |
| **M10** | `PUT` **mutates in place** — same UUID, `parent` still null, no new version — and being full-replace it **wiped `tags`** (and `extra`, and reset `state` to `approved`). So any measurement can be invalidated by a later `PUT`, undetectably |
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
                            ├─> nothing SBS serves matches its source byte for byte (Harbor's lock, npx digests)
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
| C3 | Source digests, refs, git remote | no digest of the source bundle (#326's npx digest covers a regenerated export); `version` a free string hardcoded to `1.0.0` on import; non-HEAD versions reachable only by raw UUID; no `@tag`/`@rev`/`@sha256` store refs; no git remote for Harbor's named-ref sourcing | **P0** |
| C4 | Atomic publish + declarative apply | import creates tools one-by-one and **swallows per-item failures**; a partial skill is reported as success. No transaction, no apply | **P0** |
| C5 | Measurements as records | none — score tags + free text, overwritten on re-eval | **P0** |
| C6 | Shared eval cache by digest | none (no digest) | P1 |
| C7 | Sealed splits / tamper / receipts | no splits, no protected paths, no signing; every import lands `approved` | P1 |
| C8 | OCC + PATCH + async indexing | no ETag/If-Match; PATCH 405; full-replace PUT; ~400 ms synchronous write path | **P0** |
| C9 | Namespaces + workspaces | `namespace:` tag prefixes; names global; ACL `scope:` designed but **deferred**; no owner field | **P0** |
| C10 | Typed labels, verbatim frontmatter, query | `tags[str]`, untyped `extra{}`; substring match; AND-only tags; offset pagination; fixed 5-state lifecycle | P1 |
| C11 | Hybrid body retrieval + findings + assembly | description-only embeddings; no lexical, no pre-filter, no findings kind | P1 |
| C12 | Provenance, contamination, secrets, licence | `extra.origin` for URL imports only, recording the branch name, not the resolved commit; no contamination fields; no secret scanning; **licence actively fabricated** | P1 (licence **P0**) |
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
   bind to nothing, and nothing SBS serves can be traced to its source.
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
| `Protection` (deletion guard for frozen primitives) + tripwire + fail-closed reads | C7, C9 | immutable workspace pins |
| `reset_store_to_skill()` | C4, C9 | one `:apply` call |
| GET→mutate→PUT tagging | C8 | `PATCH` / labels-at-create |
| `_list()` dual-shape handling | C10 | one response envelope |
| `public_functions()` AST re-implementation | C1, C2 | server-side projection (and M5 shows the client's assumption is currently wrong) |
| "SPA serves exactly ONE skill" + restart per candidate | C9 (plus SPA itself) | workspace-bound resolution, *and* SPA resolving per workspace on more than fixed ports |

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
boundary, not a tag prefix. **Store refs** adopt Harbor Hub's *package* grammar (tag regex
`^[a-z0-9][a-z0-9.\-]*$`), extended with a workspace pin. Under Harbor's parser that pin is
indistinguishable from a tag. These are store refs. Harbor's *skill* sourcing takes git refnames
instead (§8.6):

```
ns/name              ns/name@1.4.0        ns/name@17           # latest · tag · revision
ns/name@sha256:ab12… ns/name@latest       ns/name@<workspace>  # digest · explicit · workspace pin
```

**Immutability.** A published version never changes: `PUT` on a version → `409`. Mutations create a
new version; tags and `latest` are mutable pointers moved by compare-and-set. Deletion is **yank**
(unresolvable by `latest`, still resolvable by digest, measurements stay interpretable) — never history
rewrite, which silently invalidates every recorded measurement.

**Three digests**, because they answer different questions:
- `content_digest`: every byte and every mode. This is SBS's identity. Canonical form: entries
  sorted by relative path, each contributing `path\0mode\0content\0`. **Include mode**, because
  cap-evolve's and Harbor's digests both ignore it, and a store digest must be able to detect M4.
- `capability_digest`: with a declarable `digest_ignore` scope. This is what cap-evolve's eval cache
  needs, since it deliberately excludes optimizer scratch.
- `harbor_digest`: a byte-for-byte reimplementation of Harbor's `compute_skill_digest` (SHA-256 over
  component-sorted `path\0hex(sha256(content))\0`, no mode, `sha256:` prefix). It exists only so a
  Harbor job lock resolves back to an SBS version.

A git tree hash equals none of these. It is recorded as provenance (`origin.commit`), not used as
identity.

**Lineage.** `parents[]` — plural, for merges, which cap-evolve, Arbor and GEPA all produce —
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

**The declarative apply** replaces the multi-call, retry-looped deploy dance:

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

Harbor needs a git smart-HTTPS remote that exposes each skill as a directory at a **named** ref.
Harbor itself resolves the ref to a commit (`git ls-remote`), shallow-fetches it, and computes its
own digest. Three options, cheapest first.

**(a) Mirror to an existing git host. This needs no change to Harbor or the git host.** Publish a
namespace's versions as commits to a GitHub, GitLab or Gitea repo, one directory per skill, with each
version tagged (`refs/tags/<name>-<version>`). Harbor's existing
`--skill https://github.com/<org>/<repo>/tree/<tag>/<skill>` then resolves. Harbor records the commit,
and SBS records `(commit, harbor_digest) → version`, so the job lock resolves back to an SBS ref. The
mirror is also how SBS "indexes and mirrors" git rather than competing with it (§1.3). It needs the
original bytes (B1), not today's regenerated export. Until then, a mirror would publish the M1–M5
damage.

**(b) SBS serves git itself.** Expose read-only smart-HTTP `upload-pack` at `/git/<ns>.git`.
`git-http-backend` over a bare repo is enough. It must support `ls-remote`, a shallow fetch of an
advertised ref tip, and named refs: no `/` in the ref, and never `sha256:…`, which is an illegal
refname. `harbor run --skill https://sbs/git/runA/tree/<tag>/probe-skill` then works. In practice
this means SBS runs a git server. It only beats (a) for private or air-gapped deployments.

**(c) Native.** `…/bundle` plus a small `sbs://` resolver, or a vNFS/WebDAV/FUSE mount of a workspace at
`environment.skills_dir`.

**Inbound**, closing the loop: `POST /v2/jobs:import` reads a Harbor job dir or Hub UUID → trajectories
+ measurements, mapping trial → measurement, `result.json` → metrics, ATIF traces → trajectory bundles,
and the job lock → the measurement `context`. Matching on the lock's `(name, digest)`, Harbor's own
`AgentSkillLock` equality, joins every trial to the SBS version it ran. That is the skill-keyed index
Hub lacks.

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
  "producer": {"harness":"cap-evolve/0.1.0","agent":"claude-code","model":"…",
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
changing what is live. This is SkillOpt-Sleep's review-then-adopt: nothing live changes until the user
adopts. For a single team, the git equivalent is a pull request, and a mirror (§8.6a) can surface
proposals that way.

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
  regrade-staleness labels when a judge version moves. They are reward models in RLAIF. Where a
  team already uses MLflow's Prompt Registry, a judge's prompt version is *referenced*
  (`prompts:/…`), not duplicated.
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
| **Git mirror / read endpoint** | Harbor `--skill https://…`, any git client | be a skill source with no client change | the mirror to an existing host is the cheapest Harbor integration (§8.6a) |
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
| **Harbor** | git mirror to any host, or SBS git endpoint, or workspace mount (outbound); `jobs:import` (inbound) | **0** |
| **Arbor** | a plugin: `RecordFinding` → `POST /v2/findings`; intake recall → `findings?rank=recurrence`; `code_ref` → version | one plugin |
| **GEPA / gskill** | `optimize_anything` callback publishing candidates; `:seen` before each rollout; gskill publishes per-repo skills + transfer matrix | small adapter |
| **SkillOpt** | Sleep's staging target becomes a store **proposal**; `ckpt/` published with `(benchmark, model)` labels | small |
| **CORAL** | `.coral/public/{attempts,notes,skills}` backed by the store; islands → workspaces | moderate |
| **AEH** | judges published as versioned artifacts; measurements carry `judge@digest` + `run_ref` | small |

**cap-evolve, concretely.** Seam 1 needs no cap-evolve code change, only a small shell wrapper.
`{dir}` is the run root, not the candidate. `{tag}` is `best` on accept and empty otherwise. The
command fires on accepted iterations only. `{msg}` is substituted unescaped into `shell=True`. And
`store: command` *replaces* the git backend rather than adding to it:

```yaml
store: command
store_commit_cmd: >-
  sbs-capevolve-publish --run {dir} --namespace "$CAPEVOLVE_RUN_ID" --name probe-skill
# the wrapper resolves the accepted candidate from {dir}/state.json, publishes
# that candidate's skill directory as a bundle, and labels it from graph.jsonl
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
asynchrony, since they can all run code. It is **amortization and cross-corpus scope**: a place where
every run's artifacts coexist *and* a derivation can be cached against an immutable digest, then
reused by a different run, framework or user. A shared repo co-locates artifacts too. Running the
derivations and attaching attributable labels to them would need CI conventions that nobody has
written. That is the honest comparison.

**It is ~70% built**: async dispatch (`on_content_*`), per-plugin owner identity (so *"a scanner's
coverage does not become a function of who uploaded"*), **additive-union** `update_*_tags` (which `evaluator` and `security` bypass, see below), a
producer-namespaced closed outcome vocabulary (`result`/`skip`/`error`), two live subscribers, and six
of sixteen plugins that are pure derivations.

**The value is real, demonstrated, and concentrated in deterministic work.** The ~90-line enricher
behind Appendix B found 14 verifiable licence fabrications, 38 at-risk exec bits and 7 destroyed
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

**Fix before promoting.** The pipeline is racy by construction. `emit_event` runs handlers
concurrently, and the two live LLM plugins (`evaluator`, `security`) each read a whole object, await
an LLM, and write the stale whole object back. Last writer wins on tags *and* `extra`. And the `evaluator`
plugin writes `quality-score:8` as a **plain tag**, indistinguishable from human curation and one field
from real rewards — an LLM's opinion presented as evidence is exactly the failure this document is
organized against. Needs: the atomic merge (T0), a structural **fact vs judgement** class, typed
attributed labels (`computed_for_digest`, `plugin_version`, `deterministic`, `derived_from_splits`),
queryable enrichment state, triggers on *evidence* arriving, throttled backfill, and cost governance.

**Demos, in build order:** (1) the corpus defect census — done; (2) "the evidence panel nobody wrote";
(3) Arbor's `distill_abstract` moved off the run budget. **Not** downstream "optimizes faster" claims.

### 10.2 Bet 2 — several optimizers co-improving one skill set

**Does it make sense? Yes — validated three times.** CORAL: N agents co-improving a shared
`.coral/public/skills/`, SOTA on 10 tasks, with gains attributed to knowledge reuse and multi-agent
exploration. GEPA's `ensemble.py`: five composition strategies over one task. SkillOpt-Sleep:
transcripts harvested from six or seven agent hosts into one consolidation engine. What is
**unoccupied** is heterogeneity. GEPA composes engines registered in its own `oa` framework,
including Claude-Code-driven ones, under one orchestrator. CORAL composes agents in one run. Nobody
composes GEPA + SkillOpt + cap-evolve + Arbor. These share no process, runtime or dependency set, but
can share an artifact and a measurement. **A git remote is a workable meeting point for the
append-only half**: candidates, attempts and findings, one file per record, as CORAL's attempts
show. **It is not one for the mutable half**: one budget, an authoritative running best, a cache
with live lookup. Composition needs both, and the second half is where a store is the plausible
meeting point.

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
| **X1** | **Duplicate-work rate.** Two optimizers on the same seed; count how often they independently produce the **same `capability_digest`**, and how often the shared cache therefore skips a rollout | rollouts avoided; duplicate-edit rate | Needs a common content address across two processes *and* a live cache. A shared repo can count duplicates after the fact, but only a live lookup can skip the rollout. Build first |
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
| **`git` is a strong incumbent**, and a shared remote covers more than this document first claimed | Seven of eight chose it: free, offline, byte-exact, in every CI. A shared remote adds identity, lineage, review and authz across runs and people, and Harbor already consumes it | Do not replace it: **index and mirror it**. Record resolved commit SHAs, keep original bytes, and mirror namespaces to a git host (§8.6a). Claim value only where §1.3's seven limits apply: context-scoped evidence, shared mutable state, live lookup, partial-read authz, sensitive or expiring data, unsafe formats, runtime serving |
| **MLflow Prompt Registry overlap** | Versions prompts immutably with aliases, and is already deployed where AEH runs | Reference it for judge prompts; own only the multi-file bundle and the evidence bound to it |
| **Harbor Hub overlap** | Hub already registers tasks/datasets and stores jobs/trials/trajectories | Stay on the capability side; reference Harbor refs; make job-import the flagship integration |
| **MLflow / W&B own run tracking** | AEH persists to MLflow; GEPA has `--wandb`; teams have them deployed | Layer, don't compete: they are run-centric, SBS artifact-centric. Join via `producer.run_ref` |
| **Measurement schema bloat** | Every framework will want one more field | Small required core + `extra`; promote a field only when two frameworks need it |
| **The seal becomes a bypassed checkbox** | An honesty gate in a store the optimizer can also write to is weaker than it looks | Opt-in; advisory mode; be explicit that it raises the cost of *accidental* leakage, not deliberate cheating |
| **Migration dishonesty** | Backfilling through today's lossy exporter produces artifacts that *look* faithful | `provenance.reconstructed: true`; prefer re-import from `extra.origin` |
| **Retrieval claims without a benchmark** | "Better retrieval" is unfalsifiable as stated | Build the benchmark before the work; M12 is the baseline to beat |
| **A second source of truth for acceptance** | cap-evolve is explicit that its DAG is a *view*, not an authority | The store records verdicts; it does not decide them. The harness stays the referee |

**Non-goals.** Not a benchmark runner (Harbor). Not an optimizer (cap-evolve, GEPA, SkillOpt, Arbor,
CORAL). Not a development platform for tasks (Harbor's explicit and correct stance). Not a training
pipeline. Not a replacement for git — a peer that indexes and mirrors it.

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
9. **Mirror to a git host, or serve git?** §8.6a (mirror) costs no server and works with every git
   client. §8.6b (serve) is needed only for private or air-gapped deployments. I lean mirror first.
   Either way, which direction is authoritative when a mirror repo receives a direct push?
10. **Where is the git baseline good enough?** For a single team with append-only records and nothing
    sealed, a shared repo plus conventions may be the right answer. Should SBS ship those conventions
    (a `measurements/<digest>/` layout, a CI matrix builder) as a *non-service* product, and treat the
    store as the upgrade path?

---

# Appendices

## Appendix A — sources and measurement method

### A.1 Sources studied

| Source | What was read | Commit |
|---|---|---|
| **skillberry-store** | `src/skillberry_store/**` (51k lines of Python incl. tests, ~26k non-test), `docs/**`, `docs/design/**`, 16 plugins, `access_control_config.yaml` | `c6e72ce` |
| **cap-evolve** (`skillberry-ai/cap-evolve`) | `core/cap_evolve/*` (18.8k LOC), the 22-skill library, `ARCHITECTURE.md`, `ADAPTER_CONTRACT.md`, the SPA intervention (1473-line `spa_env.py`), `capevolve_harbor/` | `c2a4aa6` |
| **Arbor** (`RUC-NLPIR/Arbor`) | `src/coordinator/idea_tree.py`, `tools/git_ops.py`, `src/events/*`, `arbor-zoo/`, `docs/{self-evolution,skills,plugins,outputs-and-resume,zoo}.md` | `7cdaf1f` |
| **Harbor** (`harbor-framework/harbor`) | `registry.json`, `src/harbor/{skills,models/job/lock,models/package/version_ref}.py`, `docs-mintlify/core-concepts/**` (jobs/skills, harbor-hub/{publish,download,leaderboards}, results, datasets/metrics, jobs/regrade), `docs-mintlify/news/harbor-registry.mdx`, `rfcs/0001-trajectory-format.md` (ATIF v1.8) | `15da91c` |
| **GEPA + gskill** (`gepa-ai/gepa`) | `core/state.py`, `oa/{ensemble,eval_server}.py`, `proposer/merge.py`, `gskill/**`, the gskill blog + guide, arXiv:2507.19457 | `d771eb2` |
| **SkillOpt** (`microsoft/SkillOpt`) | `README.md`, `docs/sleep/README.md`, `plugins/**` (7 host integrations), `skillopt_sleep/staging.py`, `ckpt/**`, arXiv:2605.23904 | `79124b3` |
| **CORAL** (`Human-Agent-Society/CORAL`) | `coral/{hub,workspace}/**`, `concepts/shared-state.mdx`, `guides/multi-agent.mdx`, arXiv:2604.01658 (COLM 2026) | `0123dfb` |
| **AgentEvalHarness** (`opendatahub-io/agent-eval-harness`) | `README.md`, `agent_eval/{mlflow,harbor}/**`, `agent_eval/ci_context.py`, `skills/eval-mlflow/scripts/log_results.py`, `docs/{harbor-workflow,eval-train-harbor-nemo-skyrl}.md` | `55357f7` |

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

### A.5 Re-verification (2026-10-04)

Every **[read]** claim was re-checked against its source at the pinned commit, and every SBS
code-path claim at `c6e72ce` and `da481ee`. The corpus counts were recomputed. The corrections that
changed the argument:

- **The baseline.** The first draft compared the store with *local* git. Against a shared git
  remote, most "provable zeros" become "incremental over git" (§1.3, §1.5). None of the optimizers
  checked (cap-evolve, Arbor, CORAL) pushes run state to a remote. Only Harbor consumes one, for
  skills.
- **Harbor.** Harbor takes skills only by *named* git ref, never a raw SHA, and computes its own
  mode-less digest. Any git host already satisfies its contract. `content_digest` could never have
  matched Harbor's digest, hence `harbor_digest` and the git mirror (§8.2, §8.6).
- **GEPA.** The cache *is* reusable across runs by resuming or copying a run dir, and
  `optimize_anything` has a separate disk cache. "0 by construction" became "0 between independent
  runs".
- **AEH.** CI runs already carry `commit_sha`, so judge versions are indirectly recoverable.
  `/eval-train` is mostly a design. MLflow's Prompt Registry is the named incumbent for judge prompts.
- **CORAL.** Single-parent lineage (✓ removed). The 3–10× figure comes from the main comparison,
  not the ablation. The flat-parse fallback is used for notes only.
- **cap-evolve.** The `store: command` example would have published the whole run dir. The cache
  serves GEPA train minibatches only. "SPA serves ONE skill" is SPA's property. HEAD renamed
  `spa` → `blackbox`.
- **SBS corpus.** The licence count is 14/81, not 17. The old 17 counted declared `license` keys.
  4 more skills lose all frontmatter on a YAML error. Any-damage is 47/81, not 44. The plugin race
  runs through whole-object writes, not `update_*_tags`. The allowlist has 19 extensions. Since
  `c6e72ce`, #326 made exports deterministic and added a digest over the *regenerated* export; M1–M5
  still hold.
- **Smaller fixes.** gskill used gpt-5-mini, not an open model. Arbor's per-session experience is
  deliberate. Several quotes were corrected to verbatim.

## Appendix B — the corpus experiment

Run to test whether deterministic corpus-scale enrichment produces real value (§10.1), and to replace a
single synthetic probe with evidence from artifacts people actually publish.

### B.1 Corpus — 81 skills, 759 files, five independent projects

| Source | Skills | Note |
|---|---|---|
| `anthropics/skills` | 20 | the reference corpus; includes `docx`/`pdf`/`pptx`/`xlsx` |
| `skillberry-ai/cap-evolve` | 26 | every `SKILL.md` in the repo: 22 under `skills/`, 2 templates, 2 example seeds |
| `obra/superpowers` | 15 | the agent set cap-evolve's optimizer registry matches |
| `RUC-NLPIR/Arbor` | 11 | `skills/*/SKILL.md` — its *agent* skills |
| `harbor-framework/harbor` | 9 | `skills/*/SKILL.md` at the HEAD cloned for B.7 (6 at the `15da91c` pin studied in §5) |

Scope note: Arbor has **two** skill systems. The 11 counted are its agent skills (`name` +
`description` only); its *Coordinator* Skills live in `src/skills/` and carry `when_to_apply`, which is
why that key does not appear in the tally below.

### B.2 Method

**(a) Simulation** — a ~90-line analyser replicating SBS's code paths at `c6e72ce`: `read_from_folder`
(UTF-8 only; failures dropped), `parse_skill_metadata` (keeps only `{name, description}`; `None` on a YAML error),
`is_code_file` (`.py`/`.sh`/`.bash` → tools), `parse_text_files` with `snippet_mode="file"` (the API
default), `exporter.generate_skill_md` (regenerates frontmatter; injects the `Proprietary` licence when
any text-file snippet's path contains "license", case-insensitive).

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
| **T2 legal/semantic** — licence replaced by, or given, a fabricated `Proprietary` claim | | **14 / 81 (17.3%)** |
| | 13 Apache-2.0 skills declaring `Complete terms in LICENSE.txt`, relabelled | 13 |
| | `skill-creator` (Apache-2.0, declares none), given one | 1 |
| | (`docx`/`pdf`/`pptx`/`xlsx` trigger it too, but already declare that string over an all-rights-reserved `LICENSE.txt`, so they are correct by coincidence) | (4) |
| **T3 metadata** — frontmatter keys discarded | | **36 / 81 (44.4%)** |
| | all frontmatter lost, `name`/`description` included, on a YAML error (cap-evolve: `mcp-tool`, `system-prompt`, `spa`, `intake`) | **4 / 81** more |
| any of T1–T3 | | **47 / 81 (58.0%)** |

Every lost exec bit sits on a script the skill invokes:
`anthropic-skills` docx(5) pptx(4) skill-creator(7) slack-gif-creator(4) xlsx(2) pdf(1)
webapp-testing(1) web-artifacts-builder(2); `superpowers` brainstorming(2) executing-plans(2)
subagent-driven-development(3) systematic-debugging(1) writing-skills(1); `Arbor` arbor-agent-tools(1);
**`cap-evolve` spa(2)** — cap-evolve's own SBS-integration skill.

Silently dropped payloads, all with `ignored_files: []`: `canvas-design/canvas-fonts/*.ttf` (54 files —
the skill's entire font library), `web-artifacts-builder/scripts/shadcn-components.tar.gz`, and — a
document, so excluded from T1 — `theme-factory/theme-showcase.pdf`.

**T2, verified live.** `anthropic-skills/skills/algorithmic-art` declares
`license: Complete terms in LICENSE.txt`; that `LICENSE.txt` contains the **Apache License Version
2.0**. Round-tripped through a running SBS, the export reads
`license: Proprietary. LICENSE.txt has complete terms`. The store asserts *Proprietary* over Apache-2.0
on Anthropic's own published skill, triggered only by a filename containing "license".

**T3 — the frontmatter the ecosystem actually uses.** 9 distinct keys appear; SBS preserves 2. Tallies
are over the 77 skills whose frontmatter parses as strict YAML. A line scan that includes the 4
failures raises cap-evolve's counts (e.g. `needs` 22, not 18):

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
      19-extension allowlist never applies and `.csv`/`.sql`/`.jsonl` survive. The actual dropper is the
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
