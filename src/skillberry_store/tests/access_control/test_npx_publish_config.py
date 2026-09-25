"""The ``npx_publish`` switch and the ``/pub/*`` allow-list entry.

docs/design/npx.md §5.12 and §6.4. The switch lives in the access-control config
rather than an env var because it *is* an access-control decision: it is the one
setting that makes skill content reachable without a session, so it belongs
beside ``unauthenticated_paths`` where the rest of them are reviewed.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from skillberry_store.access_control.config import (
    _DEFAULT_UNAUTH_PATHS,
    NPX_PUBLISH_ALL,
    NPX_PUBLISH_NONE,
    NPX_PUBLISH_SELECTIVE,
    load_config,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _write(tmp_path, contents: str) -> str:
    path = tmp_path / "acl.yaml"
    path.write_text(textwrap.dedent(contents))
    return str(path)


def test_default_in_code_is_selective():
    """The default is per-skill opt-in, not a blanket on or off.

    Conservative in effect — a skill that has set no flag is not published — but
    it makes publishing a per-skill decision rather than an all-or-nothing one.
    """
    assert load_config("/nonexistent/acl.yaml").npx_publish == NPX_PUBLISH_SELECTIVE


def test_an_older_config_file_keeps_loading(tmp_path):
    """Purely additive: the loader reads known keys and ignores unknown ones.

    Such a file resolves to `selective`, which publishes nothing until a skill is
    opted in — so it stays as closed as it was before the setting existed.
    """
    cfg = load_config(_write(tmp_path, "mode: disabled\n"))
    assert cfg.npx_publish == NPX_PUBLISH_SELECTIVE


@pytest.mark.parametrize("value", ["true", "yes", "on", '"1"'])
def test_truthy_spellings_publish_everything(tmp_path, value):
    """YAML parses bare `true` as a bool; the quoted spellings normalise too."""
    cfg = load_config(_write(tmp_path, f"mode: disabled\nnpx_publish: {value}\n"))
    assert cfg.npx_publish == NPX_PUBLISH_ALL


@pytest.mark.parametrize("value", ["false", "no", "off"])
def test_falsy_spellings_publish_nothing(tmp_path, value):
    cfg = load_config(_write(tmp_path, f"mode: disabled\nnpx_publish: {value}\n"))
    assert cfg.npx_publish == NPX_PUBLISH_NONE


@pytest.mark.parametrize("value", ["selective", "SELECTIVE", " selective "])
def test_selective_is_accepted_case_and_space_insensitively(tmp_path, value):
    cfg = load_config(_write(tmp_path, f"mode: disabled\nnpx_publish: {value}\n"))
    assert cfg.npx_publish == NPX_PUBLISH_SELECTIVE


def test_an_explicit_null_resolves_to_the_default(tmp_path):
    """`npx_publish: null` is "I did not set this", same as omitting the key."""
    cfg = load_config(_write(tmp_path, "mode: disabled\nnpx_publish: null\n"))
    assert cfg.npx_publish == NPX_PUBLISH_SELECTIVE


def test_a_junk_value_fails_closed_with_a_warning(tmp_path, caplog):
    """Closed, not defaulted: a typo should publish nothing rather than land the
    operator in a mode they did not choose."""
    with caplog.at_level(logging.WARNING, logger="skillberry_store.access_control.config"):
        cfg = load_config(_write(tmp_path, "mode: disabled\nnpx_publish: maybe\n"))
    assert cfg.npx_publish == NPX_PUBLISH_NONE
    assert "npx_publish" in caplog.text


@pytest.mark.parametrize("value", ["true", "false", "selective"])
@pytest.mark.parametrize("mode", ["disabled"])
def test_every_value_is_legal_at_any_acl_mode(tmp_path, value, mode):
    """Independent of `mode` by design — a mode-dependent default is exactly the
    subtlety that surprises someone who later enables auth. (The `standalone`
    half needs users/roles/bindings and lives in test_publish_api.py.)"""
    cfg = load_config(_write(tmp_path, f"mode: {mode}\nnpx_publish: {value}\n"))
    assert (cfg.mode, cfg.npx_publish) == (mode, value)


def test_pub_prefix_is_in_the_built_in_allowlist():
    assert "GET /pub/*" in _DEFAULT_UNAUTH_PATHS


def test_the_allowlist_entry_matches_the_index_and_the_artifact():
    """One glob covers both routes, because both live under /pub/ (§6.4).

    It necessarily covers every other path under the prefix too, since
    ``_path_matches`` supports only a trailing ``*`` — which is exactly why
    ``/pub/`` must stay a dedicated namespace with nothing else mounted under it
    (§5.10 #1).
    """
    cfg = load_config("/nonexistent/acl.yaml")
    for path in (
        "/pub/TOKEN/.well-known/agent-skills/index.json",
        "/pub/TOKEN/.well-known/agent-skills/pdf-forms.zip",
        "/pub/pdf-forms/.well-known/agent-skills/index.json",
    ):
        assert cfg.is_unauthenticated("GET", path), path


def test_the_allowlist_entry_does_not_widen_anything_else():
    cfg = load_config("/nonexistent/acl.yaml")
    assert not cfg.is_unauthenticated("GET", "/skills/")
    assert not cfg.is_unauthenticated("GET", "/publish/x")
    # Only GET: a write method under the same prefix is not allow-listed.
    assert not cfg.is_unauthenticated("POST", "/pub/TOKEN/anything")
    assert not cfg.is_unauthenticated("DELETE", "/pub/TOKEN/anything")


# --------------------------------------------------------------------------- #
# The shipped files (the §6.4 "three files, same commit" requirement)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "filename,expected",
    [
        # mode: disabled — nothing is protected there anyway, so a bare dev
        # checkout is usable out of the box.
        ("access_control_config.yaml", NPX_PUBLISH_ALL),
        # The demo ships `true` so every seeded skill is installable out of the
        # box; a deployment wanting npx for only some skills uses `selective`.
        ("access_control_config.yaml.standalone", NPX_PUBLISH_ALL),
    ],
)
def test_shipped_configs_declare_the_switch(filename, expected):
    cfg = load_config(str(REPO_ROOT / filename))
    assert cfg.npx_publish == expected


@pytest.mark.parametrize(
    "filename",
    [
        "access_control_config.yaml",
        "access_control_config.yaml.standalone",
        "access_control_config.yaml.disabled",
    ],
)
def test_shipped_configs_document_all_three_values(filename):
    """The comment above the setting is where an operator learns the options, so
    a fourth value added in code without documenting it fails here."""
    text = (REPO_ROOT / filename).read_text()
    header = text.split("npx_publish:")[0]
    for value in (NPX_PUBLISH_ALL, NPX_PUBLISH_NONE, NPX_PUBLISH_SELECTIVE):
        assert value in header, (filename, value)


@pytest.mark.parametrize(
    "filename",
    ["access_control_config.yaml", "access_control_config.yaml.standalone"],
)
def test_shipped_configs_allowlist_the_pub_prefix(filename):
    """Present unconditionally: harmless when the routes are absent, and it
    makes enabling the feature a one-line edit rather than two (§5.12)."""
    cfg = load_config(str(REPO_ROOT / filename))
    assert "GET /pub/*" in cfg.unauthenticated_paths
    assert cfg.is_unauthenticated(
        "GET", "/pub/TOKEN/.well-known/agent-skills/index.json"
    )
