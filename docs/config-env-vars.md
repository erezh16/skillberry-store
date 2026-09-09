# Overall Configuration Variable Overrides

This table lists the default ports, host URLs and overall service configuration used by SBS service, along with the environment variables that can be used to override them.

| Configuration            | Default value | Environment Variables Override   | Notes
|--------------------------|---------------|----------------------------------|-------------------------------------------------------------|
| FastAPI Service host     | "0.0.0.0"     | `SBS_HOST`                       |                                                             |
| FastAPI Service port     | 8000          | `SBS_PORT`                       |                                                             |
| UI port (legacy subprocess) | 8002       | `SBS_UI_PORT`                    | Only the legacy `ENABLE_UI_SUBPROCESS=true` mode, which spawns `vite preview`. The UI is normally served by the backend itself at `/ui` on `SBS_PORT` |
| Vite dev server port     | 8002          | `VITE_UI_PORT`                   | `make ui-dev` only (hot-reloading dev server, proxies API calls to `SBS_PORT`)  |
| Prometheus metric port   | 8090          | `PROMETHEUS_METRICS_PORT`        | SBS prometheus endpoint (used for scraping metrics)         |
| Open telemetric port     | None          | `OTEL_TRACES_PORT`               | Must be set for OpenTelemetry tracing to work               |
| Observability enablement | True          | `OBSERVABILITY`                  | If False - disable observability (telemetry and prometheus) |
| Python execution mode    | False         | `EXECUTE_PYTHON_LOCALLY`         | If True - use local exec() instead of Docker                |
| Auto-detect dependencies | True          | `AUTO_DETECT_TOOL_DEPENDENCIES`  | If False - disable automatic tool dependency detection      |

> You can override the default values by setting the corresponding environment variables in your deployment configuration.


This table lists persistency configuration used by SBS service, along with the environment variables that can be used to override them.

| Configuration                      | Default value                                  | Environment Variables Override          | Notes
|------------------------------------|------------------------------------------------|-----------------------------------------|-------------------------------------|
| Base directory                     | {system_temp}/skillberry-store                 | `SBS_BASE_DIR`                          | Parent directory for all SBS storage |
| Files folder (tools blob)          | {SBS_BASE_DIR}/files                           | `SBS_DIRECTORY_PATH`                    | Stores tool blobs (e.g. tools code) |
| Tools directory                    | {SBS_BASE_DIR}/tools                           | `SBS_TOOLS_DIRECTORY`                   | Stores tool files                   |
| Tools descriptions folder          | {SBS_BASE_DIR}/tools_descriptions              | `SBS_TOOLS_DESCRIPTIONS_DIRECTORY`      | Stores tool embeddings information  |
| Snippets directory                 | {SBS_BASE_DIR}/snippets                        | `SBS_SNIPPETS_DIRECTORY`                | Stores snippet files                |
| Snippets descriptions folder       | {SBS_BASE_DIR}/snippets_descriptions           | `SBS_SNIPPETS_DESCRIPTIONS_DIRECTORY`   | Stores snippet embeddings           |
| Skills directory                   | {SBS_BASE_DIR}/skills                          | `SBS_SKILLS_DIRECTORY`                  | Stores skill files                  |
| Skills descriptions folder         | {SBS_BASE_DIR}/skills_descriptions             | `SBS_SKILLS_DESCRIPTIONS_DIRECTORY`     | Stores skill embeddings             |
| Metadata directory                 | {SBS_BASE_DIR}/metadata                        | `SBS_METADATA_DIRECTORY`                | Stores metadata files               |
| VMCP directory                     | {SBS_BASE_DIR}/vmcp                            | `SBS_VMCP_DIRECTORY`                    | Stores virtual MCP server files     |
| VMCP descriptions folder           | {SBS_BASE_DIR}/vmcp_descriptions               | `SBS_VMCP_DESCRIPTIONS_DIRECTORY`       | Stores VMCP embeddings              |
| Virtual MCP servers list           | {system_temp}/skillberry-store/vmcp_servers.json | `VMCP_SERVERS_FILE`                   | Stores virtual MCP servers list     |

> **Note:** `{system_temp}` refers to the operating system's temporary directory.
> All directory paths default to subdirectories under `SBS_BASE_DIR`. You can override individual directories or set `SBS_BASE_DIR` to change the base location for all directories.


This table lists embedding configuration used by SBS service, along with the environment variables that can be used to override them.

| Configuration     | Default value                                   | Environment Variables Override | Notes |
|-------------------|-------------------------------------------------|--------------------------------|-------|
| Vector database   | "faiss"                                         | `SBS_VDB`                      |       |
| Model dimension   | 384                                             | `EMBEDDING_MODEL_DIMENSION`    |       |
| Model search k    | 5                                               | `EMBEDDING_MODEL_SEARCH_K`     |       |
| Model cache dir   | `$APP_HOME/.cache/fastembed` in the container, else `$XDG_CACHE_HOME/fastembed` or `~/.cache/fastembed` | `SBS_ENCODER_CACHE_DIR` | Where the ~80 MB ONNX encoder weights are cached. The image ships them pre-seeded so startup needs no HuggingFace access; fastembed ignores `HF_HOME` / `TRANSFORMERS_CACHE` / `XDG_CACHE_HOME`, so this is the only knob |
| Max sequence length | 256                                           | `SBS_ENCODER_MAX_LENGTH`       | Tokens before input is truncated. 256 matches `SentenceTransformer('all-MiniLM-L6-v2')`, so vectors stay comparable with indices built before the fastembed migration. Override only to match an index already built at a different limit (fastembed's own default for these weights is 128) |

> You can override the default values by setting the corresponding environment variables in your deployment configuration.


This table lists MCP (Model Context Protocol) configuration used by SBS service, along with the environment variables that can be used to override them.

| Configuration          | Default value                  | Environment Variables Override | Notes                                    |
|------------------------|--------------------------------|--------------------------------|------------------------------------------|
| MCP server URL         | "http://localhost:8080/sse"    | `MCP_SERVER_URL`               | MCP server URL for SSE connections       |
| VMCP servers start port| 10000                          | `VMCP_SERVERS_START_PORT`      | Starting port for virtual MCP servers    |

> You can override the default values by setting the corresponding environment variables in your deployment configuration.


This table lists test configuration used by SBS service, along with the environment variables that can be used to override them.

| Configuration   | Default value | Environment Variables Override | Notes                                    |
|-----------------|---------------|--------------------------------|------------------------------------------|
| Test debug mode | False         | `SBS_TEST_DEBUG`               | If True - enable debug logging for tests |

> You can override the default values by setting the corresponding environment variables in your deployment configuration.

### Example: Running tests with debug logs enabled

To enable debug logging when running tests with make:

```bash
SBS_TEST_DEBUG=true make test
```

## Skill-import authentication

Importing skills from remote sources (currently GitHub) can require auth. This
is the **only** thing configured via a file — everything else uses env vars.

The file uses the same shape as `gh`'s `~/.config/gh/hosts.yml`: a mapping keyed
by hostname.

| Configuration | Default value             | Environment Variable Override | Notes                                              |
|---------------|---------------------------|-------------------------------|----------------------------------------------------|
| Import auth   | `./import_auth_config.yaml` | `SBS_IMPORT_AUTH_CONFIG`    | Loaded once at startup; missing/empty => anonymous |

```yaml
github.com:
  user: eranra
  oauth_token: gho_xxxxxxxx
  git_protocol: https
```

By default imports are **anonymous** (public sources need no config). For a
non-anonymous import, the host matching the fetched URL is resolved in order:

1. `oauth_token` — sent as `Authorization: Bearer <token>`.
2. `login_url` — forced re-auth: the API returns `401` + this URL; the user logs
   in to get a token, then retries sending header `X-Endpoint-Token: <token>`.
3. Neither — fall back to the `gh` CLI token in `~/.config/gh/hosts.yml`, if
   present (when `gh` stores it in the OS keyring it is not in the file, so this
   falls through to anonymous).

## Setting these variables in a container

Every variable in the tables above can be given a value that is **baked into the
image**, so it is set under a plain `docker run <image>` with no `--env-file`
flag, no `make` in the loop, and no Dockerfile edit.

Put it in [`container.env`](../container.env) at the repo root:

```sh
# container.env
SBS_BASE_DIR=/app/store-data
SBS_VDB=faiss
OBSERVABILITY=false
```

Then build and run normally:

```sh
make docker-build
docker run -d --network=host ghcr.io/skillberry-ai/skillberry-store:latest
```

`make docker-build` copies the file to `/app/.env` inside the image (through the
`EXTRA_COPY_FILES` hook — any pair you pass on the command line is kept as
well), and the service loads it at startup with python-dotenv. Build against a
different file with `make docker-build CONTAINER_ENV_FILE=prod.env`; if the file
is absent the build simply skips it.

### Precedence

Highest wins:

1. `docker run -e VAR=…` / `docker run --env-file …`, and Kubernetes `env:` /
   `envFrom:` — per-deployment overrides.
2. The image's own Dockerfile `ENV` (`APP_HOME`, `APP_DATA_DIR`, …).
3. `container.env`, baked to `/app/.env` — deployment defaults.
4. The default in the tables above, compiled into the code.

So `container.env` behaves like a Dockerfile `ENV` would: it supplies a default
and never fights a value the deployment sets explicitly. `make docker-run`
additionally passes the host's own (git-ignored) `.env` with `--env-file`, which
therefore overrides `container.env` — use the host `.env` for local, throwaway
values and `container.env` for what should ship with the image.

> **Never put secrets in `container.env`.** The values ride inside the image, so
> anyone who can pull it can read them. Pass secrets at run time: `-e`, or a k8s
> Secret via `envFrom:`.

### Kubernetes and OpenShift

Both are supported with no extra work:

- **`env:` / `envFrom:` still override**, per the precedence above, so a
  ConfigMap or Secret behaves exactly as it would against a Dockerfile `ENV`.
- **Arbitrary UIDs (OpenShift) work.** The file is staged with the group given
  the owner's access and lands under `$APP_HOME`, which the image `chgrp 0`s and
  `chmod g=u`s — so the random UID OpenShift assigns (always in gid 0) can read
  it. Verified by running the image as `--user 1000670000:0`.
- **A `command:` override still gets the variables**, because the loading
  happens inside the application rather than in an entrypoint script — unlike an
  `ENTRYPOINT` wrapper, which `command:` replaces.
- **`readOnlyRootFilesystem: true` is fine** — the file is only ever read.

Two limits worth knowing: the variables reach the *application* process (and
anything it spawns), so an interactive `docker exec … sh` will not see them; and
a variable consumed at Python *import* time by a module imported before
`skillberry_store.tools.configure` would miss them. Use a Dockerfile `ENV` for
either case.
