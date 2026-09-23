# `npx skills add` against a Skillberry Store — Findings & Implementation Proposal

`skills.sh` lets anyone install an agent skill with one command:

```bash
npx skills add vercel-labs/agent-skills
```

This document answers three questions:

1. **What actually makes that work** — the npm package behind `npx skills`, and, critically, the *server-side* contract that lets a host publish skills to it (§1–§2).
2. **What skillberry-store already has** that maps onto that contract, and what is missing (§3).
3. **How to implement it** so that `npx skills add http://my-store:8000` installs skills straight out of a running SBS into Claude Code, Cursor, Codex, and ~70 other agents (§4–§8). §4.3.2 is the end-to-end flow from the user's point of view, and §4.3.8 records the decisive scoping decision: **one skill per install URL**.

**Headline result:** the capability is *not* a skills.sh-proprietary registry protocol. It is an open, documented-by-implementation HTTP discovery convention — `GET /.well-known/agent-skills/index.json` — that any web server can serve. SBS can become a first-class skills source by adding **two read-only GET endpoints** plus an access-control allowlist entry. No npm package needs to be published, and no registration with skills.sh is required.

Two sharp blockers were found in the existing export path (a non-deterministic ZIP and a `<skill-name>/` archive prefix) that would make a naive "just point the CLI at `/skills/{name}/export-anthropic`" approach fail. Both are detailed in §5 with fixes.

---

## 1. How `npx skills add` works

### 1.1 The npx entry point

`npx skills` resolves to the [`skills`](https://www.npmjs.com/package/skills) package on the public npm registry — **not** to anything hosted on skills.sh.

| Property | Value |
| --- | --- |
| npm package | `skills` (unscoped) |
| Version studied | `1.6.0` (published ~Sep 2026; 97 versions) |
| Maintainers | `rauchg`, `quuu` (Vercel) |
| Source | [github.com/vercel-labs/skills](https://github.com/vercel-labs/skills), MIT |
| Binaries | `skills`, `add-skill` |
| Runtime deps | `tar`, `yaml` (everything else bundled into a single 307 KB `dist/cli.mjs`) |
| Engine | Node ≥ 22.20.0 |

So `npx skills add <source>` = "download the `skills` CLI from npm, run its `add` subcommand". skills.sh is the *website and catalog*; the CLI is an independent npm artifact. **The `npx` half of the capability requires nothing from the server at all.**

Findings below come from disassembling the published bundle (`npm pack skills@1.6.0`), since the protocol is not written up in the repo docs.

### 1.2 Source resolution — a provider chain

`skills add <source>` walks a provider registry and takes the first match:

| Order | Provider | Trigger | Server-side requirement |
| --- | --- | --- | --- |
| 1 | GitHub / GitLab / Azure Repos / any git URL | `owner/repo`, `https://github.com/...`, `git@...`, `ssh://...` | A git repo containing `SKILL.md` files |
| 2 | Local path | `./my-skills` | none |
| 3 | **Well-known HTTP** | **any other `http(s)://` URL** | **`/.well-known/agent-skills/index.json`** ← *the one that matters for SBS* |
| 4 | Direct download | same URL, if #3 found nothing | a bare `SKILL.md` or a `.zip`/`.tar.gz` at the URL |

The well-known provider `match()` accepts **any** `http://` or `https://` URL and explicitly *excludes* only `github.com`, `gitlab.com`, and `huggingface.co`. `http://localhost:8000` and `http://sbs.internal:8000` both match. This is the extension point.

The CLI probes, in order, and takes the first candidate that parses:

```
{scheme}://{host}{basePath}/.well-known/agent-skills/index.json
{scheme}://{host}{basePath}/.well-known/skills/index.json
{scheme}://{host}/.well-known/agent-skills/index.json     # only if basePath was non-empty
{scheme}://{host}/.well-known/skills/index.json
```

Discovery timeout is **10 s**. A non-2xx response, or a body that is not JSON with a `skills` array, is skipped silently — a plain FastAPI `404 {"detail":"Not Found"}` is a perfectly well-behaved "no skills here" answer.

### 1.3 The discovery index — two schema versions

`normalizeIndex()` dispatches on the top-level `$schema` key.

#### v0.1.0 — legacy, no `$schema` key (server serves loose files)

```json
{
  "skills": [
    {
      "name": "pdf-forms",
      "description": "Fill and flatten PDF forms.",
      "files": ["SKILL.md", "scripts/fill.py", "reference/spec.md"]
    }
  ]
}
```

The CLI then fetches each file at `{indexBase}/.well-known/agent-skills/{name}/{filePath}`, with `SKILL.md` requested by that exact spelling.

#### v0.2.0 — current, explicit `$schema` (server serves artifacts + digests)

```json
{
  "$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
  "skills": [
    {
      "name": "pdf-forms",
      "description": "Fill and flatten PDF forms.",
      "type": "archive",
      "url": "/artifacts/sha256-1f3a…/pdf-forms.zip",
      "digest": "sha256:1f3a…64hex"
    }
  ]
}
```

`url` is resolved relative to the index URL, so a relative path is fine (and is the right choice — it survives reverse proxies and hostname changes).

> The `$schema` value is compared as a **literal string constant**; the CLI never fetches it. `schemas.agentskills.io` did not resolve from this environment, which is irrelevant to correctness — but it does mean the string must be reproduced byte-exactly.

#### Validation rules — reproduced exactly from the bundle

These are strict, and getting one wrong is the difference between "works" and "silently zero skills".

| Rule | v0.1.0 | v0.2.0 |
| --- | --- | --- |
| `name` matches `^[a-z0-9-]+$`, 1–64 chars, no leading/trailing `-`, **no `--`** | required | required |
| `description` non-empty string | required | required, **≤ 1024 chars** |
| `type` is `"skill-md"` or `"archive"` | — | required |
| `url` non-empty, parseable | — | required |
| `digest` matches `^sha256:[a-f0-9]{64}$` | — | **required** |
| `files` non-empty array, no absolute paths / `..` / NUL, and **must contain a case-insensitive `skill.md`** | required | — |
| **An invalid entry…** | **invalidates the WHOLE index** (`return null`) | is skipped; other entries still install |
| Archive must contain **root-level `SKILL.md`** | n/a | required |

That v0.1.0 asymmetry is a trap: one malformed entry in a 50-skill index yields *no* skills, with no diagnostic. v0.2.0 degrades gracefully. This is a strong argument for §4's phasing.

### 1.4 Artifact fetch, verification, and safety limits

For v0.2.0 the CLI fetches `url`, then:

```js
if (this.computeDigest(bytes) !== entry.digest) return null;   // silent drop
```

— i.e. **the index must publish the sha256 of the exact bytes the artifact endpoint will return.** A mismatch is not an error message; the skill just vanishes from the install list.

Archive handling (zip via a bundled inflate, or tar.gz):

| Guard | Limit |
| --- | --- |
| Unpacked archive size | 50 MiB (well-known path) |
| Files per archive | 1000 |
| Direct-download body | 10 MiB (`SKILLS_DOWNLOAD_MAX_BYTES`) |
| Direct-download extract | 25 MiB (`SKILLS_EXTRACT_MAX_BYTES`) |
| Symlink / hardlink entries | **rejected outright** |
| Absolute paths, `..`, `\`, drive letters, NUL | **rejected outright** |
| Root `SKILL.md` | **required, or the archive is rejected** |
| Frontmatter `name` + `description` | required, or the skill is dropped |
| `metadata.internal: true` | hidden unless `INSTALL_INTERNAL_SKILLS=1` |

No `Authorization` header, cookie, or credential is ever sent to a well-known host. **A token-guarded index is simply unreachable** — see §4.6 for the capability-URL workaround.

### 1.5 Install, lockfile, and update

Installed skills land in per-agent directories (`.claude/skills/`, `.agents/skills/`, `~/.cursor/skills/`, …; `-g` for global), symlinked to one canonical copy by default, or copied with `--copy`. A `skills-lock.json` records provenance. This repo already contains one, from GitHub-sourced installs:

```json
{ "version": 1,
  "skills": { "find-skills": { "source": "vercel-labs/skills", "sourceType": "github",
                               "skillPath": "skills/find-skills/SKILL.md",
                               "computedHash": "b146…" } } }
```

A well-known install instead writes `sourceType: "well-known"`, `sourceBaseUrl`, `sourceUrl`, and `wellKnownDigest`. `npx skills update` re-fetches the index with an **`X-Skills-Update-Check: 1`** header and re-downloads only entries whose digest changed. For v0.1.0 the digest is computed client-side over sorted `path\0content\0`; for v0.2.0 the server's published digest is used directly.

Practical consequence: **v0.2.0 gives the server control over update detection at the cost of one hash per skill; v0.1.0 needs no server-side digest at all** because the client hashes what it downloaded.

### 1.6 The catalog APIs (not needed for installs)

Separately from discovery, skills.sh runs a catalog used by `skills find`:

| Endpoint | Purpose | Notes |
| --- | --- | --- |
| `GET /api/search?q=&limit=&owner=` | leaderboard search | Public. Verified live: returns `{skills:[{id, skillId, name, installs, source}], count, duration_ms}` |
| `GET /api/download/{owner}/{repo}/{slug}` | GitHub snapshot mirror | Public. Verified live: returns `{files:[{path, contents}], hash}` |
| `GET /api/v1/*` | documented catalog API | Auth via **Vercel OIDC** (`VERCEL_OIDC_TOKEN`) |

Overridable via `SKILLS_API_URL` / `SKILLS_DOWNLOAD_URL`. These are *discovery-of-catalog* concerns, orthogonal to installing from a given host, and SBS does **not** need them (§8 revisits this as an optional stretch).

### 1.7 What skills.sh itself sets up

Mapping the site's features onto the mechanics above:

| Feature | Mechanism |
| --- | --- |
| `npx skills add owner/repo` | Nothing server-side — the CLI talks to GitHub directly |
| Leaderboard / install counts | Anonymous telemetry to `add-skill.vercel.sh/t`, aggregated |
| **Packs** — `npx skills add https://skills.sh/p/<pack-id>` | **skills.sh serving the well-known protocol at a scoped path**: `/p/<pack-id>/.well-known/agent-skills/index.json` (verified — §1.7.1) |
| Pack privacy | "Unlisted, not access-controlled": the unguessable `<pack-id>` in the path *is* the credential |
| Security audits | `add-skill.vercel.sh/audit`, surfaced pre-install |
| README badge | `https://skills.sh/b/{owner}/{repo}` |

**skills.sh's own "packs" feature is exactly the integration SBS should build.** A pack is a curated subset of skills published at a path-scoped well-known index — structurally identical to "publish an SBS namespace at a path-scoped well-known index".

The CLI's scope handling deserves note: given `https://host/p/abc`, if the scoped index is absent but a *root* index exists, it refuses to fall back and raises `WellKnownScopeNotFoundError` — "Not falling back to the root skills index because that would install every skill the host publishes." SBS gets that safety property for free.

#### 1.7.1 Does skills.sh itself serve a `.well-known` index?

Worth answering precisely, because the intuitive assumption — "skills.sh must host the index that makes `npx` work" — is wrong, and the real answer is more useful.

**For the headline command, no `.well-known` is involved anywhere.** `npx skills add vercel-labs/agent-skills` matches the GitHub provider (§1.2) and talks to `api.github.com` / `raw.githubusercontent.com` directly. skills.sh serves **zero bytes** in that flow; it is the leaderboard and the catalog, not a delivery path.

**skills.sh serves no *root* well-known index.** Verified:

| Request | Result |
| --- | --- |
| `GET https://skills.sh/.well-known/agent-skills/index.json` | `308` → `www.skills.sh`, then **`200 text/html`** (41,449 bytes — the Next.js app shell) |
| `GET https://www.skills.sh/.well-known/skills/index.json` | **`200 text/html`** (41,276 bytes — same shell) |

Both are `200`, so `response.ok` passes — but `await response.json()` then throws, and the `catch { continue; }` around each candidate (§1.2) discards it. `npx skills add https://skills.sh` therefore discovers nothing and falls through to the direct-download provider. That is not a defect: skills.sh has no root catalogue to publish, because its skills live in other people's GitHub repos.

**skills.sh does serve a *scoped* well-known index, for packs.** Two independent pieces of evidence:

1. **Code path.** `isSkillsShPackUrl()` (`cli.mjs:4405`) is called from exactly one place: `cli.mjs:4474`, inside `handleWellKnownSkills()` (`cli.mjs:4413`), where it pre-selects every skill in the multiselect prompt. A pack URL is therefore handled by the **well-known provider** — there is no separate pack transport in the CLI.
2. **Live behaviour.** The scoped path is a real route, distinguishable from the SPA catch-all by how it fails:

   | Request | Result |
   | --- | --- |
   | `GET /p/zzz-does-not-exist` (pack landing page) | `200 text/html` — SPA catch-all |
   | `GET /p/zzz-does-not-exist/.well-known/agent-skills/index.json` | **`404`** |

   If the scoped well-known path were merely falling through to the page router, it would have returned `200 text/html` like the landing page. It returns `404`, so a handler exists there and rejects unknown pack ids.

Three consequences for SBS, all of them favourable:

- **This is not an undocumented corner being exploited.** SBS would implement the same mechanism skills.sh uses for its own curated-distribution feature. The protocol is load-bearing for its author.
- **SBS's root index is legitimate, not an odd extension.** skills.sh has none because it owns no skills; SBS *is* the store, so a root index is the natural shape — and namespace-scoped indexes (§6.7) are then the direct analogue of packs.
- **§4.6's capability-URL recommendation is validated by precedent.** Unguessable id in the path, no credentials on the wire, unlisted rather than access-controlled: that is exactly what skills.sh packs do.

### 1.8 Telemetry — a real consideration for a self-hosted store

On a successful well-known install the CLI fires a GET to `https://add-skill.vercel.sh/t` with:

| Field | Value for an SBS install |
| --- | --- |
| `event` | `install` |
| `source` | `wellknown/<your-hostname>` |
| `installUrl` | the full URL you typed |
| `skills` | comma-joined skill names |
| `skillFiles` | JSON map of `{skillName: artifactUrl}` |
| `agents`, `global`, `v`, `ci`, `agent` | target agents, CLI version, CI flag, detected agent |

**File *contents* are not transmitted** — but internal hostnames, artifact URLs, and skill names are. The privacy suppression path (`isSourcePrivate`) only understands GitHub `owner/repo` visibility; it does not recognise a private well-known host, so an intranet SBS is reported like any public one.

Opt out with `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1`. This belongs in the operator docs SBS ships (§4.8), not as an afterthought. **§9 Q7 resolves *how*: documented as an exported `DO_NOT_TRACK=1`, not as a prefix on the command the store emits** — a prefix covers one invocation, breaks in PowerShell, and suppresses ecosystem install counts by default.

---

## 2. The contract, distilled

To be installable by `npx skills add <url>`, a server must:

1. Serve `GET {base}/.well-known/agent-skills/index.json` → JSON, `{skills: [...]}`.
2. Have every entry pass §1.3 validation — notably lowercase-hyphen names and, for v0.2.0, a correct sha256.
3. Serve each skill's bytes, with `SKILL.md` at the **root** of an archive (or as the whole body for `type: "skill-md"`).
4. Answer **unauthenticated** — no credentials are sent.
5. Answer within **10 s** for the index.

That is the entire server side. Everything else is the CLI's job.

---

## 3. What skillberry-store already has

SBS is unusually well positioned: the hard part — turning an SBS skill manifest into Anthropic-format `SKILL.md` + resource files — already exists and is already load-bearing for the vNFS frontend.

| Requirement | Current state | Reference |
| --- | --- | --- |
| Skill list with name + description | `SkillsService.list_all()`, served by `GET /skills/` | [skills_service.py:325](../../src/skillberry_store/services/skills_service.py#L325), [skills_api.py:92](../../src/skillberry_store/fast_api/skills_api.py#L92) |
| Manifests held in memory | `DictCache` — full dicts by UUID, no disk I/O per read | [dict_cache.py:9-21](../../src/skillberry_store/modules/dict_cache.py#L9-L21) |
| **Skill → Anthropic `SKILL.md` + files** | `_build_file_structure()` — the whole materialisation, shared by both export forms | [exporter.py:260-304](../../src/skillberry_store/tools/anthropic/exporter.py#L260-L304) |
| Skill → ZIP | `export_skill_to_anthropic_format()` | [exporter.py:307-332](../../src/skillberry_store/tools/anthropic/exporter.py#L307-L332) |
| Skill → directory tree | `export_skill_to_directory()` — already used to serve skills over WebDAV/NFS | [exporter.py:335-360](../../src/skillberry_store/tools/anthropic/exporter.py#L335-L360), [vnfs_server.py:239-249](../../src/skillberry_store/modules/vnfs_server.py#L239-L249) |
| HTTP ZIP export endpoint | `GET /skills/{uuid_or_name}/export-anthropic` | [skills_api.py:546-575](../../src/skillberry_store/fast_api/skills_api.py#L546-L575) |
| File paths carried on snippets | `file:<path>` tag convention | [exporter.py:11-28](../../src/skillberry_store/tools/anthropic/exporter.py#L11-L28) |
| Namespaces (→ packs) | `namespace:<x>` tags, enumerable via `GET /facets/skills` | [facets.py:14-40](../../src/skillberry_store/services/facets.py#L14-L40), [skills_api.py:312](../../src/skillberry_store/fast_api/skills_api.py#L312) |
| Lifecycle state (→ publish gate) | `new` / `checked` / `approved` on every manifest | [manifest_schema.py](../../src/skillberry_store/schemas/manifest_schema.py) |
| Public-path allowlist | `unauthenticated_paths`, prefix-glob matched | [config.py:198-205](../../src/skillberry_store/access_control/config.py#L198-L205), [config.py:227-231](../../src/skillberry_store/access_control/config.py#L227-L231) |
| No catch-all route to shadow `/.well-known/*` | `/ui/{path:path}` is scoped under `/ui`; `/` is a bare redirect | [server.py:491](../../src/skillberry_store/fast_api/server.py#L491), [server.py:538](../../src/skillberry_store/fast_api/server.py#L538) |
| Prometheus counters on export | `export_anthropic_skill_counter` | [skills_service.py:53-54](../../src/skillberry_store/services/skills_service.py#L53-L54) |

**What is missing is only:** the index endpoint, a CLI-shaped artifact endpoint, name slugging, and the ACL wiring.

### 3.1 Verified behaviour of the live demo

Probed against `https://skillberry-store-demo-adv.onrender.com`:

| Request | Response | Implication |
| --- | --- | --- |
| `GET /health` | `200 {"status":"healthy"}` | reachable |
| `GET /.well-known/agent-skills/index.json` | `404 {"detail":"Not Found"}` | clean, CLI-compatible miss; the path is free |
| `GET /skills/?fields=narrow&limit=3` | **`401 {"detail":"missing_authorization"}`** | ACL is **on** here — §4.5 is mandatory, not optional |

---

## 4. Proposed implementation

### 4.0 Scope: what this design does not change

#### 4.0.1 No filesystem export is involved

Worth stating up front, because §3 cites the vNFS frontend and the two are easy to conflate: **the proposal does not export, materialise, or mount a filesystem.** It is HTTP bytes end to end.

The reusable asset from §3 is [`_build_file_structure()`](../../src/skillberry_store/tools/anthropic/exporter.py#L260-L304), which returns a plain `Dict[str, bytes]` in memory. It has two independent consumers:

| Consumer | What it does with the dict | Touches disk? |
| --- | --- | --- |
| `export_skill_to_anthropic_format()` | zips it in an `io.BytesIO` | **no** |
| `export_skill_to_directory()` | writes it out with `Path.write_bytes` — this is the vNFS path | yes |

The well-known endpoints use the **first** consumer, not the second. The only filesystem contact in the request path is `ToolsService.get_module()` reading a tool's own persisted source through `FileHandler` ([tools_service.py](../../src/skillberry_store/services/tools_service.py)) — the same read `GET /skills/{name}/export-anthropic` already performs. That is SBS reading its own state, not an export.

Concretely, nothing in the design needs: a materialised skill tree, a `tempfile.mkdtemp()` scratch directory, a WebDAV or NFS daemon, an extra listening port, or a mount on the client. The vNFS machinery ([vnfs_server.py:231-249](../../src/skillberry_store/modules/vnfs_server.py#L231-L249)) does all of those — it materialises into a temp directory, starts `wsgidav` or an NFS backend on its own port, and `shutil.rmtree`s the tree on stop. **The npx path shares none of it.** vNFS appears in §3 purely as evidence that the in-memory materialiser is already trusted in production, so the npx work inherits a tested component rather than a new one.

##### Rejected alternative: static-serving a materialised tree

For completeness, there *is* a shape where a filesystem export would play a role. Schema v0.1.0 (§1.3) serves loose files at predictable paths, so one could materialise every published skill into a directory and point a static file server at it, with a generated `index.json` alongside. It is rejected because it is strictly worse here:

- It reintroduces disk state, and with it staleness — the tree must be regenerated on every skill, tool, or snippet mutation, or it serves content that no longer matches the store.
- It needs a writable volume and a cleanup story, which the current design does not.
- It inherits v0.1.0's all-or-nothing index failure mode (§4.7) and an *O(files)* request fan-out.
- It saves no work: enumerating a skill's file list already means running `_build_file_structure()`, so the expensive step happens either way.

Serving from an in-memory cache keyed on `modified_at` (§4.4) gives the same request-time cost with none of the staleness or disk footprint. The one legitimate reason to prefer the static shape would be offloading delivery to a CDN or an existing web server — worth revisiting only if that becomes a deployment requirement.

#### 4.0.2 No change to the name / UUID scheme

Every object keeps exactly the identity it has today. Specifically, this design:

| | |
| --- | --- |
| Adds a field to any manifest | **No** — publish gating reuses existing mechanisms: `state`, ordinary tags, and the `namespace:` tag convention (§4.3) |
| Requires a migration | **No** — nothing on disk changes shape |
| Writes to the store | **No** — every endpoint is a `GET`; the artifact cache is process-local memory |
| Changes `name` or `uuid` on any object | **No** |
| Changes name→UUID resolution, `parent` chains, or HEAD selection | **No** — §5.5 *reads* the existing `name_cache`; it does not alter how HEADs are determined |

The scheme this design reads, and preserves, is: **`uuid` is unique; `name` is not.** All objects sharing a name are linked on the `parent` chain, and exactly one of them is HEAD. `name_cache` maps each name to that single HEAD ([object_handler.py:298-301](../../src/skillberry_store/modules/object_handler.py#L298-L301)), which is what `GET /skills/{name}` already resolves through. The index publishes HEADs, so it inherits that one-per-name property rather than asserting one of its own.

**The slug is a projection, not a second identity.** It is derived from `name` at read time, lives only in the in-memory cache, is never persisted, and is not resolvable through any SBS API — `GET /skills/{uuid_or_name}` continues to accept a UUID or a name, never a slug. Its only jobs are to be the index entry name, the artifact URL segment, and the directory name on the *client's* disk.

That is precisely why §5.6's stability rule is load-bearing: because the slug carries no authority, nothing in the store protects it. Stability has to come from the derivation being deterministic and monotonic, not from storage.

Two adjacent changes that are **not** identity changes, but do alter bytes, so they should not come as a surprise in review:

- **§6.0 makes `generate_skill_md` emit YAML-safe frontmatter.** This changes the bytes of `GET /skills/{name}/export-anthropic` and of the vNFS tree wherever a description previously needed quoting. It fixes a live defect (§5.4); it does not touch stored data.
- **§6.0 optionally emits the slug as the frontmatter `name`.** This applies to the well-known path only — it is a new optional argument, and existing callers keep today's raw-name output. The *stored* `name` is untouched either way; what changes is the `name:` line inside an exported `SKILL.md`, so that the file an agent loads carries a name matching its own directory (§5.8 #1).

If either of those is unwelcome, the npx feature still works without them — but §5.4 will drop any skill whose description contains a newline or a `: `, so the frontmatter fix is not really optional in practice.

### 4.1 Endpoint surface

A new module `src/skillberry_store/fast_api/wellknown_api.py`, registered from `SBS.__init__` alongside the other `register_*_api` calls ([server.py:271](../../src/skillberry_store/fast_api/server.py#L271)):

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/.well-known/agent-skills/index.json` | discovery index over all published skills |
| `GET` | `/.well-known/agent-skills/{slug}.zip` | that skill's archive (v0.2.0 `type: "archive"`) |
| `GET` | `/ns/{namespace}/.well-known/agent-skills/index.json` | namespace-scoped index (the "pack" analogue) |
| `GET` | `/ns/{namespace}/.well-known/agent-skills/{slug}.zip` | scoped artifact |

Also register `/.well-known/skills/index.json` as an alias — it costs one line and the CLI probes it.

Then:

```bash
# Superseded by §4.3.8 / §6.4 — one skill per URL, no aggregate index:
npx skills add http://localhost:8000/pub/pdf-forms                 # ACL disabled: slug
npx skills add http://localhost:8000/pub/<token> -a claude-code    # standalone: scoped token
npx skills add http://localhost:8000/pub/pdf-forms --list          # preview, install nothing
```

### 4.2 Name slugging — required, not cosmetic

SBS names are free-form (`name: None|str`, no validator — [manifest_schema.py](../../src/skillberry_store/schemas/manifest_schema.py)). The CLI demands `^[a-z0-9-]{1,64}$`, no `--`, no edge hyphens. So `"PDF Forms"`, `"pdf_forms"`, and `"pdf--forms"` are all currently unpublishable.

Reuse the CLI's own normalisation, then dedupe:

```python
def to_slug(name: str) -> str:
    s = re.sub(r"[\s_]+", "-", name.lower())
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64].strip("-")
```

Collisions are real (`"PDF Forms"` and `"pdf-forms"` collapse). Resolve deterministically — sort candidates by `(name, uuid)` and suffix later ones with a short uuid prefix (`pdf-forms-a3f1`) — so a slug is stable across restarts. A skill whose name slugs to `""` is skipped and logged at WARNING.

The slug is the install name and the directory name the user ends up with, so it must also round-trip: the artifact handler resolves `{slug}` back to a UUID via the same mapping, not via `_resolve_uuid`.

### 4.3 Which skills get published

Publishing every skill in the store by default is wrong for a store that holds drafts. Gate on a config knob:

| Env var | Default | Meaning |
| --- | --- | --- |
| ~~`SBS_WELLKNOWN_ENABLED`~~ | — | **superseded by §5.12** — the switch is `npx_publish` in the access-control config, off by default |
| `SBS_WELLKNOWN_STATES` | ~~`approved`~~ | **superseded by §4.3.1** — a state filter is a second visibility rule SBS does not otherwise have, and it breaks the guarantee that npx installs what the user can see |
| `SBS_WELLKNOWN_REQUIRE_TAG` | ~~*(unset)*~~ | **superseded by §4.3.1** — use a namespace (§6.7), which is visible to the user, instead of an invisible server-side filter |
| `SBS_WELLKNOWN_NAMESPACES` | *(unset)* | if set, restrict `/ns/{namespace}` to this allowlist |

Default `approved` matches the lifecycle's intent and means an operator who imports a repo of drafts does not accidentally publish them to the internet. Note the schema default for `state` is `APPROVED`, so this is permissive in practice — a deliberate choice to keep the feature discoverable, worth calling out in the docs.

A skill with `metadata.internal: true` in its emitted frontmatter is additionally hidden client-side (§1.4) — worth surfacing as a per-skill opt-out.

### 4.3.1 The visibility guarantee: npx installs exactly what the user can see

> **Superseded in part by §4.3.8.** With per-skill installs there is no aggregate index, so the set-equality machinery below is no longer built. The *finding* that visibility is binary still stands and still matters — it is why a per-skill token grants nothing the holder's tenant could not already read.

The requirement — *a user must be able to install, via npx, every skill visible to her, and it must be simple* — turns out to be easy to guarantee, but only after noticing something about the authorization model. The work is mostly **deleting a rule §4.3 invented**, not adding one.

#### Visibility in SBS is binary, not per-skill

| Evidence | Consequence |
| --- | --- |
| `Rule` carries only `resources` and `verbs` — no instance, name, tag or namespace field ([config.py:97-100](../../src/skillberry_store/access_control/config.py#L97-L100)) | a role cannot grant access to *some* skills |
| `authorize(subject, resource, verb, cfg)` decides on `(resource, verb)` alone ([pdp.py:34-60](../../src/skillberry_store/access_control/pdp.py#L34-L60)) | the PDP has no skill identity to discriminate on |
| No service or handler reads `current_subject()` — only the plugin layer does (§6.4) | listing is not filtered per tenant |

So for any tenant: **`skills:list` ⇒ she sees every skill in the store; no `skills:list` ⇒ she sees none.** There is no middle. In `access_control_config.yaml.standalone`, `skillberry` (`base-user`) and `skillberry-admin` (`admin`) therefore see the **same** set of skills — they differ in what they may *write*, not in what they may *see*.

#### Which means the only thing that can break the guarantee is our own publish gate

`SBS_WELLKNOWN_STATES` and `SBS_WELLKNOWN_REQUIRE_TAG` (§4.3) are a **second, parallel visibility rule that SBS does not otherwise have**. With them, a user sees 50 skills in the UI, runs the install command, is offered 30, and has nothing to tell her why. That is the remaining design issue, and it is self-inflicted.

**Revised design — one source of truth:**

```python
def visible_skills(service) -> list[dict]:
    """The skills any caller with `skills:list` can see. ONE definition.

    Both the UI listing and the well-known index call this. Equality between
    "what she sees" and "what npx installs" is then structural, not a property
    a test has to keep re-establishing. Do not add filtering here that the
    RBAC model does not itself express — see docs/design/npx.md §4.3.1.
    """
    return head_skills(service)          # HEAD per name (§5.5); nothing else
```

The three knobs in §4.3 collapse to **one binary switch** — does this store publish for npx, or not:

| Env var | Default | Meaning |
| --- | --- | --- |
| `npx_publish` (ACL config, §5.12) | `false` | publish, or do not publish. No per-skill filtering |

A store that genuinely wants to publish a curated subset uses a **namespace** (§6.7) — which is a real, user-visible SBS concept she can see and filter by in the UI — rather than an invisible server-side state filter. The install URL then names the namespace, so the scope is legible in the command itself.

> Dropping the state gate does mean a `state: new` draft is publishable. That is the correct trade: the draft is *already* visible to every user with `skills:list`, so excluding it from npx would not be protecting anything — it would only be lying about what the store contains. §9 Q2 is hereby resolved in favour of no state filter.

#### Reconciling the guarantee with "don't publish content unauthenticated"

The CLI sends no credentials (§1.4), so the index must be reachable without them. Because visibility is binary, the resolution is simple and needs only **one** mechanism, selected by the ACL mode the operator already set:

| ACL mode | Install URL | Why the guarantee holds |
| --- | --- | --- |
| `disabled` | `/pub/{slug}/.well-known/agent-skills/index.json` — the slug is the whole reference, no secret (§6.4) | everyone already sees everything, so there is nothing for a token to protect |
| `standalone` | `/pub/{publish_token}/.well-known/agent-skills/index.json` (§4.6) | the token resolves to a subject, the handler runs `authorize(subject, "skills", "list")`, and on allow returns `visible_skills()` — the same list, via the same function, as that subject's `GET /skills/` |

On deny, return **404, not 403** — the CLI only needs "nothing here", and a 403 would confirm that a guessed token is well-formed.

Because the visible set is identical for every tenant holding `skills:list`, **a single store-wide publish token is sufficient**. Per-user tokens would buy revocation granularity and install attribution, not a different skill set; they are worth adding only if attribution is wanted, and should not be the default. This is the one place where the binary authorization model makes the design markedly simpler than it would otherwise be — worth not over-building.

#### Keeping it simple for the user: generate the command, don't document flags

Two verified CLI behaviours make the naive command unpleasant, and both are invisible until you hit them:

| Behaviour | Effect on a 50-skill store | Evidence |
| --- | --- | --- |
| Skills are pre-selected in the multiselect **only for `skills.sh` pack URLs** — `initialSelected: isSkillsShPackUrl(url) ? skills : void 0` | an SBS URL yields a 50-item checklist with **nothing checked**; the user ticks 50 boxes | §1.7.1 |
| `-y` with **no detected agent** installs to **every** supported agent | ~75 skill directories (`.aider-desk/`, `.autohand/`, `.bob/`, …) created in the user's tree | `cli.mjs:4499-4502` (1.6.0) |

`-y` *does* select all skills without prompting, so it is the right flag for "install everything" — but it must be paired with an explicit agent so the no-agent-detected branch is never reached:

```bash
npx skills add <url> -y -a claude-code
```

So the answer to "keep it simple" is **not** a documented flag incantation — it is a **UI affordance that emits the whole command**: the skills page shows an agent picker and a copy button producing

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/<token> -y -a claude-code
```

One copy, one paste, no login, no flags to learn, no 50-item checklist, and no agent sprawl. `DISABLE_TELEMETRY=1` is not decoration: the publish token travels in `installUrl` to `add-skill.vercel.sh` otherwise (§1.8), and the copied command is the only place we can reliably set it.

**Residual risk, stated plainly:** a URL-embedded credential that a third-party endpoint receives by default is a real downside, mitigated but not eliminated by the token being read-only, revocable, and scoped to skill reads. An operator who cannot accept it has two honest options: keep npx publishing off, or run the store behind a network boundary and use slug-addressed URLs as in `mode: disabled` (which, given binary visibility, differ from token URLs only by authentication). Both should be in the operator notes.

#### How the guarantee is tested

Set equality, not a subset check — this replaces the weaker assertion in §6.5 #11:

```python
def test_npx_set_equals_user_visible_set(skillberry_demo_client):
    """Every skill `skillberry` can see is installable via npx, and no more."""
    token, _ = client.app.state.acl_sessions.mint("skillberry", [], 60)
    seen = {s["name"] for s in client.get("/skills/?fields=narrow",
                                          headers=auth(token)).json()}
    index = client.get(f"/pub/{publish_token}/.well-known/agent-skills/index.json").json()
    published = {slug_to_name[e["name"]] for e in index["skills"]}
    assert published == seen          # equality, not `<=`
```

Plus: the same assertion for `skillberry-admin` yields the same set (binary visibility); a tenant bound to a role **without** `skills:list` gets `404` on the publish URL; and a skill created after the index was fetched appears on the next fetch.

### 4.3.2 The install, from the user's side

What follows is the whole user-facing flow. It is deliberately short, because that is the design goal — and every line of CLI behaviour below was read out of `cli.mjs`, not assumed.

#### Step 1 — she copies one line from the store UI

On the Skills page she picks her agent from a dropdown (defaulting to whatever she used last) and clicks **Copy install command**. The clipboard gets one line:

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/<token> -y -g -a claude-code
```

`-g` is not optional when the URL carries a token — §4.3.6 explains why (a project-scope install commits the token to git). The UI builds every part of it, so she never has to know that `-y` means "all skills", that `-a` pins the agent, or where the token came from. She authenticated once in the browser; the UI minted that token on her behalf — §4.3.3 has the exact mechanism, including why her **session** token must never appear here. If the store runs with `mode: disabled`, the URL is simply the store's root and there is no token:

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/pdf-forms -y -a claude-code
```

#### Step 2 — she pastes it into her project

No prior setup: no `npm install`, no `skills` CLI on her PATH, no `git` credentials, no GitHub PAT, no `skills login`. `npx` fetches the CLI on first use and caches it.

What she sees, in order:

```
◐ Discovering skills from well-known endpoint...
✔ Found 12 skills
ℹ Skill: pdf-forms
│ Fill and flatten PDF forms.
│   Files: SKILL.md, scripts/fill.py
ℹ Skill: release-notes
│ Generate release notes from git history.
…
ℹ Installing all 12 skills

┌ Installation Summary ──────────────
│ .agents/skills/pdf-forms
│   claude-code
│   files: 2
│ …
└────────────────────────────────────

◐ Installing skills…
✔ Installation complete
```

Because `-y` is set, three prompts that would otherwise appear are skipped, each defaulting the way she wants:

| Prompt | Skipped because | Default she gets |
| --- | --- | --- |
| skill multiselect | `options.yes` → `selectedSkills = skills` | **all** skills (this is why `-y` matters — §4.3.1 shows an SBS URL pre-selects *nothing*, so without it she would tick 12 boxes) |
| installation scope | `options.global === undefined && !options.yes` | **project** — `installGlobally` stays `false` |
| symlink vs copy | `!options.copy && !options.yes && uniqueDirs.size > 1` | with one agent, `uniqueDirs.size <= 1` → **copy** |
| "Proceed with installation?" | `!options.yes` | proceeds |

And because `-a claude-code` is explicit, agent auto-detection never runs — which is the other half of why the UI generates the flag rather than documenting it (§4.3.1: `-y` with no detected agent would install to *every* supported agent).

#### Step 3 — what is on her disk

With a single agent the CLI is in **copy** mode, so it writes real files straight into the agent's directory — no symlinks, no canonical second copy:

```
her-project/
├── .claude/skills/
│   ├── pdf-forms/
│   │   ├── SKILL.md            ← frontmatter: name = pdf-forms, description
│   │   └── scripts/fill.py
│   └── release-notes/SKILL.md
└── skills-lock.json
```

`skills-lock.json` records provenance per skill — `sourceType: "well-known"`, `sourceBaseUrl`, `sourceUrl`, `wellKnownDigest` — which is what makes Step 4 work.

> Cosmetic wrinkle worth knowing before someone reports it: the Installation Summary prints the *canonical* path (`.agents/skills/<slug>`) as each skill's heading even in copy mode, where nothing is written there. That is CLI behaviour, not ours, and the files really are at `.claude/skills/<slug>/`.

Her agent picks the skills up with no further configuration — `.claude/skills/` is where Claude Code already looks.

#### Step 4 — later, one word to catch up

```bash
npx skills update
```

Re-fetches the index (with an `X-Skills-Update-Check: 1` header, so the store can tell polls from installs) and re-downloads **only** skills whose digest changed. A store-side edit reaches her on the next `update`; a store that has not changed produces no downloads.

#### What she never has to do

| | |
| --- | --- |
| Install anything first | `npx` fetches the CLI |
| Log in from the CLI | she already logged into the UI; the CLI cannot send credentials anyway (§1.4). See §4.3.3 |
| Store a token in a config file | it lives in the command the UI gave her |
| Learn any flags | the UI emits them |
| Pick skills one by one | `-y` takes all of them |
| Know what a namespace, a slug, or a digest is | all internal |
| Clone a repo, or hold a GitHub PAT | the store serves the bytes directly |

#### The one thing she must be told

The command contains a credential. If she pastes it into a shared channel, anyone who receives it can read the store's skills until the token is revoked. That is the cost of a CLI that cannot authenticate, and it belongs in the UI next to the copy button — one sentence, not a buried note — along with the fact that `DISABLE_TELEMETRY=1` is what keeps the token out of a third party's logs (§1.8).

### 4.3.3 Authentication: who authenticates, when, and with what

§4.3.2 said "the token in the URL stands in for her login", which glosses over the part that matters. Spelling it out, because conflating two different tokens is the easiest way to get this wrong — and one of the two must never appear in the command.

#### She authenticates once. The CLI never authenticates.

There is exactly one authentication event, and it already exists: she signs in — in the UI (`POST /auth/login`) or with `sbs login` — and holds a **session token**. The npx CLI authenticates at no point; it cannot, since it sends no credentials to a well-known host (§1.4).

The install URL carries a **second, different** credential — a *publish token* — and the session token is what entitles her to learn it. The session token itself never enters the command.

#### Three credentials, and only one of them belongs in a URL

| Credential | Where it lives | Lifetime | What it grants | In the npx URL? |
| --- | --- | --- | --- | --- |
| Password | her password manager | — | login | no |
| **Session token** | client + in-memory server store | 12 h, **lost on restart** | the full RBAC of her roles | **never** |
| **Publish token** | derived on demand; only copy is in the command | stable until the secret is rotated | read-only skill discovery, nothing else | **yes — that is its only job** |

A session token cannot be the thing in the URL, for three independent reasons — worth stating because "just put the session token in the path" is the obvious-looking shortcut:

1. **It does not survive a restart.** `SessionStore` is in-memory by design — *"session tokens are opaque, module-level, and lost on process restart"* ([sessions.py:1-5](../../src/skillberry_store/access_control/sessions.py#L1-L5)). Her command is recorded in `skills-lock.json` and replayed by every `npx skills update`, so a restart would silently break updates.
2. **It expires in 12 h** (`session_ttl_seconds: 43200` in the standalone config), so the command would go stale by the next day.
3. **It carries her full privileges.** In a URL it would reach her shell history, her lockfile, and — via `installUrl` — Vercel's telemetry endpoint (§1.8). For `skillberry-admin` that would be an admin credential leaving the building. A publish token is read-only precisely so that this exposure is survivable.

#### The publish token is *derived*, not minted and stored

An earlier draft of this section proposed `POST /wellknown/publish-token` plus a persistent token table. That was over-built. The session token already proves who she is, so the publish token can be a pure **function of her tenant** rather than a row somebody has to create, store, list and revoke:

```python
def publish_token(tenant_id: str, secret: bytes) -> str:
    """Stable, unguessable, read-only capability for one tenant.

    Derived rather than stored: a restart must not invalidate it (the URL lives
    in the user's skills-lock.json and is replayed by `npx skills update`), and
    nothing about it needs a lifecycle of its own. Verification recomputes.
    See docs/design/npx.md §4.3.3.
    """
    return hmac.new(secret, tenant_id.encode(), hashlib.sha256).hexdigest()[:43]
```

To resolve one, the handler recomputes the HMAC for each configured tenant and compares in constant time. That is O(users-in-config) per request — single or double digits, and trivially memoised. Deliberately **not** `"{tenant}.{mac}"`: putting the tenant id in the URL would leak her username into her shell history and into `installUrl` telemetry for no benefit.

What this removes, compared with the minted-token design:

| | Minted + stored | **Derived (recommended)** |
| --- | --- | --- |
| New REST endpoints | 3 (mint / list / revoke) | **0** |
| Persistent state | a token table | **one secret** |
| Survives restart | only because it is persisted | yes, as long as the secret is |
| Code to write | store, atomic writes, lifecycle, 3 handlers, their RBAC markers and tests | a helper function and one response field |

The only durable state is the secret: `SBS_WELLKNOWN_SECRET`, or generated once and persisted following the `~/.skillberry/plugins.json` precedent (atomic tmp-file + `os.replace`, [plugins/config.py:43-63](../../src/skillberry_store/plugins/config.py#L43-L63)), with a boot log line saying which. One key in one file, not a table.

#### She gets the URL from a response the UI already fetches

No dedicated endpoint: add one optional field to `GET /auth/whoami`, which already returns `tenant_id`, `groups` and `roles` ([auth_api.py:37-40](../../src/skillberry_store/fast_api/auth_api.py#L37-L40)) and already self-resolves the bearer.

```python
class WhoAmIResponse(BaseModel):
    tenant_id: Optional[str] = None
    groups: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    npx_install_url: Optional[str] = None   # present iff the caller holds skills:list
```

It is the right home on four counts:

- The UI **already calls it** — its docstring says it populates the "Signed in as …" indicator — so the Skills page has the URL without a second round trip.
- It already **recomputes roles at request time** rather than baking them into the session, which is exactly the semantics the install URL needs: if her binding changed a minute ago, the field reflects that.
- It carries `x-cli-name: whoami`, so **`sbs whoami` surfaces the URL for free** — which covers the CLI-login path as well as the UI, with no extra command.
- It is already reachable with a bearer and self-resolves it, so the field is simply omitted when there is no valid session, or when the caller lacks `skills:list`.

In `mode: disabled` whoami returns `503 auth_disabled` and there is no token at all — the install URL is just the store root, which the UI composes locally with no server involvement.

#### Revocation, without a revocation API

This is the part that made the token table look necessary, and it turns out most of it is already handled:

| Case | How it is revoked | Cost |
| --- | --- | --- |
| She leaves; her user is deleted, or her role loses `skills:list` | **automatic** — the handler re-runs `authorize(subject_for(tenant), "skills", "list")` on *every* request, so the URL stops working immediately | none |
| Her URL leaked and she stays | rotate `SBS_WELLKNOWN_SECRET` | everyone re-copies their command |
| Revoke exactly one user, keep everyone else's URL working | not supported by the derived scheme | see below |

Per-request re-authorization is what makes the common case free — and it is required anyway (§4.3.3 earlier: a token must not outlive the permission that justified it). The remaining gap is single-user revocation while others keep working. Two honest positions:

- **Accept it.** A leaked URL grants only what any authenticated user already sees (§4.3.1), and rotating the secret is a defensible response to a leak. This keeps the implementation at one function and one field.
- **If single-user revocation is required**, the smallest addition is a per-tenant epoch folded into the HMAC (`HMAC(secret, f"{tenant}:{epoch}")`) with the epochs in the same small state file; revoking is incrementing an integer. That is still far less machinery than a token table, and it can be added later without changing the URL shape or any caller.

Start without the epoch. Add it only if someone asks for per-user revocation.

#### The one thing that is still authenticated

| Route | ACL treatment |
| --- | --- |
| `GET /auth/whoami` | already allowlisted, self-resolves the bearer; emits `npx_install_url` **only** for a valid session holding `skills:list` |
| `GET /pub/{token}/.well-known/agent-skills/*` | allowlisted (§6.4); the token *is* the authorization, re-checked per request |
| everything else | unchanged — session bearer only |

So learning the URL requires a login; using it does not. Deny on the `/pub/` path — unknown token, rotated secret, or a tenant that no longer holds `skills:list` — returns **404, not 403** (§4.3.1), so a prober cannot distinguish a malformed token from a valid one without permission.

### 4.3.4 What the publish token is not — and the alternatives if that is unacceptable

Two properties that the words "publish token" tend to oversell.

**It does not widen the API surface.** The token is accepted on exactly one route shape and nowhere else:

| | |
| --- | --- |
| Accepted | `GET /pub/{token}/.well-known/agent-skills/*` — as a **path segment**, resolved by that handler alone |
| Not accepted | as an `Authorization: Bearer` value; the PEP resolves bearers only via `SessionStore.resolve` ([deps.py:91-102](../../src/skillberry_store/access_control/deps.py#L91-L102)) and reads no path segment or query parameter for credentials |
| Unaffected | `/skills/*`, `/tools/*`, `/snippets/*`, `/admin/*`, `/auth/*` — still session-token only |

**This is a requirement to enforce, not a property that falls out for free.** In particular: never derive the publish token from, or resolve it through, `SessionStore`. Doing so would make it a general-purpose credential with the full RBAC of its tenant — the exact leak §4.3.3 exists to prevent. The HMAC helper and its verifier must be the only consumers of the secret, and the well-known handler the only consumer of the verifier.

**It is a bearer capability, not a user identity — so anyone holding the URL can use it.** npx sends no headers, no cookie and no client identity, so the URL is the only channel available: per-user *enforcement is impossible by construction*. Deriving the token per tenant gives per-tenant **issuance** and automatic expiry when that tenant loses `skills:list` (§4.3.3), but it cannot tell who is calling.

Sizing the exposure precisely: because visibility is binary (§4.3.1), the token grants exactly what *any* authenticated user with `skills:list` already sees — **no additional data**. What it changes is the precondition, from "hold an account" to "hold a URL". That is the whole security question, and it is a judgement about the deployment rather than something this design can settle.

#### If a URL-borne capability is not acceptable

Alternatives, presented as peers rather than fallbacks — for some deployments one of them is simply the right answer:

| Option | What it gives | What it costs |
| --- | --- | --- |
| **A. Public root index, no token** | one on/off switch; nothing to derive, leak or rotate | readable by anyone who can reach the port. Given binary visibility this differs from the token only in discoverability and rotatability — on a trusted network, barely at all |
| **B. Do not offer npx on ACL-on stores** | zero new exposure; the feature exists only where `mode: disabled` | users of secured stores keep the UI, the SBS CLI, or vNFS. **This should be a first-class supported configuration, not a failure mode** |
| **C. Terminate auth at a gateway** | the operator's existing SSO/proxy fronts the store; SBS publishes the plain root index behind it | outside SBS to implement, but it is how many organisations would actually do this and deserves a paragraph in the operator notes |
| **D. Get the CLI to send credentials** | real per-user authentication | requires upstreaming a change to `vercel-labs/skills`, whose well-known provider deliberately sends no headers (§1.4). A dependency on someone else's roadmap, not a plan |

For `mode: disabled`, A *is* the design and costs nothing. For `mode: standalone` the derived token (§4.3.3) is the recommendation, with A and B written out so the trade-off is an operator decision rather than a discovery made in production. See §9 Q6.

### 4.3.5 Surfaces: API, CLI and UI are one scheme, not three

> **Amended by §4.3.8.** The surface analysis stands. Two changes: `-s <slug>` is no longer used (the URL identifies the skill), and `_npx_install` on `list` is now acceptable — a per-skill token is self-limiting — though it stays opt-in.

#### There is no separate CLI to keep in sync

The `sbs` CLI is **auto-generated from the OpenAPI schema** — a wrapper around [restish](https://rest.sh/), which builds commands from the spec and prints whatever the API returns (`docs/cli.md`). The `x-cli-name` extras on each route are what name the commands.

The consequence matters for this design: the API is the *only* implementation. A field added to a response model appears in `sbs` output with **zero CLI code**, and cannot drift from the API because it is the same schema.

| Surface | How it gets the install command | Code needed |
| --- | --- | --- |
| **API** | `npx_install_url` on `GET /auth/whoami` (§4.3.3); optional per-skill field on `GET /skills/{uuid_or_name}` (below) | the field + one helper |
| **CLI** | `sbs whoami`, `sbs get-skill <name>` — restish renders the new field automatically | **none** |
| **UI** | agent picker + copy button, reading the same fields | the button (§4.3.2) |

One caveat, deliberate: the well-known routes themselves are registered `include_in_schema=False` (§6.4), so they are **not** `sbs` commands and never will be. They exist for npx. The CLI surface here is the *delivery of the URL*, not the discovery endpoints.

#### Per-skill install command on `get` — yes, with three corrections

`sbs get-skill pdf-forms` returning that skill's install command is the right idiom — it is what skills.sh does on each skill page, and it costs no CLI work. Three things have to be right:

**1. The command must use `-s <slug>`. There is no per-skill URL form.** This is the correction that matters, because the natural guesses both fail silently:

| Guess | What actually happens |
| --- | --- |
| `…/pub/<token>#@pdf-forms` | the `@skill` fragment is parsed **only** when `looksLikeGitSource(input)` is true, which for an `http(s)` URL means a GitHub / GitLab / Azure host. For an SBS host the fragment is **discarded** and all skills install |
| `…/pub/<token>/.well-known/agent-skills/pdf-forms` | the CLI treats the whole path as `basePath` and probes `…/pdf-forms/.well-known/agent-skills/index.json` → 404 → nothing found |

So the only correct per-skill form is the flag:

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/<skill-token> -y -a claude-code
```

*(§4.3.8 removed the `-s` form entirely — a scoped token yields a single-entry index the CLI auto-selects. The table above is kept because it records why a per-skill **URL path** cannot work and a flag was once required.)*

`-s` filters the index client-side (`selectedSkills = skills.filter(...)` on `installName`), which is why the index still has to publish the whole visible set for a single-skill install to work.

**2. `list` must not carry it.** A 50-skill listing would repeat the same credential 50 times in one response body, and `narrow` is the payload the UI fetches on every page load. The install command belongs on `get`, never on `list`.

**3. Make it opt-in through the existing `fields` mechanism, not a default.** SBS already has the right idiom: flag fields that "do not name persisted data — instead they activate a bundling mechanism" ([field_selection.py:42-47](../../src/skillberry_store/services/field_selection.py#L42-L47)), resolved per object type through a tag table, where a CSV token is accepted only if it is a declared field name ([field_selection.py:218-247](../../src/skillberry_store/services/field_selection.py#L218-L247)).

Declare `_npx_install` for `skill` with an **empty preset tag set**, so it is reachable only when named explicitly:

```
GET /skills/pdf-forms?fields=name,description,_npx_install
```

It is then absent from `minimal`, `narrow`, `wide` **and** `full` — which matters, because `fields="full"` is used internally (for example when gathering export inputs) and none of those paths should start returning a credential. Note the ordering invariant `minimal ⊆ narrow ⊆ wide ⊆ full` forbids tagging it for a small preset only; an empty set sidesteps that cleanly rather than fighting it.

> Why gate it at all, when the caller could get the same token from `whoami`? Not to prevent access — it is the same secret to the same authenticated caller, so this is not a security boundary. It is to stop a credential from appearing in payloads nobody asked for, where it ends up in response logs, screenshots and pasted support tickets. Opt-in keeps the blast radius equal to "places someone deliberately asked for it".

#### One helper, three callers

To keep the surfaces genuinely identical rather than merely similar, the command string is built in exactly one place:

```python
def npx_install_command(base_url: str, ref: str,
                        agent: str = "claude-code") -> str:
    """The single definition of the install command, for every surface.

    `get-skill` and `list` both emit it per skill; the UI substitutes its own
    agent from the picker. Never assemble this string anywhere else — see
    docs/design/npx.md §4.3.5 and §4.3.8.
    """
    # `ref` is a scoped token (standalone) or the slug itself (disabled) — §6.4.
    # Always one skill per URL, so no -s and no -g: the single-entry index is
    # auto-selected, and a per-skill token is safe in a committed lockfile.
    return (f"DISABLE_TELEMETRY=1 npx skills add "
            f"{base_url}/pub/{ref} -y -a {agent}")
```

**Tests:** `sbs get-skill X` and `GET /skills/X?fields=_npx_install` yield byte-identical commands; `fields=narrow` and `fields=full` on both `get` and `list` contain no token; `fields=…,_npx_install` does; the emitted URL, fetched, returns an index whose single entry is that skill; and the field is omitted for a non-HEAD version (§5.10 #7).

### 4.3.6 Where the token ends up: the lockfile is meant to be committed

> **Resolved by §4.3.7 + §4.3.8.** With no store-wide token, the only thing a project lockfile can record is a per-skill capability for a skill whose content is committed beside it. The `-g` rule below is withdrawn. Kept as the record of why.

The most consequential thing found by tracing the token rather than reasoning about it.

A **project-scope** well-known install writes the install URL — token and all — into `./skills-lock.json` at the project root:

```js
await addSkillToLocalLock(skill.installName, {
  source: sourceIdentifier,
  sourceUrl: url,            // ← the full URL, including /pub/<token>
  sourceType: "well-known",
  computedHash, wellKnownDigest,
}, cwd);
```

And that file is one the ecosystem expects to be **committed**: the CLI's own scope table describes project installs as *"Committed with your project, shared with team"*. This repo already carries a `skills-lock.json` from earlier GitHub installs, at the root, tracked.

So the default path leaks the credential into git history, permanently, visible to everyone with repo read access — and §4.3.2's recommended command made it worse, because `-y` leaves `installGlobally` at `false`, i.e. **project scope is the default**.

#### The rule: an **unscoped** token in the URL means `-g`

> Narrowed by §4.3.7: a token scoped to a single skill is safe in a committed lockfile, because it grants only what the commit already contains. What follows applies to the store-wide (`*`) token.

Global installs use a different lockfile entirely — `~/.agents/.skill-lock.json`, or `$XDG_STATE_HOME/skills/.skill-lock.json` — which is outside any repository:

| Scope | Lockfile | Token committed? |
| --- | --- | --- |
| project (default) | `./skills-lock.json` | **yes, into git** |
| global (`-g`) | `~/.agents/.skill-lock.json` | no |

So the copy-command for a token-bearing URL becomes:

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/<store-token> -y -g -a claude-code
```

*(Historical: this was the store-wide form, removed by §4.3.8.)* `-g` is defensible on its own merits as well as for the leak: a store-wide skill library is naturally a per-user resource rather than a per-repo one, and global install puts it at `~/.claude/skills/` where every project sees it. What it gives up is the repo-committed, team-shared lockfile — which is exactly the thing we do not want here.

The alternatives, for completeness:

| Option | Verdict |
| --- | --- |
| `-g` (recommended when a token is present) | keeps the token out of every repo; loses repo-level sharing, which is the point |
| Project scope + `skills-lock.json` in `.gitignore` | works, but silently breaks the file's purpose, and depends on every user remembering |
| Project scope, accept the commit | only where repo read access and store read access are already the same set of people — and it is still in history forever after that stops being true |
| **Scoped tokens (§4.3.7)** | **the better fix** — a `skill:<slug>` token in a committed lockfile grants only what the commit already contains, so project scope stays safe and the lockfile keeps working as designed |
| **Option A (public index, no token)** | still has nothing to leak; remains the right answer where no capability should exist at all |

This reweights §9 Q6: the choice is not only about who can reach the port, but about whether an install URL can be safely committed. §4.3.7 resolves most of it by scoping the token to what was actually shared.

### 4.3.7 Scoped tokens: make the blast radius equal to what was shared

§4.3.6's problem is that a single store-wide capability is the *only* thing we hand out, so any place it lands — a committed lockfile, a pasted command — leaks the whole store. The fix is to scope the token to what the user is actually installing, so a leak grants exactly what the leak already contained.

#### One derivation, three scopes

```python
def publish_token(secret: bytes, tenant_id: str, scope: str) -> str:
    """Read capability for (tenant, scope). scope ∈ {"*", "ns:<name>", "skill:<slug>"}.

    Scoped so that a leaked URL grants exactly what its holder was given, not the
    whole store (§4.3.7). Derived from the durable secret and the *stable* identity
    — never from the session token, which is ephemeral (§4.3.3).
    """
    msg = f"{tenant_id}|{scope}".encode()
    return base64.urlsafe_b64encode(
        hmac.new(secret, msg, hashlib.sha256).digest()
    ).decode().rstrip("=")          # 43 URL-safe chars
```

| Scope | Index contains | Blast radius of a leaked URL |
| --- | --- | --- |
| `skill:<slug>` | that one skill | read that one skill |
| `ns:<name>` | that namespace (§6.7) | read that namespace |
| `*` | everything visible (§4.3.1) | read the whole store |

Resolution recomputes: for the candidate tenants (§5.10 #4), and for the scopes implied by the request path, compare in constant time. No storage, and this also settles §5.10 #5 — base64url of the full digest, not a truncated hex string.

#### Why a per-skill token is safe in a committed lockfile

This is the part that makes the idea strong, and it is a better argument than "they could only re-install the same skill":

**The lockfile is committed next to the skill's own files.** A project install writes `./skills-lock.json` *and* `.claude/skills/<slug>/SKILL.md` plus its resources — in the same commit. So a `skill:<slug>` token in that lockfile grants read access to content that is **already in the repository beside it**. Anyone who can read the token can already read the skill. The blast radius does not merely shrink; it collapses to approximately nothing.

That flips §4.3.6's conclusion for the per-skill case: with a scoped token, project-scope installs are safe to commit, and the lockfile keeps doing the job it was designed for — team-shared, reproducible installs.

Being precise about what a leaked scoped token *does* grant, since "re-install the same skill" undersells it slightly: it is a **durable read capability** for that skill until the secret rotates, so it also returns future *edits* to that skill, not just the version that was committed. For a skill whose content is already in the repo that is a marginal difference; for one that later gains sensitive content it is not. Worth one line in the operator notes.

#### The revised rule

| URL scope | Safe in a committed lockfile? | Recommended command |
| --- | --- | --- |
| `skill:<slug>` | **yes** — grants only what the commit already contains | project scope (no `-g` needed) |
| `ns:<name>` | only if the whole namespace is installed in that repo | `-g` unless that holds |
| `*` | **no** | **`-g`** (§4.3.6) |

So §4.3.6's blanket "a token means `-g`" narrows to: **an unscoped (`*`) token means `-g`.** Scoped tokens do not need it.

#### A bonus: a single-entry index needs no flags at all

When the index contains exactly one skill the CLI selects it with no prompt and no `-s`, and that branch is checked *before* `options.yes`:

```js
} else if (skills.length === 1) {
    selectedSkills = skills;
    log.info(`Skill: ${cyan(firstSkill.installName)}`);
}
```

So a per-skill install URL yields the shortest command in the whole design — and, unlike §4.3.5's `-s <slug>` form, it does not require the index to publish the rest of the store for a single-skill install to work:

```bash
DISABLE_TELEMETRY=1 npx skills add https://store.example.com/pub/<skill-token> -a claude-code
```

`-y` is still worth keeping to skip the scope and confirm prompts, but the skill selection needs nothing.

#### What this does not change

- **The session token is still never an input.** Deriving from it would break on every login (a new random session token ⇒ a new URL) and on every restart (`SessionStore` is in-memory), which is the same failure that rules it out of the URL entirely (§4.3.3). Its role is to authorize *learning* the URL.
- **Per-user enforcement is still impossible** (§4.3.4): a scoped token is still a bearer capability, just a smaller one. Anyone holding it can use it.
- **Re-authorization still runs per request.** A `skill:<slug>` token stops working when its tenant loses `skills:list`, exactly as an unscoped one does.
- **`*` is still needed**, because "install everything visible to her" cannot be expressed as N per-skill URLs without destroying the simplicity it exists for (§4.3.1, §4.3.2).

### 4.3.8 Decision: per-skill is the default install path

**Decided: the default is one skill per install URL — that is what the UI emits and what a casual user ever sees.** A user who wants several skills scripts it (below).

> **Amended by §4.3.9:** the `ns:` and `*` scopes *are* supported, as opt-in capabilities. What is removed from the **default** path is the store-wide install, not the scope itself. The deletions below hold for the default path; §4.3.9 states what returns for the multi-entry scopes.

This is the largest simplification in the document, and it removes problems rather than trading them.

#### What this deletes outright

| Removed | Why it existed | Why it is gone |
| --- | --- | --- |
| The unscoped `*` token *from the default path* | to serve "install everything" | the default is per-skill; `*` remains available opt-in (§4.3.9) |
| §4.3.6's committed-token leak | a project lockfile recorded a whole-store capability | the only tokens left are per-skill, and a per-skill token in a committed lockfile grants only what the commit already contains (§4.3.7) |
| The `-g` rule *on the default path* | to keep the unscoped token out of git | **not needed for per-skill URLs** — project scope is safe again and the lockfile keeps its team-sharing role. Still applies to `*` (§4.3.9) |
| The 50-item checklist trap (§4.3.1) | an SBS URL pre-selects nothing in the multiselect | a single-entry index auto-selects, before `options.yes` is even consulted (§4.3.7) |
| `-s <slug>` handling (§4.3.5) | the only way to install one skill from an aggregate index | the URL identifies the skill; no flag, and no need to publish the rest of the store for one install to work |
| The set-equality guarantee machinery (§4.3.1) | to prove the aggregate index equalled her visible set | there is no aggregate index. The guarantee becomes local and per-request: she can install any skill she can see, because the same `authorize(subject, "skills", "list")` gates each one |
| The `SBS_WELLKNOWN_STATES` / `_REQUIRE_TAG` debate | a publish gate | already resolved against filtering (§4.3.1); now moot |
| **Namespace scoping** | ~~deferred~~ | **reinstated by §4.3.9** — namespaces are supported |

#### What this simplifies

**Index cost collapses.** §4.4's central problem was that the digest forces every archive to be built before the index can answer — *N* full exports per request, which does not fit a 10 s budget on a 50-skill store. A single-entry index builds **one** archive. The `modified_at`-keyed cache becomes a straightforward optimization rather than a correctness requirement, and the index/artifact consistency window (§5.3 #7) shrinks to one skill.

**Slug collisions stop mattering within an index.** They can still produce two same-named directories on a client that installs both, so §5.6's stability rule stays — but it is no longer load-bearing for index validity.

**Namespace scoping** is supported as an opt-in tier — see §4.3.9, which reinstates it along with `*`, and states the forward-compatibility constraint that matters for both.

#### Installing several skills: the scripted path

Because each token is scoped to its own skill, a per-skill token is self-limiting — which removes the objection in §4.3.5 to returning install commands from `list`. That makes the automation the decision relies on a one-liner:

```bash
# every skill she can see, with its own install command
sbs list-skills --fields name,_npx_install

# install all of them
sbs list-skills --fields _npx_install -o json \
  | jq -r '.[]._npx_install' \
  | while read -r cmd; do eval "$cmd"; done
```

`list` may now carry `_npx_install` because each value grants only its own skill — which the caller, holding `skills:list`, can already read. It stays **opt-in** via the `fields` mechanism (§4.3.5) so the UI's `narrow` listing payload carries no capabilities it did not ask for, but it is no longer a security problem, only hygiene.

The honest cost: *N* `npx` invocations instead of one, each doing its own discovery. The npm package is cached after the first, so this is seconds-per-skill, not minutes. For a power user scripting a fresh machine that is acceptable; it is explicitly **not** the path a casual user is pointed at — she installs the one skill she is looking at.

#### `npx skills update` still works, and gains a property

Each lockfile entry carries its own `sourceUrl`, so `processWellKnownUpdates` groups by base URL into *N* groups of one and checks each independently — verified, `for (const [baseUrl, items] of groups)`. More requests, same correctness.

It also picks up a small bonus: when a skill is deleted from the store its index goes empty, and `update` offers to remove the local copy (`promptDeletions`). A store-side deletion propagates on the user's next update instead of leaving an orphan.

#### What is unchanged and still required

Nothing in §5's blocker list is affected — the per-skill decision is about *what* is published, not *how*:

| Still required | Section |
| --- | --- |
| YAML-safe frontmatter | §5.4 |
| Deterministic archive bytes + digest | §5.1 |
| Root-level `SKILL.md` | §5.2 |
| HEAD-only version selection | §5.5 |
| `file:` path sanitisation | §5.7 |
| Per-request re-authorization | §4.3.3 |
| `SBS_PUBLIC_URL` (the URL is still absolute) | §5.10 #2 |
| `GET /pub/*` allowlist entry | §5.10 #1 |
| Telemetry opt-out documented (an exported `DO_NOT_TRACK=1`, *not* a command prefix — §9 Q7) | §1.8 |

### 4.3.9 Scope support: per-skill by default, namespace and global available

Amending §4.3.8, which deferred the `ns:` and `*` scopes. **All three scopes from §4.3.7 are supported.** What §4.3.8 settles is the *default*, not the menu:

| Scope | Index | Who gets it | Committed lockfile? |
| --- | --- | --- | --- |
| `skill:<slug>` | one entry | **the default** — the UI's per-skill copy button, `_npx_install` on `get`/`list` | **safe** (§4.3.7) |
| `ns:<name>` | that namespace | opt-in, for pack-style distribution (§6.7) | only if the whole namespace is installed in that repo; otherwise `-g` |
| `*` | everything visible | opt-in, for bulk scripting on a fresh machine | **no — use `-g`** (§4.3.6) |

The consequences §4.3.8 removed come back, but **only for the multi-entry scopes**, and they are now confined to an opt-in path rather than sitting on the default one:

- **Index cost.** A multi-entry index must build and hash every archive before it can answer (§4.4), so the `modified_at`-keyed cache is a correctness requirement for `ns:`/`*` — not the optional optimization it is for a single-entry index.
- **The `-g` guidance** applies to `*` (and conditionally to `ns:`), for the reason in §4.3.6.
- **Slug collisions** matter again within a multi-entry index, so §5.6's stability rule is load-bearing there.

None of this touches the default path, which is why per-skill remains what the UI emits and what a casual user ever sees.

#### Forward compatibility: do not bake in binary visibility

A likely future extension is **namespace scopes inside role bindings**, so that a tenant's visible surface becomes the union of the namespaces its roles allow. Today visibility is binary (§4.3.1) — `skills:list` means all skills — but that is a property of the current RBAC model, not of this feature.

The design constraint that follows costs nothing now and avoids a rewrite later:

> **Compute a multi-entry index as "the skills this subject is authorized to see", never as "all skills, because visibility is binary today."**

Concretely: the `*` and `ns:` handlers must resolve their token to a `Subject` and filter through the authorization model, even though that filter is currently a no-op once `skills:list` is granted. Written that way, per-namespace bindings narrow the published set automatically when they arrive. Written the other way — `return head_skills()` because the check is redundant — the extension silently over-publishes, and nothing in the test suite would notice, because today the two are indistinguishable.

Two cheap things make the constraint stick:

1. Route the index through a single `authorized_skills(subject, scope)` function, so there is one place where the filter would need to become real.
2. Add a test that asserts the filter is *called* — not just that its current output is everything — so a future binding model has a failing test to satisfy rather than a silent behaviour change.

`ns:` also becomes the natural expression of that future model: a tenant allowed two namespaces would hold two `ns:` URLs, or one `*` URL whose index is the union. No URL shape has to change.

### 4.4 Index construction and caching

```python
# No @requires: this path is allow-listed, so the PEP short-circuits before
# mapping a route to (resource, verb). House convention forbids the marker on
# allow-listed routes — see §4.5 and access_control/decorator.py.
@app.get("/.well-known/agent-skills/index.json", include_in_schema=False)
def wellknown_index() -> dict:
    return {
        "$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
        "skills": [
            {"name": e.slug,
             "description": truncate(e.description, 1024),
             "type": "archive",
             "url": f"{e.slug}.zip",        # relative: inherits /pub/{ref}/ (§5.14)
             "digest": e.digest}
            for e in published_entries()
        ],
    }
```

**The digest forces the archive to be built before the index can be answered.** Under §4.3.8 each index has one entry, so this is **one** export per request and the cache below is an optimization, not a requirement. The *N*-export analysis that follows applies only if an aggregate index is ever reintroduced. Naively that would be *N* full exports per index request — each pulling every tool (`fields="full"`) plus `get_module()` disk reads ([skills_service.py:732-790](../../src/skillberry_store/services/skills_service.py#L732)). On a 50-skill store that is unacceptable for a 10 s budget.

Mitigation — a process-local artifact cache keyed on identity + freshness:

```
cache[skill_uuid] -> (modified_at, bytes, digest)
```

`modified_at` already exists on every manifest, so invalidation is a string compare. Because a skill's bytes also depend on its tools and snippets, include those in the key: `(skill.modified_at, tuple(sorted(t.modified_at for t in tools)), tuple(sorted(s.modified_at for s in snippets)))`. All of these are `DictCache` reads — in-memory, no disk I/O.

Two further properties this buys:

- **Digest/artifact consistency.** The index and the subsequent artifact fetch are separate HTTP requests; an edit in between yields a digest mismatch and a *silently dropped skill* (§1.4). Serving both from the same cached bytes closes that window. Making the artifact URL content-addressed (`/artifacts/{digest}/{slug}.zip`) closes it completely and makes the response infinitely cacheable — recommended if the extra route is acceptable.
- **Update detection.** Because the digest is derived from content, `npx skills update` re-downloads exactly the skills that changed.

Emit the counter alongside the existing export metric so operators can see npx traffic, and treat the `X-Skills-Update-Check: 1` header as a separate label — it distinguishes update polls from real installs.

### 4.5 Access control — three things, all mandatory

The demo's `401` (§3.1) is the proof this cannot be skipped. ACL is a **global** FastAPI dependency installed before any route exists ([server.py:171-181](../../src/skillberry_store/fast_api/server.py#L171-L181)), so the new endpoints are guarded automatically.

1. **Allowlist the paths.** Add to `unauthenticated_paths` in `access_control_config.yaml`, the `.standalone` variant, and `_DEFAULT_UNAUTH_PATHS` ([config.py:237](../../src/skillberry_store/access_control/config.py#L237)):

   ```yaml
   # Public skill discovery for `npx skills add` (docs/design/npx.md).
   # Read-only, and publishes ONLY skills matching SBS_WELLKNOWN_STATES.
   - GET /.well-known/agent-skills*
   - GET /.well-known/skills*
   ```

   Matching is exact-or-trailing-`*` prefix ([config.py:227-231](../../src/skillberry_store/access_control/config.py#L227-L231)), so one glob covers the index and all artifacts. The namespace-scoped routes live under `/ns/...` and need their own entries — or, better, move them under `/.well-known/agent-skills/ns/{namespace}/` so the single glob covers them and the public surface stays confined to one prefix. **Recommended.** (The CLI's scoped probe uses the URL's `basePath`, so this needs the install URL `http://host/ns/data-eng` to keep working — see §9.)

2. **Do NOT apply `@requires` to these routes.** `decorator.py` states the convention explicitly: *"Do NOT apply `@requires` to routes that live in the unauth allow-list — those never reach the PDP, and the audit skips them"* ([decorator.py:30-32](../../src/skillberry_store/access_control/decorator.py#L30-L32)). `/health`, `/health/ready`, and `/admin/metrics` all follow it ([admin_api.py:142-152](../../src/skillberry_store/fast_api/admin_api.py#L142-L152), [admin_api.py:36-40](../../src/skillberry_store/fast_api/admin_api.py#L36-L40)). The well-known routes are allowlisted, so they carry no marker; `audit_rbac_coverage` skips them ([audit.py:200-260](../../src/skillberry_store/access_control/audit.py#L200)). The allowlist entry and the absence of a marker are a matched pair — remove the entry without adding a marker and the route 403s for everyone, which is the fail-safe direction (§6.4).

3. **Document the exposure explicitly.** This is the first endpoint that publishes *skill content* — not just metadata — without authentication. That is the point of the feature, but it must be a deliberate operator decision. Recommend `SBS_WELLKNOWN_ENABLED` default to `true` **only when ACL mode is `disabled`**, and require an explicit opt-in when ACL is on, so switching on access control does not silently leave a public content endpoint behind. The log line at boot should state how many skills are being published publicly.

### 4.6 Private stores — the capability-URL pattern

Because the CLI sends no credentials (§1.4), a store behind auth cannot be installed from. Copy skills.sh packs: put an unguessable token **in the path**.

```
GET /pub/{publish_token}/.well-known/agent-skills/index.json
```

`npx skills add https://sbs.internal/pub/7f3c…` then works unmodified: the CLI treats `/pub/7f3c…` as `basePath`, probes the scoped index, and — per §1.7 — refuses to fall back to the root index if the token is wrong. There is no root or aggregate index in any mode (§6.4), so an unauthenticated prober with a wrong ref gets a 404 and nothing else.

Per §4.3.1 this is **not** just a private-store nicety — under `mode: standalone` it is *the* mechanism that delivers the visibility guarantee, so it belongs in the core path rather than in a late phase. §4.3.3 has the mechanics: the token is **derived per tenant** from a durable secret (no minting, no token table, no new endpoints), the handler resolves it to a subject, re-runs `authorize(subject, "skills", "list")` on every request, and on allow returns `visible_skills()`. A token is scoped to a namespace only when the operator wants a narrower install URL, never as a hidden filter.

Treat a publish token as a **bearer credential in a URL**: never log the full path, and note that it will reach Vercel's telemetry endpoint via `installUrl` (§1.8) unless `DISABLE_TELEMETRY=1` is set. That interaction is the single strongest reason to document the telemetry opt-out prominently.

### 4.7 Phasing: v0.1.0 first, or straight to v0.2.0?

| | v0.1.0 (loose files) | v0.2.0 (archive + digest) |
| --- | --- | --- |
| Server work | list files per skill; serve each file | build archive; hash it; serve bytes |
| Determinism required | **no** — client hashes what it got | **yes** — §5.1 |
| Requests per install | 1 + Σ(files per skill) | 1 + 1 per skill |
| Bad-entry blast radius | **whole index dies** | that entry only |
| Index cost | must enumerate paths → still runs the exporter | must build + hash → same work |
| Future-proof | legacy | current |

The apparent shortcut is illusory: enumerating a skill's file list already means running `_build_file_structure()`, so v0.1.0 saves no work on the index path — it only avoids the determinism fix, while accepting the all-or-nothing failure mode and an *O(files)* request fan-out.

**Recommendation: implement v0.2.0 directly.** Ship §5.1's deterministic archive as the first commit; it is ~10 lines and independently correct.

### 4.8 Documentation and surfacing

- `docs/cli.md` — an "Install skills into your agent with npx" section.
- `README.md` — add the one-liner next to the live-demo link; it is the most persuasive quickstart the project has, since it needs nothing installed.
- Operator notes: what gets published, how to scope it, `DISABLE_TELEMETRY=1`, and the publish-token flow.
- A UI affordance — an agent picker plus a per-skill copy-to-clipboard button on each skill (§4.3.2) — is where most of the perceived value lands, and §4.3.1 shows the feature is barely usable without it.

---

## 5. Blockers and gaps found in the existing code

These are the two findings that make "just point the CLI at the existing export endpoint" fail. Both were verified, not inferred.

### 5.1 Blocker A: the export ZIP is not byte-deterministic → every digest is wrong

`export_skill_to_anthropic_format()` calls `zip_file.writestr(file_path, content)` with a **string** name ([exporter.py:329](../../src/skillberry_store/tools/anthropic/exporter.py#L329)). CPython then stamps each entry with `time.localtime()`, so identical content produces different bytes — and a different sha256 — on every call.

Verified empirically:

```
same bytes: False
sha a: e331f008aa2c6dc3…   sha b: 34e80a605ac7f5a8…
```

Under §1.4 that means `computeDigest(bytes) !== entry.digest` and the skill is **silently dropped**. Fix — pass an explicit `ZipInfo`, and sort:

```python
def build_deterministic_zip(files: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            zi = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, files[path])
    return buf.getvalue()
```

Verified: `deterministic same: True`. > **Superseded by §5.13:** the decision is to unify. `export_skill_to_anthropic_format` is rewritten to call this function, so there is a single zip path and the digest tests cover both callers. The prefix remains a caller choice (§5.13).

### 5.2 Blocker B: the export ZIP nests everything under `<skill-name>/` → archive rejected

`_build_file_structure()` returns keys prefixed with the skill name ([exporter.py:302](../../src/skillberry_store/tools/anthropic/exporter.py#L302)):

```python
result[f"{skill_name}/{file_path}"] = normalized.encode("utf-8")
```

So the archive contains `pdf-forms/SKILL.md`, never `SKILL.md`. The CLI looks up the root entry exactly — `files.get("SKILL.md")` for zip, and `if (!files.has("SKILL.md")) throw new Error("Archive missing root SKILL.md")` for tar.gz — and **does not strip a single leading directory**. The existing endpoint's output is therefore rejected outright.

Fix: have the well-known artifact builder strip the leading `{skill_name}/` component (or refactor `_build_file_structure` to return unprefixed paths and let the two existing callers add the prefix — cleaner, but it touches the vNFS path, so it needs the vNFS tests to pass). Do not change the semantics of the existing `export-anthropic` endpoint; external consumers may depend on the nesting.

### 5.3 Secondary gotchas

| # | Issue | Handling |
| --- | --- | --- |
| 1 | SBS names are unconstrained; CLI names are `^[a-z0-9-]+$` | slug + deterministic dedupe (§4.2) |
| 2 | `description` > 1024 chars invalidates a v0.2.0 entry | truncate on the boundary, don't reject |
| 3 | Empty description invalidates the entry | fall back to a synthesized `"Skill: <name>"` |
| 4 | Archives may not contain symlinks | `_build_file_structure` emits plain bytes only — safe today, worth a test |
| 5 | 1000-file / 50 MiB archive caps | log a warning and skip an oversized skill rather than serve a rejected artifact |
| 6 | 10 s discovery timeout | the artifact cache (§4.4) is what makes this hold on a large store |
| 7 | Index/artifact race dropping skills silently | serve both from one cached byte string; content-addressed URLs remove it entirely |
| 8 | Telemetry leaks hostname + artifact URLs | document `DISABLE_TELEMETRY=1` (§1.8, §4.6) |
| 9 | CORS is configured app-wide ([server.py:417](../../src/skillberry_store/fast_api/server.py#L417)) | irrelevant to the CLI, but keep the new routes consistent for browser use |
| 10 | `include_in_schema=False` on well-known routes | keeps `/openapi.json` (and the generated CLI/MCP surface) clean; matches how `/ui` routes are registered |
| 11 | **Never add a root SPA catch-all.** A `200 text/html` answer for a missing index is *silently* discarded by the CLI, exactly as happens on skills.sh today (§1.7.1) — the operator sees "no skills found" with no clue why. SBS's `/ui/{path:path}` is correctly scoped under `/ui` ([server.py:491](../../src/skillberry_store/fast_api/server.py#L491)); keep it that way, and keep the well-known 404 a JSON 404 | add a regression test asserting `GET /.well-known/agent-skills/index.json` is `application/json`, never `text/html` |

---

### 5.4 Blocker C: `SKILL.md` frontmatter is not YAML-escaped → skills dropped or silently corrupted

`generate_skill_md()` builds frontmatter by string interpolation ([exporter.py:49-81](../../src/skillberry_store/tools/anthropic/exporter.py#L49-L81)):

```python
content += f"description: {skill.get('description', 'No description provided')}\n"
```

SBS descriptions are free-form text. The CLI parses frontmatter with the `yaml` package and requires `data.name` and `data.description` to be strings, dropping the skill otherwise (§1.4). Verified against the real function:

| Description | Emitted frontmatter | `yaml.safe_load` | CLI outcome |
| --- | --- | --- | --- |
| `Line one.\nLine two: with colon.\n- bullet` | raw newlines injected into the block | **`ParserError`** | skill **dropped**, no diagnostic |
| `Use when: reviewing code` | `description: Use when: reviewing code` | **`ScannerError`** | skill **dropped** |
| `"quoted" #hash @at` | `description: "quoted" #hash @at` | parses to **`'quoted'`** | **installs with a silently truncated description** |
| `None` (key present, value `None`) | `description: None` | parses to `'None'` | installs with the literal string `"None"` |

The third row is the dangerous one: it succeeds, so nothing surfaces the problem, and the agent gets a description that no longer says when to use the skill. A colon followed by a space is entirely ordinary in a skill description ("Use when: …"), so this is not an edge case.

**Fix** — serialise instead of interpolating, in the well-known path:

```python
import yaml

def render_frontmatter(fields: Dict[str, Any]) -> str:
    """Emit YAML frontmatter that round-trips through a real parser.

    Interpolating free-form text into `key: value` breaks on newlines and on
    `: `, and quietly truncates anything a YAML scanner reads as a comment or a
    quoted scalar. The consuming CLI parses frontmatter with a YAML library and
    drops any skill whose `name`/`description` is not a string, so an unescaped
    description is an uninstallable skill. See docs/design/npx.md §5.4.
    """
    body = yaml.safe_dump(
        fields,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10**6,          # never line-wrap: a folded scalar changes the text
    )
    return f"---\n{body}---\n\n"
```

`yaml.safe_dump` quotes or block-scalars whatever needs it, so all four rows above round-trip. Keep `width` effectively infinite — the default 80-column folding rewrites long descriptions with inserted newlines, which is legal YAML but silently reflows the text an agent reads.

Scope note: this is a **pre-existing latent defect in the existing export path**, not something the npx work introduces — a multi-line description already produces a malformed `SKILL.md` in `GET /skills/{name}/export-anthropic` and in the vNFS tree today. The npx work is what makes it *load-bearing*, because a malformed file now means an uninstallable skill rather than a slightly-off download. Fixing it centrally in `generate_skill_md` is therefore the better call and is worth doing as its own commit, ahead of §6.1 — but it does change the bytes of existing exports, so it needs the exporter tests updated in the same change.

**Tests:** each of the four rows above round-trips through `yaml.safe_load` to the original string; a 2000-character description does not gain a newline; a description that is `None` or missing yields the synthesized fallback, not `"None"`.

### 5.5 Blocker D: the index would publish every historical version of every skill

`uuid` is unique; **`name` is not**. Every object sharing a name is linked on the `parent` chain, and the HEAD is the one that is nobody's parent ([object_handler.py:278-306](../../src/skillberry_store/modules/object_handler.py#L278-L306)). A new version is minted by **`create` with an existing name** — which allocates a fresh uuid and points its `parent` at the current HEAD ([skills_service.py:216-220](../../src/skillberry_store/services/skills_service.py#L216-L220)). `update` is *not* what creates a version: it writes back to the same uuid ([skills_service.py:563](../../src/skillberry_store/services/skills_service.py#L563)) and only re-points `parent` when the name changes.

`SkillsService.list_all()` calls `handler.list_all_dicts()` with **no HEAD filter** ([skills_service.py:376](../../src/skillberry_store/services/skills_service.py#L376)), so it returns *every* object on every chain.

Building the index off `list_all()` — which §4.4 and §6.3 as originally written imply — would therefore publish a skill once per revision. All revisions share a name, so they all slug identically, and §6.3's collision handling would mint `pdf-forms`, `pdf-forms-a3f1`, `pdf-forms-9c02`… for what is one logical skill. Users would be offered a menu of indistinguishable entries, most of them stale.

**Fix** — enumerate HEADs, which the store already tracks in a lookup cache:

```python
names = service.handler.name_cache.get_all_names()          # name -> HEAD uuid
heads = [service.handler.name_cache.get_head(n) for n in names]
```

([lookup_cache.py:19-36](../../src/skillberry_store/modules/lookup_cache.py#L19-L36)). This is the same resolution `_resolve_uuid` performs for a name, so "the skill the index publishes" and "the skill `GET /skills/{name}` returns" stay the same object by construction.

Two useful properties follow. First, **one entry per name is guaranteed by construction**, not by index-side deduplication: `name_cache` holds a single uuid per name, and `_find_head_uuid` falls back to the first object when no clear HEAD exists ([object_handler.py:329-331](../../src/skillberry_store/modules/object_handler.py#L329-L331)), so even a forked or cyclic chain yields exactly one published entry. Second, HEAD names are unique, so slug collisions can now only arise from *slugging* (`"PDF Forms"` and `"pdf-forms"` both → `pdf-forms`); the uuid-suffix fallback in §6.3 stays as a backstop but becomes rare.

The fork case is worth one line of operator documentation rather than code: on a chain with no unambiguous HEAD, *which* object gets published follows cache iteration order. That is pre-existing behaviour shared with `GET /skills/{name}`, so the index is not making it worse — but an operator debugging "the index published the wrong version" should be pointed at the chain, not at the index.

**Tests:** `create` the same skill name three times, assert the index contains exactly **one** entry for it and that its uuid equals `name_cache.get_head(name)` and its digest matches that object's export. (Note: `update`-ing a skill three times would *not* exercise this — it mutates one uuid in place. The test must use repeated `create`.) Also assert no two index entries share a slug.

### 5.6 Gap: slug stability across store mutations

§6.3's original tiebreak — sort by `(name, uuid)`, suffix the later ones — is deterministic for a *fixed* store but not *stable* across mutations: adding a skill whose uuid sorts lower can take the bare slug and push an existing skill onto a suffixed one. The slug is the install name recorded in the client's `skills-lock.json`, so a shifted slug silently orphans an installed skill and `npx skills update` can no longer find it.

**Fix:** break ties by `created_at` (oldest keeps the bare slug), fall back to uuid only when timestamps tie, and never re-assign a slug that is already the bare form. §5.5 makes this mostly moot — HEAD names are unique — but the rule should still be written down and tested, because the failure is invisible to the operator and shows up as a broken update on a user's machine.

**Tests:** given two skills that slug identically, adding a third does not change either existing slug; the oldest `created_at` holds the bare slug across a rebuild.

### 5.7 Gap: `file:` tag paths are unvalidated (pre-existing; separate report warranted)

`build_file_structure_from_snippets` / `..._from_tools` take the path from a `file:<path>` tag verbatim, stripping only a leading skill-name component ([exporter.py:120-146](../../src/skillberry_store/tools/anthropic/exporter.py#L120-L146)). Nothing rejects `..`, an absolute path, or a backslash. Verified against `_build_file_structure`:

| Snippet tag | Resulting archive path |
| --- | --- |
| `file:../../escape.txt` | `demo/../../escape.txt` |
| `file:/abs/path.txt` | `demo//abs/path.txt` |

Two distinct consequences:

- **For this feature (must fix):** the CLI's `normalizeArchivePath` rejects such an entry by **throwing**, which aborts extraction of the *entire* archive (§1.4) — so one bad tag makes the whole skill uninstallable. The well-known artifact builder must drop or reject unsafe paths itself, and log which skill and which tag, so the operator can see why a skill is not being published.
- **Outside this feature (report separately):** `export_skill_to_directory` writes `Path(output_dir) / rel_path` ([exporter.py:358-360](../../src/skillberry_store/tools/anthropic/exporter.py#L358-L360)), so the first row resolves to `/tmp/escape.txt` — outside the vNFS export directory, written as the server process user. Preconditions are non-trivial (an authenticated caller with snippet/skill `create`, then a vNFS server start), and `FileHandler._validate_path` does not cover this path because the write does not go through it. **This is a pre-existing path-traversal issue in the vNFS export, not something the npx work introduces, and it should not be fixed silently as a drive-by inside this feature.** It is called out here so it is not lost; it deserves its own issue and its own decision about severity.

**Tests (for the npx path):** a skill carrying an unsafe `file:` tag is excluded from the index with a WARNING naming the skill and the tag; every published archive path is relative, contains no `..`, and no backslash or drive letter.

### 5.8 Smaller gaps

| # | Gap | Fix |
| --- | --- | --- |
| 1 | **Frontmatter `name` is the SBS name, not the slug.** The install directory is the slug, but `generate_skill_md` writes `name: {skill['name']}`. Claude Code and most agents key on the frontmatter name and expect lowercase-hyphen, so `name: PDF Forms` in a `pdf-forms/` directory is a fidelity bug | emit the slug as the frontmatter `name`; keep the original SBS name in a `metadata` field if it is worth preserving |
| 2 | **The artifact cache is unbounded.** §6.3 caches full archive bytes per skill with no eviction — a 50-skill store with 1 MB skills holds ~50 MB resident for the process lifetime | bound it (LRU on total bytes, e.g. 64 MiB) and document the footprint; a miss is cheap, a leak is not |
| 3 | **`modified_at` is `Optional[str]`.** It is reliably set on create ([skills_service.py:216](../../src/skillberry_store/services/skills_service.py#L216)) and update ([skills_service.py:559](../../src/skillberry_store/services/skills_service.py#L559)), so the §6.3 cache key is sound for normally-created objects — but an imported or hand-placed object could carry `None`, and a `None` key that never changes means the cache **never invalidates** | treat a `None` timestamp as always-stale (rebuild every time) rather than as a cache key |
| 4 | **`HEAD` on the well-known routes returns 405.** Verified: `@app.get` registers GET only — the same trap the `/ui` handler documents ([server.py:484-490](../../src/skillberry_store/fast_api/server.py#L484-L490)). The CLI never issues an HTTP HEAD (its only `"HEAD"` is a git ref), so this is cosmetic for the feature, but proxies and uptime monitors do | optional. If added via `api_route(methods=["GET","HEAD"])`, the ACL audit then requires `HEAD /.well-known/*` in the allowlist too — every method on a route must be allow-listed |
| 5 | **Renaming a skill changes its slug**, so it leaves the index under the old name and reappears under the new one; an installed copy is orphaned | expected behaviour, not a defect — document it in the operator notes alongside `npx skills update` |

### 5.9 Checked and cleared

Worth recording so these are not re-litigated in review:

| Concern | Result |
| --- | --- |
| Does FastAPI route `/{slug}.zip` correctly, given the dot? | **Yes** — verified: `/…/pdf-forms.zip` → `slug="pdf-forms"`; it does not shadow `index.json` or `ns/{namespace}/index.json` |
| Does the unauthenticated index return zero skills because no tenant is in scope? | **No** — content is not tenant-scoped; only the plugin layer reads the ambient subject (§6.4) |
| Cold-cache thundering herd across a 50-skill store? | Bounded in practice — the CLI fetches the index once (one build of all N), then artifacts in parallel as cache hits. Two concurrent cold installs cost one duplicate build |
| Can the CLI's bundled zip reader handle our archives? | Yes — it supports stored + deflate and verifies CRC32; `build_deterministic_zip` writes deflate with known sizes from a seekable buffer |

---

### 5.10 Remaining ambiguities to settle before implementation

Not defects — genuinely unresolved choices, each with the constraint that forces it. Listed so none of them is discovered during coding.

| # | Ambiguity | Constraint that forces the choice |
| --- | --- | --- |
| 1 | **The token path needs its own allowlist entry, and it must be a broad one.** `/pub/{token}/.well-known/…` does **not** start with `/.well-known/`, so §6.4's globs do not cover it. And `_path_matches` supports only *exact* or *trailing-`*` prefix* ([config.py:227-231](../../src/skillberry_store/access_control/config.py#L227-L231)) — there is no middle wildcard, so `GET /pub/*/.well-known/*` is inexpressible. The only option is `GET /pub/*`. **Consequence: `/pub/` must be a dedicated namespace with nothing else ever mounted under it.** Same constraint applies to the §6.7 namespace shape |
| 2 | ~~**The server does not know its own public URL.**~~ **Resolved: add `SBS_PUBLIC_URL`** — see §5.11 |
| 3 | ~~**Root index absent or 404?**~~ **Resolved (§6.4):** per §4.3.8 there is no root or aggregate index in either mode. Both modes serve only `/pub/{ref}/…`, differing in what `{ref}` holds — so the question does not arise and an aggregate index cannot be reached by accident |
| 4 | ~~**Which tenants get a derived token?**~~ **Resolved: only tenants with a real `standalone.users` entry** — `[u.tenant_id for u in cfg.users]` ([config.py:89-93](../../src/skillberry_store/access_control/config.py#L89-L93)). This is *simpler* than the alternative, not more complex: the candidate set is one list comprehension over `cfg.users`, where collecting every tenant named in bindings would mean walking `bindings[].subjects[]` and de-duplicating. Virtual subjects like `plugin-user` are excluded for free |
| 5 | ~~**Token encoding.**~~ **Resolved (§4.3.7):** base64url of the full 32-byte HMAC digest, `=` stripped — 43 URL-safe characters |
| 7 | **`_npx_install` on a superseded version.** An install URL always installs the HEAD (§5.5), so returning one from `GET /skills/{old-uuid}` would offer a command that installs something other than what the caller is looking at. **Decided: omit the field for non-HEAD objects** — recorded here because it is a silent surprise otherwise, and test #24 pins it |
| 6 | ~~**Operator-visible failure modes.**~~ **Resolved: neither is a case to handle.** *Secret rotation* invalidating every URL is not a hazard — it is the intended **global revoke**, and the only one the derived scheme has (§4.3.3); document it as a control, not a warning. *Tenant rename* is already equivalent to delete-plus-create in the ACL config: it invalidates that tenant's sessions and role bindings too, so the install URL is not a special case and needs no handling. One line each in the operator notes; no code |


---

### 5.11 `SBS_PUBLIC_URL` — the externally-visible base URL

**Decided: an explicit env var, authoritative when set.** The install command is absolute and the server composes it, so it must know the URL a *user's terminal* can reach — which is not derivable from how the process is bound.

```python
class SBSettings(BaseSettings):
    ...
    public_url: Optional[str] = Field(None, validation_alias="SBS_PUBLIC_URL")
```

Same `Field(..., validation_alias=...)` idiom as the existing settings ([server.py:58-68](../../src/skillberry_store/fast_api/server.py#L58-L68)), so it is configurable from the environment, from `container.env`, and from the Docker/Podman run targets with no extra plumbing. Normalised once at startup: scheme required, trailing slash stripped, and a boot log line stating the value in use.

#### Why request-based derivation cannot be the primary mechanism

The obvious alternative — read `X-Forwarded-Proto` / `X-Forwarded-Host` off the request — is **unreliable in exactly the deployments that need it**. `uvicorn.run(...)` is called without `proxy_headers` / `forwarded_allow_ips` ([server.py:574-580](../../src/skillberry_store/fast_api/server.py#L574-L580)), so uvicorn honours forwarded headers only from its default trusted source, `127.0.0.1`. Behind a container ingress or a load balancer — where the proxy is *not* on loopback — those headers are ignored, and `request.base_url` reports the internal bind address instead. The failure is silent: the store hands out install commands pointing at `http://0.0.0.0:8000`.

So the precedence is:

| Order | Source | Note |
| --- | --- | --- |
| 1 | `SBS_PUBLIC_URL` | authoritative whenever set |
| 2 | `request.base_url` | correct for a directly-reachable server; **only trustworthy behind a proxy if the operator also sets uvicorn's `forwarded_allow_ips`** |
| 3 | — | if neither yields an absolute `http(s)` URL, log a **warning at boot** and omit `_npx_install` rather than emit a command that cannot work |

Option 3 matters: a wrong URL is worse than an absent one, because the user discovers it only when npx fails against an address that means nothing on their machine.

#### It is useful beyond npx

Worth setting regardless of this feature, and worth mentioning in the operator docs as such: the same value is what the UI, the CLI, and any copy-paste snippet need in order to name the store. `sts_url` is currently built from the bind address (`f"http://{sbs_host}:{sbs_port}"`, [server.py:213](../../src/skillberry_store/fast_api/server.py#L213)) and is internal-only by construction. Unifying the two is out of scope here, but `SBS_PUBLIC_URL` is the value a follow-up would use.

**Tests:** set/unset precedence; a value without a scheme is rejected at startup; a trailing slash is normalised so the composed URL never contains `//pub/`; with neither source available, `_npx_install` is omitted and a warning is logged rather than a bogus command returned.


---

### 5.12 `npx_publish` — opt-in, declared in the access-control config

**Decided: npx publishing is off unless the operator turns it on, and the switch lives in the ACL config file, not in an env var.**

That is the right home because it *is* an access-control decision: it is the one setting that makes skill **content** reachable without a session. Putting it beside `unauthenticated_paths` means anyone reviewing the file's security posture sees it in the same glance, rather than having to correlate YAML with a container environment.

```yaml
# access_control_config.yaml / .standalone
mode: standalone

# Publish skills for `npx skills add` (docs/design/npx.md).
# OFF by default. Turning this on serves SKILL.md CONTENT — not just
# metadata — to anyone holding a per-skill install URL, with no session
# (§4.3.3). One skill per URL; the URL's token grants that skill only.
npx_publish: false

unauthenticated_paths:
  ...
  - GET /pub/*
```

The loader reads known keys with `raw.get(...)` and ignores unknown ones ([config.py:350-387](../../src/skillberry_store/access_control/config.py#L350-L387)), so this is purely additive — no migration, and an older config file keeps loading with the feature off.

| Decision | Choice | Why |
| --- | --- | --- |
| Default in code | **`False`** | fail-closed. A mode-dependent default (on when `disabled`, off when `standalone`) is exactly the kind of subtlety that surprises someone who later enables auth |
| Shipped `access_control_config.yaml` (`mode: disabled`) | may ship `true` with the comment above | a bare dev checkout is usable out of the box, and nothing is protected there anyway |
| Shipped `access_control_config.yaml.standalone` | ships `false` | the demo operator opts in deliberately; flip it to showcase npx on the live demo |
| Route registration | **gated on the flag** | when off, `/pub/*` is not registered at all, so the surface genuinely does not exist rather than existing-but-refusing |
| Allowlist entry | **present unconditionally** | harmless when the routes are absent (it matches nothing and a request 404s either way), and it means enabling the feature is a one-line edit rather than two |
| Boot log | one line, always | states whether publishing is on and, when on, how many skills are publishable — so "is this store exposed?" is answerable from the log |

**Tests:** with `npx_publish: false`, `/pub/<anything>` returns 404 and no route appears in the app's route table; with it `true`, the per-skill URL works; the flag is absent from an old config file and the feature stays off; the boot log line states the mode in both cases; and `audit_rbac_coverage` passes either way (the routes carry no `@requires` by design, §4.5).


---

### 5.13 One archive builder

**Decided (§9 Q4): one deterministic zip function, used by every caller.** `export_skill_to_anthropic_format` stops having its own zip loop and becomes a thin wrapper over `build_deterministic_zip` (§6.1).

This supersedes §5.1's "keep it separate" recommendation. The argument for separation was that the human-facing `export-anthropic` download has no digest contract and its real mtimes are friendlier to someone unzipping by hand. The argument against is stronger: two code paths that must produce the same archive, where only one of them is exercised by the digest tests, is precisely the arrangement in which they silently diverge.

#### What "one builder" does and does not unify

The pipeline is already shared up to the last step, so the change is small:

```
_build_file_structure(skill, tools, snippets, tool_modules)   # shared today
        ↓  (optional)
strip_skill_prefix(files, skill_name)                         # §6.2 — caller's choice
        ↓
build_deterministic_zip(files)                                # shared after this change
```

The **path layout stays a caller decision**, and deliberately so:

| Caller | Prefix | Why |
| --- | --- | --- |
| `GET /skills/{name}/export-anthropic` | keeps `<skill-name>/` | a human unzipping wants a containing directory, not files splattered into their cwd |
| well-known artifact | stripped to root | the CLI requires a root-level `SKILL.md` and strips nothing (§5.2) |

So "one builder" means one *zip* function and one *file-structure* function, with the prefix as a parameter — not one output. Collapsing the layouts too would break the download UX for no gain.

#### The cost, accepted

Every existing `export-anthropic` download changes bytes: entries get a fixed `1980-01-01` timestamp and sorted order. A user unzipping sees 1980 mtimes, which looks odd and is harmless; in exchange the endpoint becomes reproducible, which is a virtue in its own right. The exporter's existing tests are updated in the same commit — that is the whole cost, and it is paid once.

This moves the work from §6.1 ("new function, nothing existing changed") to §6.1 amended: `build_deterministic_zip` is new, and `export_skill_to_anthropic_format` is rewritten to call it.


---

### 5.14 "Content-addressed" artifact URLs — what the choice actually is (§9 Q5)

The index publishes a digest, and the CLI **drops any skill whose downloaded bytes do not hash to it** (§1.4). Index and artifact are two separate HTTP requests, so the question is what the artifact URL should name. Three forms, all verified to resolve correctly against the index URL:

#### First: the token is inherited, not embedded — in all three options

Worth stating before the comparison, because it is the part that is easy to miss. **None of the three forms puts the token in the index body.** Each entry's `url` is *relative*, and the CLI resolves it against the URL the index was fetched from:

```js
const artifactUrl = new URL(entry.url, indexUrl).toString();   // cli.mjs:3202
...
const response = await fetch(entry.artifactUrl);               // cli.mjs:3367
```

So for an index fetched from `https://store/pub/TOKEN/.well-known/agent-skills/index.json`, a relative `"pdf-forms.zip"` resolves to `https://store/pub/TOKEN/.well-known/agent-skills/pdf-forms.zip` — the `/pub/TOKEN/` prefix carries forward on its own.

Three properties follow, and they are why relative URLs are the right choice rather than a stylistic one:

| | |
| --- | --- |
| **The index body is credential-free** | the token appears only in the URL the user pasted. An index response that gets logged, cached or pasted leaks nothing |
| **The artifact inherits the same gate** | it cannot accidentally be served from an unprotected path, because it is resolved *relative to* the protected one (test #22) |
| **It survives a reverse proxy** | an absolute `url` would hard-code a hostname the store may not know (§5.11); a relative one is whatever the client actually reached |

The token question is therefore **orthogonal** to the A/B/C choice below: all three inherit it identically, and they differ only in *which bytes the URL names*. Option C's `../../artifacts/…` still resolves under `/pub/TOKEN/` — verified — so it is equally gated.

#### A — stable URL (the current design)

```json
{"name": "pdf-forms", "type": "archive",
 "url": "pdf-forms.zip",
 "digest": "sha256:abc123…"}
```
→ `https://store/pub/TOKEN/.well-known/agent-skills/pdf-forms.zip`

One URL per skill, forever. Its *content* changes whenever the skill is edited.

**The race:** the index is built at T1 and publishes `sha256:abc…` for the bytes as they were then. The CLI fetches the archive at T2. If someone edits the skill in between, the server serves different bytes, the hash does not match, and the skill **vanishes from the install with no error message**.

#### B — digest as a query selector

```json
{"url": "pdf-forms.zip?digest=sha256:abc123…", "digest": "sha256:abc123…"}
```
→ `https://store/pub/TOKEN/.well-known/agent-skills/pdf-forms.zip?digest=sha256:abc123…`

The artifact handler serves *those* bytes from cache when asked for that digest. The race closes: the T2 request names the T1 bytes explicitly. Same route, same pretty path, no new endpoint — the query string survives relative-URL resolution and the CLI passes the whole URL through to `fetch` verbatim.

#### C — fully content-addressed path

```json
{"url": "../../artifacts/sha256-abc123…/pdf-forms.zip", "digest": "sha256:abc123…"}
```
→ `https://store/pub/TOKEN/artifacts/sha256-abc123…/pdf-forms.zip`

Same guarantee as B, plus the URL can be cached forever (`Cache-Control: immutable`) because its content can never change — the reason a CDN would want it. Costs an extra route and an uglier URL.

#### Why the cache does not close the race on its own

Worth being explicit, because it is the intuitive assumption and it is wrong. The §6.3 cache is keyed on `modified_at`. At T1 the index request builds bytes B1 and publishes digest D1. The edit at T1.5 changes `modified_at`. At T2 the artifact request finds a *different* cache key, rebuilds as B2, and serves bytes whose digest is D2 ≠ D1. The cache makes the common case fast; it does not make the two requests agree. **Only keying the artifact lookup by the requested digest does that** — which is exactly what B and C do.

#### Decided: ship A, with B's server side in place

| | |
| --- | --- |
| **Window size** | With per-skill single-entry indexes (§4.3.8) the window is one index fetch plus one artifact fetch — milliseconds. Losing the race needs an edit landing inside it |
| **Consequence** | A silently dropped skill. Annoying and opaque, but the user re-runs one command and it works |
| **Cost of B** | A query parameter the index composes and the handler honours — roughly ten lines, no new route, no URL shape change |
| **Cost of C** | An extra route, a second URL shape, and retaining blobs by hash |

**Decided: ship A.** B is a pre-planned upgrade rather than a redesign — the index already composes the artifact URL, so emitting `?digest=` later changes nothing any caller depends on. **C** is reserved for a CDN requirement that does not exist.

One thing lands now regardless, because it costs almost nothing and is what makes B a one-line change later: the artifact handler **accepts and honours an optional `digest` query parameter** — when present, it serves those exact bytes from cache and 404s on a miss; when absent, it serves the current bytes. The index does not emit it yet. That way the race-closing behaviour is implemented and tested from day one, and enabling it is a single edit to the index builder.


---

## 6. Detailed implementation plan

Concrete, ordered, and written so that each step lands as a reviewable commit that leaves the tree green. Every step names the file it touches. §6.4–§6.6 cover the standalone-ACL requirement, with `access_control_config.yaml.standalone` — the `skillberry` / `skillberry-admin` demo config — as the worked test case.

### 6.0 Step 0 — Fix blocker C: YAML-safe frontmatter (§5.4)

**File:** `src/skillberry_store/tools/anthropic/exporter.py` — `generate_skill_md()` ([exporter.py:49-81](../../src/skillberry_store/tools/anthropic/exporter.py#L49-L81))

Goes **first**, and unlike §6.1/§6.2 it changes an existing function rather than adding a new one, because the defect is not npx-specific: a multi-line or colon-bearing description already emits malformed `SKILL.md` from `GET /skills/{name}/export-anthropic` and into the vNFS tree. Fixing it only in the well-known path would leave two divergent renderers and the same bug in the older one.

Replace the interpolated frontmatter with `yaml.safe_dump` (§5.4), and while in there:

- emit the **slug** as `name`, not the raw SBS name (§5.8 #1) — accept it as a new optional argument so the existing callers keep today's behaviour until Step 3 passes one;
- substitute the synthesized `f"Skill: {name}"` when the description is absent, empty, **or `None`** — `skill.get("description", default)` returns `None` when the key exists with a `None` value, which is how `description: None` reaches the file today;
- keep `width` effectively infinite so a long description is never reflowed.

This commit changes the bytes of existing exports, so it updates the exporter's own tests in the same change. That is the cost of fixing it centrally, and it is worth paying once.

**Tests:** the four §5.4 rows round-trip through `yaml.safe_load`; a 2000-character description gains no newline; `description: None` yields the fallback, not `"None"`; the license line still appears when a `LICENSE.txt` snippet is present.

### 6.1 Step 1 — Fix blocker A: deterministic archive bytes (§5.1)

**File:** `src/skillberry_store/tools/anthropic/exporter.py` (new function, nothing existing changed)

```python
# Fixed epoch for every entry. The ZIP format's own minimum (1980-01-01) rather
# than the Unix epoch, which zipfile cannot represent. Any constant works; this
# one is conventional for reproducible builds.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def build_deterministic_zip(files: Dict[str, bytes]) -> bytes:
    """Zip ``files`` so identical content always yields identical bytes.

    ``ZipFile.writestr(name, data)`` with a *string* name stamps each entry
    with ``time.localtime()``, so the same skill hashes differently on every
    call. The well-known discovery protocol publishes a sha256 of the exact
    bytes it will serve and drops any skill whose artifact does not match, so
    a wall-clock timestamp makes every skill uninstallable. Pin the mtime, the
    permission bits and the entry order instead. See docs/design/npx.md §5.1.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[path])
    return buf.getvalue()
```

Deliberately a **new** function, not a change to `export_skill_to_anthropic_format()`: the human-facing `GET /skills/{name}/export-anthropic` download has no digest contract, and real mtimes are friendlier for someone unzipping by hand. Unifying the two is a defensible follow-up (§9 Q4) but should not ride along on this commit.

**Tests** (`src/skillberry_store/tests/tools/`): two calls separated by a `time.sleep` produce identical bytes; key order in the input dict does not affect output; a round-trip through `zipfile.ZipFile` recovers every file.

### 6.2 Step 2 — Fix blocker B: root-relative paths (§5.2)

**File:** `src/skillberry_store/tools/anthropic/exporter.py`

`_build_file_structure()` prefixes every key with `{skill_name}/` ([exporter.py:302](../../src/skillberry_store/tools/anthropic/exporter.py#L302)). The CLI looks up root `SKILL.md` exactly and strips no leading directory, so that prefix makes the archive unreadable.

Add a stripping helper rather than changing `_build_file_structure()`, whose prefixed output both existing callers (`export_skill_to_anthropic_format`, and the vNFS `export_skill_to_directory`) depend on:

```python
def strip_skill_prefix(files: Dict[str, bytes], skill_name: str) -> Dict[str, bytes]:
    """Re-root ``files`` so ``SKILL.md`` sits at the archive root.

    ``_build_file_structure`` keys every path under ``<skill_name>/`` because
    both existing consumers want that container directory. The well-known
    protocol does not: it requires a root-level ``SKILL.md`` and does not strip
    a leading component. See docs/design/npx.md §5.2.
    """
    prefix = f"{skill_name}/"
    return {
        (k[len(prefix):] if k.startswith(prefix) else k): v
        for k, v in files.items()
    }
```

**Tests:** the result contains `SKILL.md` at the root and no key starting with the skill name; no key is absolute, contains `..`, a backslash, or a drive letter (mirroring the CLI's `normalizeArchivePath` rejections, §1.4); a file legitimately named `SKILL.md` inside a subdirectory is not promoted.

> Assert the guards even though `_build_file_structure` cannot currently emit such a path — the test documents the CLI's contract, so a future change to snippet `file:` tag handling fails here rather than in a user's terminal.

### 6.3 Step 3 — Slug mapping and the published-entry builder

**New file:** `src/skillberry_store/tools/wellknown.py` — pure functions, no FastAPI import, so it is unit-testable without an app.

```python
@dataclass(frozen=True)
class PublishedSkill:
    slug: str            # CLI-legal install name
    uuid: str            # SBS identity
    name: str            # original SBS name
    description: str     # truncated to 1024
    digest: str          # "sha256:<64 hex>"
    payload: bytes       # the exact archive bytes the digest covers
```

Responsibilities:

| Function | Contract |
| --- | --- |
| `to_slug(name)` | §4.2 normalisation; returns `""` when nothing survives |
| `head_skills(service)` | enumerate HEADs via `name_cache.get_all_names()` + `get_head(name)` — never `list_all()`, which returns every historical version (§5.5). Needed for slug assignment and for `_npx_install` on `list`, not for an index |
| `assign_slugs(skills)` | on collision, oldest `created_at` keeps the bare slug; later ones get `-{uuid[:4]}`, then `-{uuid[:8]}`; uuid breaks a `created_at` tie. Skip and log WARNING on empty slug. **Stable across store mutations**, not merely deterministic for a fixed store (§5.6) |
| `publishable(skill)` | per-skill predicate (§4.3.8): the skill is a HEAD, and carries no unsafe `file:` path — logging the skill and the offending tag when it does (§5.7). Deliberately applies **no** state or tag gate (§4.3.1) |
| `resolve_ref(ref)` | `{ref}` → skill, per §6.4: token match in `standalone`, slug lookup in `disabled`; `None` on any failure so the caller can 404 uniformly |
| `safe_archive_paths(files)` | reject absolute paths, `..`, backslashes and drive letters before zipping — the CLI aborts the whole archive on one bad entry (§5.7) |
| `normalise_description(text)` | truncate to 1024 chars on a word boundary; substitute `f"Skill: {name}"` when empty — an empty or over-long description invalidates a v0.2.0 entry (§1.3) |
| `build_entry(uuid)` | export → strip prefix → deterministic zip → sha256; returns `PublishedSkill` |

`build_entry` is where the §6.1/§6.2 fixes are composed:

```python
files = _build_file_structure(skill, tools, snippets, tool_modules)
files = strip_skill_prefix(files, skill["name"])
payload = build_deterministic_zip(files)
digest = "sha256:" + hashlib.sha256(payload).hexdigest()
```

Reuse `SkillsService.export_anthropic`'s existing gathering logic ([skills_service.py:732-790](../../src/skillberry_store/services/skills_service.py#L732)) rather than duplicating the tool/snippet walk — extract it into a `_gather_export_inputs(uuid)` helper that both call.

**Cache** (§4.4), keyed for correctness rather than just speed:

```python
key = (skill["modified_at"],
       tuple(sorted(t["modified_at"] for t in tools)),
       tuple(sorted(s["modified_at"] for s in snippets)))
```

Every input is a `DictCache` read — in memory, no disk I/O ([dict_cache.py:9-21](../../src/skillberry_store/modules/dict_cache.py#L9-L21)). Cache the whole `PublishedSkill`, so the index and the artifact response are served from **one byte string** and cannot disagree (§5.3 #7).

Two constraints on the cache itself, both from §5.8: **bound it** (LRU over total bytes, default 64 MiB) so a large store does not pin archives for the process lifetime; and treat a `None` `modified_at` as **always stale** rather than as a key, or an object with no timestamp would be cached once and never rebuilt.

**Tests:** slug table (spaces, underscores, uppercase, `--`, edge hyphens, 64-char cap, empty→skipped); **slug stability** — adding a third colliding skill changes neither existing slug (§5.6); **HEAD-only** — three `create`s of the same name yield exactly one entry, whose uuid equals `name_cache.get_head(name)` (§5.5); an unsafe `file:` tag excludes the skill with a WARNING naming the tag (§5.7); description truncation and empty-fallback; digest matches `hashlib.sha256(payload)`; mutating a tool's `modified_at` changes the digest, touching nothing does not; a `None` `modified_at` rebuilds every call.

### 6.4 Step 4 — Endpoints, token resolution, and ACL wiring

**Revised for §4.3.8: per-skill only. There is no aggregate index and no `{slug}.zip` at the well-known root.**

#### One route shape, one path prefix

A new module `src/skillberry_store/fast_api/wellknown_api.py`, registered from `SBS.__init__` beside the other `register_*_api` calls ([server.py:271](../../src/skillberry_store/fast_api/server.py#L271)):

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/pub/{ref}/.well-known/agent-skills/index.json` | a **single-entry** discovery index for one skill |
| `GET` | `/pub/{ref}/.well-known/skills/index.json` | alias — the CLI probes both (§1.2) |
| `GET` | `/pub/{ref}/.well-known/agent-skills/{slug}.zip` | that skill's archive. Accepts an optional `?digest=sha256:…` selector — serves those exact cached bytes, 404 on a miss; without it, the current bytes (§5.14) |

`{ref}` is resolved by ACL mode — one prefix, so one allowlist entry covers everything:

| ACL mode | `{ref}` is | Resolution |
| --- | --- | --- |
| `standalone` | a scoped token (§4.3.7) | recompute `HMAC(secret, f"{tenant}|skill:{slug}")` over candidate `(tenant, HEAD slug)` pairs; on match, re-run `authorize(subject, "skills", "list")` |
| `disabled` | the **slug itself** | no auth layer exists, so there is nothing for a secret to protect; `/pub/pdf-forms` is the whole URL |

This settles §5.10 #3: **no root index exists in either mode**, so there is no "absent or 404" question and no risk of an aggregate index being reachable by accident. It also keeps the install command shape identical across modes — only what fills `{ref}` differs.

Artifact URLs stay relative (`"pdf-forms.zip"`), which resolves to `…/pub/{ref}/.well-known/agent-skills/pdf-forms.zip` — verified — so **the archive is protected by the same prefix as the index**, and the token is inherited rather than written into the index body (§5.14). A test should assert both rather than assume them.

#### Handler rules

```python
def register_wellknown_api(app: FastAPI, service=None):
    """Per-skill discovery for `npx skills add` (docs/design/npx.md §4.3.8).

    NOTE: no @requires markers. These paths are in the ACL unauthenticated
    allow-list, so the PEP short-circuits before mapping a route to
    (resource, verb) — access_control/decorator.py forbids the marker on
    allow-listed routes, with /health as precedent.
    """

    @app.get("/pub/{ref}/.well-known/agent-skills/index.json",
             include_in_schema=False)
    def wellknown_index(ref: str):
        skill = resolve_ref(ref)                 # None → 404, never 403
        if skill is None:
            raise HTTPException(404, "Not Found")
        entry = build_entry(skill.uuid)          # §6.3
        return {"$schema": DISCOVERY_SCHEMA_V2,
                "skills": [{"name": entry.slug,
                            "description": entry.description,
                            "type": "archive",
                            "url": f"{entry.slug}.zip",
                            "digest": entry.digest}]}
```

Four rules, each with a concrete failure mode behind it:

1. **Call the service layer directly, never `StoreAPI`.** On an allow-listed path the PEP returns before `set_current_subject` ([deps.py:93-94](../../src/skillberry_store/access_control/deps.py#L93)), so `current_subject()` is `None`. `StoreAPI._admit` reads it ([store_api.py:132](../../src/skillberry_store/plugins/store_api.py#L132)) and raises `PluginIdentityError` → **500** ([server.py:127-135](../../src/skillberry_store/fast_api/server.py#L127-L135)).
2. **Never read `request.state.subject`** — only set on the authenticated path, so touching it is an `AttributeError` on exactly the requests this feature serves.
3. **Always JSON, never HTML** — a stray HTML error body is parsed as an index and silently yields zero skills (§5.3 #11).
4. **404 for every failure** — unknown ref, rotated secret, tenant without `skills:list`, unpublished skill. Never 403, never a distinguishable error (§4.3.1).

#### ACL wiring — three files, same commit

| File | Change |
| --- | --- |
| [config.py:237](../../src/skillberry_store/access_control/config.py#L237) | add the glob to `_DEFAULT_UNAUTH_PATHS` |
| `access_control_config.yaml` | same line + comment |
| `access_control_config.yaml.standalone` | same line + comment — **the §6.5 test case** |

```yaml
  # Per-skill discovery for `npx skills add` (docs/design/npx.md §4.3.8).
  # Read-only, one skill per URL, gated by SBS_WELLKNOWN_ENABLED. The path
  # segment is a scoped capability token (standalone) or a slug (disabled).
  # NOTE: `_path_matches` supports only a trailing '*' — a middle wildcard
  # like /pub/*/.well-known/* is inexpressible, so /pub/ MUST remain a
  # dedicated prefix with nothing else ever mounted under it (§5.10 #1).
  - GET /pub/*
```

One entry covers the index, the alias and the artifact, because all three live under `/pub/`. That is the payoff for choosing a single prefix, and the constraint that makes it mandatory.

### 6.5 Step 5 — Standalone-ACL verification, with the `skillberry` demo config

This is the step that proves the feature works on a deployment like the live demo, which returns `401 missing_authorization` for `GET /skills/` today (§3.1). `access_control_config.yaml.standalone` is the right fixture because it is the **real** file, with real roles and bindings:

| Tenant | Binding | Role | Effective skills verbs |
| --- | --- | --- | --- |
| `skillberry` | `skillberry-base` | `base-user` | `list, get, search, execute` — **no** `create/update/delete` |
| `skillberry-admin` | `skillberry-admin` | `admin` | `*` |
| `plugin-user` | `plugin-agent-binding` | `plugin-agent` | virtual subject, no password, cannot log in |

**New file:** `src/skillberry_store/tests/fast_api/test_wellknown_api.py`, reusing the `fresh_sbs_factory` fixture from [test_access_control.py:31-54](../../src/skillberry_store/tests/fast_api/test_access_control.py#L31-L54), which writes a config, resets the singletons, and hands back a `TestClient`.

Point it at the real file rather than a hand-written YAML:

```python
REPO_ROOT = Path(__file__).resolve().parents[4]
STANDALONE_CFG = REPO_ROOT / "access_control_config.yaml.standalone"


@pytest.fixture
def skillberry_demo_client(tmp_path, monkeypatch):
    """A TestClient running the *shipped* skillberry/skillberry-admin config.

    Uses the real file, not a copy: if someone adds an endpoint to
    access_control_config.yaml.standalone without allow-listing the
    well-known paths, this fixture is what catches it.
    """
    monkeypatch.setenv("SBS_ACCESS_CONTROL_CONFIG", str(STANDALONE_CFG))
    acl_config.reset_config_cache()
    clean_test_tmp_dir()
    object_handler.clear_object_handlers()
    registry.clear_services()
    yield TestClient(SBS())
    object_handler.clear_object_handlers()
    registry.clear_services()
    acl_config.reset_config_cache()
```

Assertions:

| # | Test | Expected |
| --- | --- | --- |
| 1 | `cfg.mode` | `"standalone"` — the fixture really did enable ACL |
| 2 | `GET /skills/` **no token** | `401 {"detail": "missing_authorization"}` — the store is genuinely locked down |
| 3 | `GET /.well-known/agent-skills/index.json` **no token** | `200`, JSON, correct `$schema` |
| 4 | `GET /.well-known/skills/index.json` **no token** | `200`, identical body (alias works) |
| 5 | `GET /.well-known/agent-skills/{slug}.zip` **no token** | `200`, `application/zip`, `sha256 == index digest` |
| 6 | every index entry | passes all §1.3 v0.2.0 rules, asserted by a `_assert_cli_valid()` helper that mirrors `isValidSkillEntryV2` |
| 7 | each artifact | root `SKILL.md`, parseable frontmatter with `name` + `description`, no symlink entries, ≤ 1000 files, ≤ 50 MiB |
| 8 | `GET /.well-known/agent-skills/no-such-skill.zip` | `404`, and the body is JSON |
| 9 | **`skillberry` authenticated** | with a minted token, `GET /skills/` → `200` **and** the well-known index → `200`. The feature does not disturb the existing tenant's access |
| 10 | `skillberry` authenticated | `POST /skills/` still `403` — `base-user` has no `create`, and nothing here widened it |
| 11 | per-skill index vs. `/skills/` | each `skill:<slug>` URL returns an index of **exactly one** entry, that skill, and only for a tenant holding `skills:list`; a tenant without it gets 404. Every skill in her authenticated listing has a working install URL, and no URL returns a skill she cannot see (§4.3.8) |
| 12 | `SBS_WELLKNOWN_ENABLED=false` | both well-known paths `404`; `GET /skills/` unchanged |
| 13 | `state: new` skill | absent from the index by default; present once `SBS_WELLKNOWN_STATES=new,approved` |
| 14 | RBAC audit | the app constructed at all — `audit_rbac_coverage` runs in `SBS.__init__` and would have refused to boot on a marker/allowlist mismatch |
| 15 | `GET /skills/{name}?fields=_npx_install` **no bearer** | `401` — learning an install URL requires a session (§4.3.3) |
| 16 | as `skillberry`: read `_npx_install`, then `GET` that URL **no bearer** | `200`; index has **exactly one** entry, that skill (§4.3.8) |
| 17 | same URL after rebuilding `SessionStore` | still `200` — derived, not session-backed (§4.3.3) |
| 18 | rotate `SBS_WELLKNOWN_SECRET`, re-request the old URL | `404`, not `403` |
| 19 | a tenant bound to a role without `skills:list` | no `_npx_install`; and a token whose tenant *later* loses `skills:list` returns `404` — re-authorized per request, not at issue time |
| 20 | the token as an `Authorization: Bearer` value on `GET /skills/` | `401` — never resolvable as a session credential (§4.3.4) |
| 21 | skill A's token requested for skill B's URL | `404` — a token grants exactly one skill (§4.3.7) |
| 22 | the artifact URL from the index | resolves **under** `/pub/{ref}/` and is equally gated; fetching it with a different ref 404s; the index body contains **no** token (§5.14) |
| 25 | `…/{slug}.zip?digest=<current>` | `200`, bytes hash to that digest |
| 26 | `…/{slug}.zip?digest=<stale>` | `404` — the selector is honoured, not ignored (this is what makes §5.14 option B a one-line change later) |
| 27 | edit the skill between fetching the index and fetching the artifact, **without** a digest selector | reproduces the §5.14 race: the CLI would drop the skill. Documents accepted behaviour for option A rather than asserting it never happens |
| 23 | every skill in her authenticated listing | has a working `_npx_install` URL, and no URL returns a skill absent from that listing (§4.3.8) |
| 24 | `GET /skills/{old-version-uuid}?fields=_npx_install` | field **omitted** — an install URL always installs the HEAD, so offering one on a superseded version would mislead (§5.10 #7) |

For #9–#11, mint the session directly instead of logging in:

```python
sessions = client.app.state.acl_sessions
token, _ = sessions.mint("skillberry", [], 60)
headers = {"Authorization": f"Bearer {token}"}
```

That is the existing pattern at [test_plugin_access_control.py:347-350](../../src/skillberry_store/tests/fast_api/test_plugin_access_control.py#L347-L350). It keeps the test independent of the demo file's plaintext passwords — which should not be needed by, or hard-coded into, the suite — while still exercising the file's real bindings. Test #10 is the one that matters most: it proves the well-known route did not widen `base-user`.

> **Test #11 carries two properties at once.** Read one way it is the product requirement — everything she can see, she can install. Read the other way it is the security assertion — nothing she *cannot* see is published. Set equality is what makes a single test cover both directions; a subset check would have caught only one.

### 6.6 Step 6 — Manual end-to-end against the standalone config

Not part of `make test` (needs network and Node ≥ 22). Worth scripting under `scripts/demo/`:

```bash
export SBS_ACCESS_CONTROL_CONFIG=access_control_config.yaml.standalone
make run                                   # ACL on: /skills/ is 401

export DISABLE_TELEMETRY=1                 # do not report this host to Vercel (§1.8)
npx skills add http://localhost:8000 --list          # discovery, no auth, no install
npx skills add http://localhost:8000 -s <slug> -a claude-code -y
ls .claude/skills/<slug>/SKILL.md          # arrived
cat skills-lock.json                       # sourceType: "well-known", wellKnownDigest

# then, in the UI, sign in as `skillberry` and edit that skill
npx skills update <slug>                   # re-downloads only what changed
```

Checkpoints: `--list` works with no credentials while `curl localhost:8000/skills/` still 401s; the lockfile records `sourceType: "well-known"`; and `update` is a no-op until the skill actually changes.

### 6.7 Step 7 — Namespace scoping ("packs")

Mount **under** the allowlisted prefix so §6.4's two globs still cover it:

```
GET /.well-known/agent-skills/ns/{namespace}/index.json
GET /.well-known/agent-skills/ns/{namespace}/{slug}.zip
```

This is the §9 Q1 trade-off, and it has a wrinkle worth stating plainly: the CLI derives the well-known path from the URL's `basePath`, so it probes `{url}/.well-known/agent-skills/index.json`. To make `npx skills add http://host/ns/data-eng` work, `/ns/{namespace}/.well-known/agent-skills/index.json` must **also** exist as a route — which then needs its own allowlist entries. Either accept two allowlist entries per shape, or accept the less pretty install URL. Recommend registering both routes onto the same handler and allowlisting `GET /ns/*/.well-known/*` explicitly, with a comment pointing at this paragraph.

Tests: a scoped index contains only that namespace's skills; an unknown namespace 404s rather than falling back to the root index (the CLI would otherwise install everything the host publishes — the exact failure the CLI's own `WellKnownScopeNotFoundError` exists to prevent, §1.7); `SBS_WELLKNOWN_NAMESPACES` restricts which namespaces resolve.

### 6.8 Files touched, in summary

| File | Change | Step |
| --- | --- | --- |
| `tools/anthropic/exporter.py` | `generate_skill_md` → YAML-safe, slug as `name`, `None`-description fallback | 0 |
| `tools/anthropic/exporter.py` | `build_deterministic_zip`, `strip_skill_prefix`, `safe_archive_paths` | 1, 2 |
| `tools/wellknown.py` | **new** — slugs, filtering, entry builder, cache | 3 |
| `services/skills_service.py` | extract `_gather_export_inputs` from `export_anthropic` | 3 |
| `fast_api/wellknown_api.py` | **new** — `/pub/{ref}` index + artifact routes, `resolve_ref` | 4 |
| `tools/publish_tokens.py` | **new** — `publish_token()` / `resolve_token()` + durable secret (`SBS_WELLKNOWN_SECRET`, else generate-and-persist) | 4 |
| `fast_api/auth_api.py` | *(dropped — there is no store-wide URL to report; §4.3.8)* | — |
| `tools/wellknown.py` | `npx_install_command()` — the single definition used by whoami, get-skill and the UI (§4.3.5) | 4 |
| `services/field_selection.py` + `skills_api.py` | declare the opt-in `_npx_install` flag field for `skill` (§4.3.5) | 4 |
| `fast_api/server.py` | one `register_wellknown_api(...)` call | 4 |
| `access_control/config.py` | two entries in `_DEFAULT_UNAUTH_PATHS` | 4 |
| `access_control_config.yaml` | two allowlist entries + comment | 4 |
| `access_control_config.yaml.standalone` | two allowlist entries + comment | 4 |
| `tests/tools/test_exporter_frontmatter.py` | **new** — the §5.4 round-trip table | 0 |
| `tests/tools/test_wellknown.py` | **new** — unit tests | 1–3 |
| `tests/fast_api/test_wellknown_api.py` | **new** — integration + standalone ACL | 5 |
| `docs/cli.md`, `README.md` | npx quickstart, telemetry note | 8 (§4.8) |

Steps 0–2 are independently correct and can merge first — Step 0 fixes a live defect in the existing export path and is worth landing on its own merits. Step 4 is the only one that changes deployed security posture, and it is one glob pair plus a config knob.

---

## 7. Test plan

The table below is the coverage summary; §6.0–§6.7 give the per-step assertions, and §6.5 is the authoritative list for the standalone-ACL case. Every row maps to a blocker or gap in §5 — none of them are speculative.

| Layer | Test |
| --- | --- |
| Unit — frontmatter | the four §5.4 rows (newline, `key: value`, quote+hash, `None`) round-trip through `yaml.safe_load`; no reflow at 2000 chars; slug emitted as `name` |
| Unit — slug | free-form names → valid slugs; `--` collapse; edge hyphens; 64-char cap; empty-slug skip; **stability** — a new colliding skill shifts no existing slug (§5.6) |
| Unit — versioning | three `create`s of one name yield exactly one index entry, at `name_cache.get_head(name)`; a forked chain still yields one (§5.5) |
| Unit — determinism | same inputs → identical bytes and digest on repeated calls (the §5.1 regression) |
| Unit — layout | archive has root `SKILL.md`; no `<skill-name>/` prefix; no absolute paths, `..`, backslashes, drive letters, or symlink entries |
| Unit — unsafe paths | a skill with a `file:../../x` tag is excluded from the index, with a WARNING naming the skill and the tag (§5.7) |
| Unit — index | validates against every rule in §1.3, including the ≤1024 description and the `sha256:` regex |
| Unit — content type | the index is `application/json`; a missing skill 404s as JSON, never `text/html` (§5.3 #11) |
| Integration | `TestClient`: index → parse → fetch each artifact → recompute digest → assert equality (this is precisely what the CLI does) |
| Integration — ACL | with `mode: standalone` and no credentials, the well-known paths return 200 while `GET /skills/` still returns 401 |
| Integration — scoping | namespace index contains only that namespace; unknown namespace 404s rather than falling back to the root index |
| Integration — publish gate | `state: new` skills absent by default; present when `SBS_WELLKNOWN_STATES` includes them |
| Cache | mutating a skill (or one of its tools/snippets) changes its digest; touching nothing does not; a `None` `modified_at` rebuilds every call; the LRU bound evicts rather than growing without limit (§5.8) |
| End-to-end (manual, gated) | `npx skills add http://localhost:8000 --list`, then a real install into `.claude/skills/`, then edit a skill and confirm `npx skills update` re-downloads only it. Run with `DISABLE_TELEMETRY=1`. |

The end-to-end step needs network + Node ≥ 22 and should stay out of the default `make test`.

---

## 8. Recommended sequence

| Phase | Scope | Plan step | Deliverable |
| --- | --- | --- | --- |
| 0 | YAML-safe frontmatter | §6.0 | fixes a live defect in `export-anthropic` and vNFS output, independent of npx |
| 1 | Deterministic, root-relative archive builder + path sanitisation | §6.1, §6.2 | blockers A and B fixed independently of any endpoint |
| 2 | Slug mapping + cached entry builder | §6.3 | pure functions, fully testable |
| 3 | `/pub/{ref}` endpoints + `GET /pub/*` allowlist + `SBS_PUBLIC_URL` | §6.4, §5.10 #2 | `npx skills add http://localhost:8000/pub/<slug>` works with ACL disabled |
| 4 | **Scoped derived tokens (one HMAC helper) + `_npx_install` field** | §4.3.3, §4.3.7, §4.3.8 | works under `mode: standalone`; no new endpoints, no token table, re-authorized per request |
| 5 | Standalone-ACL verification against the `skillberry` demo config, incl. the set-equality test | §6.5, §6.6 | proven on a deployment with auth switched on |
| 6 | **UI agent picker + copy-command button** | §4.3.1, §4.8 | one copy-paste install; no flags, no 50-item checklist, no agent sprawl |
| 7 | Namespace scoping (the "pack" analogue) | §6.7 | `npx skills add http://host/…/ns/data-eng` |
| 8 | Docs (`docs/cli.md`, README, operator notes incl. telemetry) | §4.8 | the feature becomes discoverable |
| 9 | *(stretch)* `SKILLS_API_URL`-compatible `/api/search` | — | `npx skills find` against a private store; low value until 1–8 land |

Phases 0–5 are the whole capability. **Phase 6 is not cosmetic** — §4.3.1 shows that without a generated command the
default experience is a 50-item checklist or ~75 stray agent directories, so the UI button is what makes the feature
usable rather than merely present. Phase 5 is what distinguishes "works on my laptop with ACL disabled" from "works on
the demo deployment".

---

## 9. Open questions

1. ~~**Scoped-path shape.**~~ **Resolved (§6.4, §4.3.9):** a single `/pub/{ref}` prefix carries every scope — `{ref}` is a per-skill token, a namespace token, or a slug — so one `GET /pub/*` allowlist entry covers all of them and the middle-wildcard limitation never bites. Namespaces are supported (§4.3.9), as is the global `*` surface. The forward-looking extension — namespace scopes inside role bindings, making a tenant's surface the union of its allowed namespaces — needs no URL change, but does impose the §4.3.9 constraint: compute a multi-entry index by filtering through the authorization model, never by assuming today's binary visibility.
2. ~~**Default publish gate.**~~ **Resolved (§4.3.1):** no state or tag gate. Any such filter is a second visibility rule the RBAC model does not express, and it breaks the guarantee that npx installs exactly what the user can see. Curation is done with namespaces, which the user can see.
3. ~~**Publish scope when ACL is on.**~~ **Resolved (§5.12):** opt-in, off by default, declared as `npx_publish` in the access-control config file rather than an env var — it is an access-control decision and belongs where the rest of them are reviewed.
4. ~~**One archive builder or two?**~~ **Resolved (§5.13): one.** `export-anthropic` moves onto the deterministic builder. The path *layout* stays a caller choice — the download keeps its `<skill-name>/` container, the artifact is root-relative — so one builder means one zip function, not one output.
5. ~~**Digest strategy.**~~ **Resolved (§5.14):** ship the stable URL. The artifact handler honours an optional `?digest=` parameter from day one, so closing the index/artifact race later is a one-line index change rather than a redesign. Full content-addressing is reserved for a CDN requirement that does not exist yet.
6. ~~**Is a per-skill bearer capability in a URL acceptable?**~~ **Closed — the model is as you describe it.** The install URL embeds a token; the token is per-skill; it can only be used to fetch that one skill; and that is precisely what compensates for npx being unable to authenticate. Nothing remains to decide. One property is worth keeping visible in the operator notes rather than as an open question: the capability is **durable**, so it also returns *future edits* to that skill until the secret is rotated — not only the version that was installed. Where the skill's content is committed beside the lockfile this is immaterial (§4.3.7); it matters only if a published skill later gains sensitive content. npx cannot authenticate, so per-user enforcement is impossible — anyone holding the install URL can read the store's skills until the token is revoked. Options A (public index), B (no npx on secured stores) and the publish token are laid out with their trade-offs; which is right depends on whether the store is internet-facing and on how the organisation treats URL-borne secrets. **Recommend deciding this before phase 4**, since it determines whether the token store gets built at all.
7. ~~**Telemetry stance.**~~ **Resolved: document the opt-out; do not prefix the emitted command.** The snippets SBS publishes carry no `DISABLE_TELEMETRY=1`, and `DO_NOT_TRACK=1` — the cross-vendor convention, honoured here alongside it — is documented as a shell-profile export instead. Three reasons, in ascending order of force:

   - **It protects one invocation.** `npx skills update` is typed by the user and replayed indefinitely from `skills-lock.json`; a prefix on the single command we hand out never reaches those runs. An opt-out only means something if it lives in the environment, which is also where a prefix teaches nobody to put it.
   - **It is not portable.** `VAR=1 cmd` is POSIX shell syntax, so a prefixed command *fails outright* in PowerShell and `cmd.exe` — the copy button would emit something broken for every Windows user. This is a correctness defect, not a preference.
   - **It is not ours to decide silently.** Suppressing by default removes every SBS install from the ecosystem's install counts, which is a choice to make in the open rather than by default.

   The residual exposure is real and stated rather than mitigated: on a secured store the default reports `installUrl`, token included. The place that fact belongs is the UI, next to the copy button, at the moment a reader is about to paste a credential-bearing URL — which §4.3.2 already reserved for the token warning. Prose there beats shell syntax in a command they skim past.

   Two shapes were considered and rejected. Emitting the prefix only under `mode: standalone` keeps the Windows defect on exactly the deployments that can least afford a broken command. And a server-side knob (`npx_telemetry_opt_out` in the ACL config) would be *advisory only* — it changes a suggested string and cannot affect a hand-typed command, an edited one, or a later `update` — so placing it beside `unauthenticated_paths` and `npx_publish`, where every entry is enforced, would mislead whoever reviews that file's security posture.

---

## 10. Sources

- `skills@1.6.0` from npm (line numbers throughout refer to this version; **`1.7.0`, checked 2026-09-22, has a byte-identical `WellKnownProvider` region — the protocol has not drifted**, though it should be re-verified at implementation time) — `package.json`, `README.md`, and `dist/cli.mjs` (the bundle is the only complete specification of the protocol; `WellKnownProvider` and its validators were read directly).
- `https://skills.sh/docs`, `/docs/cli`, `/docs/packs`, `/docs/api` — retrieved 2026-09-17.
- Live probes: `skills.sh/api/search`, `skills.sh/api/download/...`, `skills.sh/.well-known/{agent-skills,skills}/index.json`, `skills.sh/p/<bogus-id>[/.well-known/...]` (§1.7.1), and `skillberry-store-demo-adv.onrender.com` (`/health`, `/.well-known/agent-skills/index.json`, `/skills/`).
- Local determinism experiment reproducing §5.1.
- [github.com/vercel-labs/skills](https://github.com/vercel-labs/skills) — MIT. No written spec for the well-known protocol exists in the repo; `src/providers/wellknown.ts` is the reference implementation.
