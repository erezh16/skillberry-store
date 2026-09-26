# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""The mandatory unauthenticated floor — docs/design/new_cli.md §5.5.3, §8.2 #15.

An operator's ``unauthenticated_paths`` *replaces* the built-in defaults rather
than extending them. That is long-standing behaviour and stays as it is, but it
has two consequences this floor fixes:

1. The CLI download surface has to be unauthenticated "independently of ACL
   mode". Adding it to ``_DEFAULT_UNAUTH_PATHS`` would not achieve that — any
   operator with their own list (as ``access_control_config.yaml.standalone``
   ships with) would still authenticate ``/cli/*``, and a browser, ``curl``, CI
   or a freshly downloaded binary has no token to offer.

2. It is **already broken** for such operators, independently of this feature:
   they silently lost ``/health``, ``/health/ready``, ``/openapi.json`` and
   ``/docs`` unless they happened to re-list them, so liveness probes started
   demanding a bearer token with nothing in the log to explain it.

The floor is merged in regardless of the config file, and anything it had to add
is named in the boot log so the widening is never silent.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from skillberry_store.access_control.config import (
    _ALWAYS_UNAUTH_PATHS,
    _DEFAULT_UNAUTH_PATHS,
    _merge_unauth_paths,
    load_config,
)

REPO_ROOT_YAMLS = (
    "access_control_config.yaml",
    "access_control_config.yaml.standalone",
    "access_control_config.yaml.disabled",
)

# What the floor must cover, whatever the operator wrote.
FLOOR_MUST_REACH = (
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/whoami"),
    ("GET", "/cli/manifest"),
    ("GET", "/cli/download"),
    ("HEAD", "/cli/download"),
    ("GET", "/cli/install.sh"),
    ("GET", "/cli/install.ps1"),
    ("GET", "/cli/license"),
)


def _write(tmp_path, contents: str) -> str:
    path = tmp_path / "acl.yaml"
    path.write_text(textwrap.dedent(contents))
    return str(path)


def _is_unauth(cfg, method: str, path: str) -> bool:
    """Whether the PEP would let this request through without a session.

    Goes through the config's own ``is_unauthenticated`` — the method the PEP
    actually calls — rather than inspecting ``unauthenticated_paths`` directly.
    The entries use a ``METHOD /path*`` syntax with trailing-wildcard semantics,
    so a substring assertion over the list would happily pass for an entry that
    never matches a real request.
    """
    return cfg.is_unauthenticated(method, path)


# --------------------------------------------------------------------------- #
# The merge helper
# --------------------------------------------------------------------------- #


def test_merge_adds_only_what_is_missing():
    merged, added = _merge_unauth_paths(["A", "B", "C"], ["B", "Z"])
    # Configured entries first, so the file still reads as the primary
    # description of the public surface.
    assert merged == ["B", "Z", "A", "C"]
    assert added == ["A", "C"]


def test_merge_reports_nothing_when_already_covered():
    merged, added = _merge_unauth_paths(["A"], ["A", "B"])
    assert merged == ["A", "B"]
    assert added == []


def test_merge_does_not_duplicate():
    merged, _ = _merge_unauth_paths(["A", "A"], ["A"])
    assert merged.count("A") == 1


# --------------------------------------------------------------------------- #
# §8.2 #15: an operator config that omits everything
# --------------------------------------------------------------------------- #


def test_minimal_operator_list_still_reaches_the_floor(tmp_path, monkeypatch):
    """The headline case: a config that lists something else entirely.

    Without the floor this deployment would authenticate its own liveness probe
    and its own login endpoint — an unbootable configuration that nothing warned
    about — as well as the CLI download that has no token to offer.
    """
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    path = _write(
        tmp_path,
        """
        mode: standalone
        unauthenticated_paths:
          - GET /something-unrelated
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$hash"
        """,
    )
    cfg = load_config(path)
    assert cfg.mode == "standalone"

    for method, target in FLOOR_MUST_REACH:
        assert _is_unauth(cfg, method, target), (
            f"{method} {target} is not reachable without a session despite the "
            f"mandatory floor"
        )


def test_an_explicitly_empty_list_falls_back_to_the_defaults(tmp_path, monkeypatch):
    """Pre-existing behaviour, documented because it is surprising.

    ``unauthenticated_paths: []`` is falsy, so the loader treats it as "not
    specified" and applies the built-in defaults rather than an empty allow-list.
    An operator who writes ``[]`` intending "authenticate everything" does not get
    that.

    Left as it is deliberately: the alternative — honouring ``[]`` literally —
    would make a deployment that authenticates its own ``/auth/login`` reachable
    by a one-character edit, and the floor exists precisely to stop that class of
    self-lockout. Asserted here so the behaviour is a decision rather than an
    accident, and so a future change to it is visible.
    """
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    path = _write(
        tmp_path,
        """
        mode: standalone
        unauthenticated_paths: []
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$hash"
        """,
    )
    cfg = load_config(path)
    # The defaults, not an empty list: /docs is in the defaults but not the floor.
    assert _is_unauth(cfg, "GET", "/docs")
    for method, target in FLOOR_MUST_REACH:
        assert _is_unauth(cfg, method, target), f"{method} {target}"


def test_operator_list_omitting_cli_still_serves_cli(tmp_path, monkeypatch):
    """A realistic config: health is listed, /cli is not (it is new)."""
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    path = _write(
        tmp_path,
        """
        mode: standalone
        unauthenticated_paths:
          - GET /health
          - POST /auth/login
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$hash"
        """,
    )
    cfg = load_config(path)
    assert _is_unauth(cfg, "GET", "/cli/manifest")
    assert _is_unauth(cfg, "GET", "/cli/download")
    assert _is_unauth(cfg, "HEAD", "/cli/download")


def test_the_floor_widening_is_logged(tmp_path, monkeypatch, caplog):
    """Never silent: an operator reading their own file must learn it is not all.

    A permanent hole an operator cannot close has to be discoverable from the
    boot log, or the config file becomes actively misleading about the public
    surface.
    """
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    path = _write(
        tmp_path,
        """
        mode: standalone
        unauthenticated_paths:
          - GET /something-unrelated
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$hash"
        """,
    )
    with caplog.at_level("INFO"):
        load_config(path)

    assert "unauthenticated floor" in caplog.text
    # The specific patterns, so the log answers "what exactly was opened?"
    assert "GET /cli*" in caplog.text
    assert "GET /health" in caplog.text
    # And it names the file, so the reader knows which config understates things.
    assert "acl.yaml" in caplog.text


def test_no_log_noise_when_the_config_already_covers_the_floor(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    # Written without textwrap.dedent: interpolating a generated block into a
    # dedented triple-quoted string mixes indentation levels and yields invalid
    # YAML.
    lines = ["mode: disabled", "unauthenticated_paths:"]
    lines += [f"  - {entry}" for entry in _ALWAYS_UNAUTH_PATHS]
    path = tmp_path / "acl.yaml"
    path.write_text("\n".join(lines) + "\n")

    with caplog.at_level("INFO"):
        load_config(str(path))
    assert "unauthenticated floor" not in caplog.text


def test_floor_applies_in_disabled_mode_too(tmp_path):
    path = _write(
        tmp_path,
        """
        mode: disabled
        unauthenticated_paths: []
        """,
    )
    cfg = load_config(path)
    assert _is_unauth(cfg, "GET", "/cli/manifest")


def test_floor_applies_with_no_config_file_at_all(tmp_path):
    """The defaults path must not be able to drift from the with-config path."""
    cfg = load_config(str(tmp_path / "does-not-exist.yaml"))
    assert cfg.mode == "disabled"
    for method, target in FLOOR_MUST_REACH:
        assert _is_unauth(cfg, method, target), f"{method} {target}"


def test_operator_entries_are_preserved(tmp_path, monkeypatch):
    """Merging must not discard what the operator asked for."""
    monkeypatch.delenv("SBS_SESSION_TTL", raising=False)
    path = _write(
        tmp_path,
        """
        mode: standalone
        unauthenticated_paths:
          - GET /my-custom-public-thing
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$hash"
        """,
    )
    cfg = load_config(path)
    assert _is_unauth(cfg, "GET", "/my-custom-public-thing")


# --------------------------------------------------------------------------- #
# The floor's own contents
# --------------------------------------------------------------------------- #


def test_floor_is_minimal_and_justified():
    """Every entry is a hole an operator cannot close, so the list stays short.

    This test exists to make *adding* to the floor a deliberate act: a new entry
    means a new permanently-public path on every deployment in every mode.
    """
    assert set(_ALWAYS_UNAUTH_PATHS) == {
        "GET /health",
        "GET /health/ready",
        "POST /auth/login",
        "POST /auth/logout",
        "GET /auth/whoami",
        "GET /cli*",
        "HEAD /cli*",
    }, (
        "The mandatory unauthenticated floor changed. Every entry is a path no "
        "operator can protect, so adding one needs a design note — see "
        "docs/design/new_cli.md §5.5.3."
    )


def test_floor_does_not_expose_content_or_admin_paths():
    """The floor must never reach tenant data or administration."""
    for pattern in _ALWAYS_UNAUTH_PATHS:
        for dangerous in (
            "/skills",
            "/tools",
            "/snippets",
            "/admin",
            "/plugins",
            "/vmcp",
            "/vnfs",
        ):
            assert dangerous not in pattern, (
                f"the floor entry {pattern!r} would expose {dangerous}"
            )


def test_cli_floor_does_not_leak_to_a_sibling_prefix():
    """`GET /cli*` is a prefix match, so check it cannot open something else.

    A trailing `*` matches anything after `/cli`, so a future route mounted at
    e.g. `/client-secrets` would be safe (different prefix) but `/clients` would
    NOT be — it starts with `/cli`. Recorded here so the constraint is visible
    before someone adds such a route.
    """
    cfg = load_config(str("does-not-exist.yaml"))
    # The intended surface.
    assert _is_unauth(cfg, "GET", "/cli/manifest")
    # The trap: anything beginning with /cli is opened by this pattern.
    assert _is_unauth(cfg, "GET", "/clients"), (
        "This documents a real constraint rather than desired behaviour: "
        "`GET /cli*` opens any path starting with `/cli`. Never mount a "
        "protected route under a name beginning with `cli`."
    )
    # A clearly separate prefix stays protected.
    assert not _is_unauth(cfg, "GET", "/skills")


# --------------------------------------------------------------------------- #
# The shipped YAMLs stay a complete description of the public surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("filename", REPO_ROOT_YAMLS)
def test_shipped_yaml_lists_the_cli_paths(filename):
    """§5.5.3: the file should still describe the whole public surface.

    The floor means omitting them does not close them — which is exactly why the
    file must list them, or it understates what the deployment exposes.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / filename
    if not path.is_file():
        pytest.skip(f"{filename} not present in this checkout")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    listed = data.get("unauthenticated_paths") or []
    assert "GET /cli*" in listed, f"{filename} does not list GET /cli*"
    # HEAD because /cli/download serves both, and the RBAC audit requires every
    # method on a route to be allow-listed.
    assert "HEAD /cli*" in listed, f"{filename} does not list HEAD /cli*"


@pytest.mark.parametrize("filename", REPO_ROOT_YAMLS)
def test_shipped_yaml_covers_the_whole_floor(filename):
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / filename
    if not path.is_file():
        pytest.skip(f"{filename} not present in this checkout")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    listed = set(data.get("unauthenticated_paths") or [])
    missing = [entry for entry in _ALWAYS_UNAUTH_PATHS if entry not in listed]
    assert not missing, (
        f"{filename} omits floor entries {missing}. They are still reachable "
        f"(the floor merges them in), but the file then understates the public "
        f"surface — which is what makes an operator's own list misleading."
    )


def test_defaults_also_cover_the_floor():
    """Belt and braces: the built-in default list should need no widening."""
    missing = [p for p in _ALWAYS_UNAUTH_PATHS if p not in _DEFAULT_UNAUTH_PATHS]
    assert not missing, (
        f"_DEFAULT_UNAUTH_PATHS omits {missing}; a default-config deployment "
        f"would log a floor widening on every boot for no reason."
    )
