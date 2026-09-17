# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Exercises the two stamp-keeping scripts of the build system.

* ``install-requirements.sh`` owns the install-stamp bookkeeping of concept 6 in
  ``docs/design/build_concepts.md``: `uv pip install -e .[X]` replaces the
  previous set of extras, so a stamp left over from an earlier ODEPS would claim
  an environment that no longer exists.
* ``docker-reconcile-stamp.sh`` owns concept 3: a build stamp may only survive
  while an image at that tag actually exists, so `docker rmi` behind make's back
  cannot leave the build believing the image is there.

Both are driven with a shim on PATH standing in for `uv` / `docker`, so the real
tools are never invoked and the tests run anywhere.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "skillberry-common" / "scripts"
INSTALL_SH = SCRIPTS / "install-requirements.sh"
RECONCILE_SH = SCRIPTS / "docker-reconcile-stamp.sh"


def shim(bin_dir: Path, name: str, body: str) -> None:
    """Put an executable `name` on a directory that will be prepended to PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/usr/bin/env bash\n{body}\n")
    script.chmod(0o755)


def run(script: Path, *args: str, cwd: Path, bin_dir: Path | None = None):
    env = dict(os.environ)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def stamps(work: Path) -> set[str]:
    d = work / ".stamps"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


# ---------------------------------------------------------------------------
# Concept 6: install stamp bookkeeping
# ---------------------------------------------------------------------------


@pytest.fixture
def install_env(tmp_path):
    """A work dir plus a `uv` shim that records its arguments and succeeds."""
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    shim(bin_dir, "uv", f'echo "$@" >> "{work}/uv-calls"; exit 0')
    return work, bin_dir


def test_installing_extras_validates_both_stamps(install_env):
    """`.[dev]` also installs the base package, so the empty stamp is valid too."""
    work, bin_dir = install_env
    proc = run(INSTALL_SH, "dev", "", cwd=work, bin_dir=bin_dir)
    assert proc.returncode == 0, proc.stderr
    assert stamps(work) == {"install-requirements-", "install-requirements-dev"}


def test_installing_without_extras_validates_only_the_empty_stamp(install_env):
    work, bin_dir = install_env
    assert run(INSTALL_SH, "", "", cwd=work, bin_dir=bin_dir).returncode == 0
    assert stamps(work) == {"install-requirements-"}


def test_a_new_extras_set_invalidates_the_previous_one(install_env):
    """The environment reflects only the last install; an older stamp is a lie."""
    work, bin_dir = install_env
    run(INSTALL_SH, "test", "", cwd=work, bin_dir=bin_dir)
    assert "install-requirements-test" in stamps(work)

    run(INSTALL_SH, "dev", "", cwd=work, bin_dir=bin_dir)

    assert stamps(work) == {"install-requirements-", "install-requirements-dev"}, (
        "the test extras stamp survived a dev install, so `make test` would skip "
        "reinstalling dependencies that are no longer there"
    )


def test_a_base_install_invalidates_an_extras_stamp(install_env):
    work, bin_dir = install_env
    run(INSTALL_SH, "dev", "", cwd=work, bin_dir=bin_dir)
    run(INSTALL_SH, "", "", cwd=work, bin_dir=bin_dir)
    assert stamps(work) == {"install-requirements-"}


def test_a_failed_install_touches_nothing(tmp_path):
    """No stamp on failure, so the next invocation retries."""
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    shim(bin_dir, "uv", "exit 1")

    proc = run(INSTALL_SH, "dev", "", cwd=work, bin_dir=bin_dir)

    assert proc.returncode != 0
    assert stamps(work) == set()


def test_a_failed_install_does_not_keep_an_earlier_stamp(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    ok_bin, bad_bin = tmp_path / "ok", tmp_path / "bad"
    shim(ok_bin, "uv", "exit 0")
    shim(bad_bin, "uv", "exit 1")

    run(INSTALL_SH, "dev", "", cwd=work, bin_dir=ok_bin)
    proc = run(INSTALL_SH, "test", "", cwd=work, bin_dir=bad_bin)

    assert proc.returncode != 0
    assert "install-requirements-test" not in stamps(work), (
        "a failed install must never leave a stamp claiming its extras are present"
    )


def test_skipopt_falls_back_to_the_base_install(tmp_path):
    """SKIPOPT=1: extras are optional, so only the empty stamp may be validated."""
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    # Fails for `.[<extras>]`, succeeds for a plain `-e .`
    shim(bin_dir, "uv", 'case "$*" in *"["*) exit 1 ;; *) exit 0 ;; esac')

    proc = run(INSTALL_SH, "dev", "1", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0, proc.stderr
    assert stamps(work) == {"install-requirements-"}
    assert "SKIPOPT=1" in proc.stderr


def test_without_skipopt_a_failed_extras_install_fails(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    shim(bin_dir, "uv", 'case "$*" in *"["*) exit 1 ;; *) exit 0 ;; esac')

    proc = run(INSTALL_SH, "dev", "", cwd=work, bin_dir=bin_dir)

    assert proc.returncode != 0
    assert stamps(work) == set()


# ---------------------------------------------------------------------------
# Concept 3: the docker stamps track image presence, not command invocation
# ---------------------------------------------------------------------------

IMAGE = "ghcr.io/skillberry-ai/skillberry-store"
TAG = "0.2.1-7-gc9b7ddd"

# `docker image inspect` answers for exactly one tag; anything else is absent.
PRESENT = f'[ "$1" = "image" ] && [ "$3" = "{IMAGE}:{TAG}" ] && exit 0; exit 1'
ABSENT = "exit 1"


@pytest.fixture
def reconcile_env(tmp_path):
    work = tmp_path / "work"
    (work / ".stamps").mkdir(parents=True)
    return work, tmp_path / "bin"


def build_and_get_stamps(work: Path, tag: str = TAG) -> None:
    for name in (f"docker-build-local-{tag}", f"docker-get-{tag}"):
        (work / ".stamps" / name).touch()


def test_stamps_survive_while_the_image_is_present(reconcile_env):
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", PRESENT)
    build_and_get_stamps(work)

    proc = run(RECONCILE_SH, "docker", IMAGE, TAG, "local", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0
    assert stamps(work) == {f"docker-build-local-{TAG}", f"docker-get-{TAG}"}


def test_stamps_are_dropped_when_the_image_is_gone(reconcile_env):
    """`docker rmi` behind make's back must not leave the build believing the image is there."""
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", ABSENT)
    build_and_get_stamps(work)

    proc = run(RECONCILE_SH, "docker", IMAGE, TAG, "local", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0
    assert stamps(work) == set()


def test_only_this_tag_is_reconciled(reconcile_env):
    """Another variant's image is still present; its stamps are not ours to drop."""
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", ABSENT)
    build_and_get_stamps(work)
    build_and_get_stamps(work, tag=f"{TAG}-full")
    (work / ".stamps" / "docker-get-my-experiment").touch()

    run(RECONCILE_SH, "docker", IMAGE, TAG, "local", cwd=work, bin_dir=bin_dir)

    assert stamps(work) == {
        f"docker-build-local-{TAG}-full",
        f"docker-get-{TAG}-full",
        "docker-get-my-experiment",
    }


def test_registry_builds_are_left_alone(reconcile_env):
    """A pushed manifest cannot be checked locally; concept 3 accepts push presence."""
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", ABSENT)
    (work / ".stamps" / f"docker-build-registry-{TAG}").touch()
    build_and_get_stamps(work)

    run(RECONCILE_SH, "docker", IMAGE, TAG, "registry", cwd=work, bin_dir=bin_dir)

    assert f"docker-build-registry-{TAG}" in stamps(work)


def test_a_missing_docker_is_not_an_error(reconcile_env):
    """Parse-time hook: it must never break a `make help` on a machine without docker."""
    work, bin_dir = reconcile_env
    bin_dir.mkdir(parents=True, exist_ok=True)
    build_and_get_stamps(work)

    proc = run(RECONCILE_SH, "no-such-docker", IMAGE, TAG, "local", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0
    assert proc.stdout == "", "a $(shell ...) hook must not print to stdout"
    assert stamps(work) == {f"docker-build-local-{TAG}", f"docker-get-{TAG}"}


def test_nothing_to_reconcile_does_not_call_docker(reconcile_env):
    """With no stamp to invalidate there is no reason to pay for a docker call."""
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", f'echo called >> "{work}/docker-calls"; exit 1')

    proc = run(RECONCILE_SH, "docker", IMAGE, TAG, "local", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0
    assert not (work / "docker-calls").exists()


def test_an_empty_tag_is_a_no_op(reconcile_env):
    """DEPLOY_ONLY and other label-less contexts must not glob stamps away."""
    work, bin_dir = reconcile_env
    shim(bin_dir, "docker", ABSENT)
    build_and_get_stamps(work)

    proc = run(RECONCILE_SH, "docker", IMAGE, "", "local", cwd=work, bin_dir=bin_dir)

    assert proc.returncode == 0
    assert stamps(work) == {f"docker-build-local-{TAG}", f"docker-get-{TAG}"}


# ---------------------------------------------------------------------------
# Concepts 3 + 4: which change detector each tagging scheme uses
# ---------------------------------------------------------------------------

MANIFEST_STAMP = ".stamps/git-version-manifest"


def _docker_build_rule(*make_vars: str) -> str:
    """The resolved `.stamps/docker-build-local-*` rule line from make's database."""
    proc = subprocess.run(
        ["make", "-pRrq", *make_vars, "print_build_version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert "# Files" in proc.stdout, f"could not read make database:\n{proc.stderr}"
    rules = [
        line
        for line in proc.stdout.splitlines()
        if line.startswith(".stamps/docker-build-local") and ":" in line
    ]
    assert len(rules) == 1, f"expected one docker-build-local rule, got {rules}"
    return rules[0]


@pytest.mark.parametrize("scheme", ["default", "suffixed"])
def test_a_label_scoped_stamp_takes_the_manifest_order_only(scheme):
    """The stamp name carries the label, so the label is the change detector.

    Depending on the manifest's *mtime* as well would only add false positives:
    returning to an already-built state (a stash pop, a checkout back and forth,
    a CI job revisiting an older commit) moves the manifest while the stamp and
    the image for that label are both still there — which concept 3 says is
    exactly when no build work should happen. Order-only keeps the manifest up to
    date (it is what projects the label to VERSION_LOCATION) without making it a
    reason to rebuild.
    """
    make_vars = [] if scheme == "default" else ["IMAGE_TAG_SUFFIX=-full"]
    rule = _docker_build_rule(*make_vars)

    target, prereqs = rule.split(":", 1)
    assert MANIFEST_STAMP in prereqs, "the manifest must still be brought up to date"
    order_only = prereqs.split("|", 1)[1] if "|" in prereqs else ""
    assert MANIFEST_STAMP in order_only, (
        f"{MANIFEST_STAMP} is a timestamp prerequisite of {target}, so every return "
        "to an already-built state rebuilds an image that already exists"
    )


def test_a_custom_tag_takes_the_manifest_as_a_real_prerequisite():
    """A fixed tag says nothing about state, so only the manifest's mtime can."""
    rule = _docker_build_rule("CUSTOM_TAG=my-experiment")

    _, prereqs = rule.split(":", 1)
    timestamp_prereqs = prereqs.split("|", 1)[0]
    assert MANIFEST_STAMP in timestamp_prereqs, (
        "with a fixed tag the stamp name cannot tell that the tree moved on, so the "
        "manifest must be a real prerequisite"
    )
