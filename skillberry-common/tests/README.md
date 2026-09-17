# Tests for the build system

The build system's code artifacts — `scripts/`, `.mk/`, `Makefile` — are tested
from here rather than from the test tree of whichever project embeds this
subtree. A change to `git_state.py` or `docker.mk` and the test that pins its
behaviour travel together, in one repository and one PR.

Nothing here imports the embedding project's application code, and the scripts
are driven as scripts: `git_state.py` runs against throwaway repositories, and
`install-requirements.sh` / `docker-reconcile-stamp.sh` run with shims on `PATH`
standing in for `uv` and `docker`, so no real install or docker daemon is
involved.

`build_paths.py` names the two roots the tests resolve against:

* `COMMON_ROOT` — this repository. Always real.
* `PROJECT_ROOT` — the project that embeds this subtree at
  `<project>/skillberry-common`. Only there does make have a full rule database
  to resolve, and only there do project-owned makefiles such as `.mk/dev.mk`
  exist. Tests that read either carry `requires_project` and skip when this
  repository is checked out on its own.

The concepts these tests pin are written down in `docs/design/build_concepts.md`
of the embedding project.

## Running them

They are ordinary pytest modules, collected by the embedding project's `make
test`. To run only these:

    pytest skillberry-common/tests
