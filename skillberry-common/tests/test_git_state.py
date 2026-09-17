# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Exercises the build system's git-state code against the concepts it implements.

``scripts/git_state.py`` computes BUILD_VERSION and maintains
``.stamps/git-version-manifest``, the pivot of the build stamp graph. Concepts 1,
2 and 4 of ``docs/design/build_concepts.md`` state the properties this must have,
and each one is a property the makefiles now *rely* on: the label names the
docker stamps, and the manifest's mtime is what tells every downstream stamp
whether anything changed.

Everything runs in a throwaway repository of its own, so nothing here depends on
the state of the checkout it runs in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from build_paths import COMMON_ROOT

GIT_STATE = COMMON_ROOT / "scripts" / "git_state.py"

MANIFEST = ".stamps/git-version-manifest"

# A git that ignores the developer's own config, so the tests behave the same on
# every machine (and cannot be broken by a global core.hooksPath or excludesfile).
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed:\n{proc.stderr}"
    return proc.stdout


def git_state(repo: Path, *args: str, env: dict[str, str] | None = None):
    """Run git_state.py inside `repo`; returns the completed process."""
    return subprocess.run(
        [sys.executable, str(GIT_STATE), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
        timeout=300,
    )


def version(repo: Path) -> str:
    proc = git_state(repo, "version")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def update(repo: Path, label: str | None = None, version_location: str = "", **kw):
    """The `update` subcommand, defaulting the label to the current one."""
    proc = git_state(repo, "update", label or version(repo), MANIFEST, version_location, **kw)
    assert proc.returncode == 0, proc.stderr
    return proc


def write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one commit, one gitignored path, and no releases."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    write(repo, ".gitignore", ".stamps/\nbuild/\n*.log\n")
    write(repo, "tracked.txt", "one\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    return repo


# ---------------------------------------------------------------------------
# Concept 1: the label is a pure function of state
# ---------------------------------------------------------------------------


def test_label_is_stable_when_nothing_changes(repo):
    assert version(repo) == version(repo)


def test_label_changes_on_a_new_commit(repo):
    before = version(repo)
    write(repo, "tracked.txt", "two\n")
    git(repo, "commit", "-am", "second")
    assert version(repo) != before


def test_label_changes_when_a_tracked_file_is_modified(repo):
    clean = version(repo)
    write(repo, "tracked.txt", "modified\n")
    dirty = version(repo)
    assert dirty != clean
    assert dirty.startswith(clean), "the dirty label must extend the clean one"
    assert "-dirty-" in dirty


def test_different_dirty_content_gives_different_labels(repo):
    """A bare `-dirty` suffix is explicitly insufficient (concept 1)."""
    write(repo, "tracked.txt", "first edit\n")
    first = version(repo)
    write(repo, "tracked.txt", "second edit\n")
    assert version(repo) != first


def test_staging_an_edit_keeps_the_label_but_moves_the_manifest(repo):
    """The label identifies buildable content; the manifest records index state.

    `git add` of an already-tracked file changes nothing about what a build would
    produce, so it must not invalidate an image already built at this label — but
    the manifest, which is the change *detector*, records the index move all the
    same, so nothing is lost from change detection.
    """
    write(repo, "tracked.txt", "an edit\n")
    unstaged_label = version(repo)
    unstaged_manifest = git_state(repo, "manifest").stdout

    git(repo, "add", "tracked.txt")

    assert version(repo) == unstaged_label
    assert git_state(repo, "manifest").stdout != unstaged_manifest


def test_label_changes_when_a_new_file_is_added_to_tracking(repo):
    """Untracked → tracked is a real state change (concept 1)."""
    write(repo, "added.txt", "content\n")
    untracked = version(repo)
    git(repo, "add", "added.txt")
    assert version(repo) != untracked


def test_label_changes_when_a_file_is_removed_from_tracking(repo):
    tracked = version(repo)
    git(repo, "rm", "--cached", "tracked.txt")
    assert version(repo) != tracked


def test_label_changes_when_a_tracked_file_is_deleted(repo):
    before = version(repo)
    (repo / "tracked.txt").unlink()
    assert version(repo) != before


def test_label_changes_for_a_new_untracked_file(repo):
    before = version(repo)
    write(repo, "brand-new.txt", "hello\n")
    after = version(repo)
    assert after != before
    # ... and again when only that untracked file's content changes.
    write(repo, "brand-new.txt", "goodbye\n")
    assert version(repo) != after


def test_label_ignores_gitignored_files(repo):
    before = version(repo)
    write(repo, "noise.log", "chatter\n")
    write(repo, "build/artifact.bin", "binary\n")
    (repo / ".stamps").mkdir(exist_ok=True)
    write(repo, ".stamps/some-stamp", "")
    assert version(repo) == before


def test_label_ignores_remote_refs_that_were_only_fetched(repo):
    """Fetching or pruning a remote ref is not a change to the working tree."""
    before = version(repo)
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/some-feature", head)
    git(repo, "update-ref", "refs/remotes/origin/branch-a-feature-branch", head)
    assert version(repo) == before, (
        "a remote ref named branch-* but not version-shaped was mistaken for a release"
    )


# ---------------------------------------------------------------------------
# Concept 1: release identification
# ---------------------------------------------------------------------------


def test_label_is_the_bare_release_at_a_release_commit(repo):
    git(repo, "tag", "-a", "0.3.0", "-m", "release 0.3.0")
    assert version(repo) == "0.3.0"


def test_label_counts_commits_past_the_release(repo):
    git(repo, "tag", "-a", "0.3.0", "-m", "release 0.3.0")
    write(repo, "tracked.txt", "after release\n")
    git(repo, "commit", "-am", "post-release")
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()
    assert version(repo) == f"0.3.0-1-g{sha}"


def test_label_uses_the_highest_release_not_the_last_ref(repo):
    for tag in ("0.3.0", "0.10.0", "0.4.0"):
        git(repo, "tag", "-a", tag, "-m", tag)
    assert version(repo).startswith("0.10.0"), "0.10 sorts above 0.4 numerically"


def test_a_prerelease_tag_does_not_outrank_its_release(repo):
    git(repo, "tag", "-a", "0.3.0-rc1", "-m", "rc")
    git(repo, "tag", "-a", "0.3.0", "-m", "release")
    assert version(repo) == "0.3.0"


def test_release_branch_refs_are_honored(repo):
    """The release convention is a branch per release; a bare clone may have no tag."""
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/branch-0.5.2", head)
    git(repo, "tag", "0.5.2")  # what the count is measured against
    assert version(repo) == "0.5.2"


def test_label_matches_git_describe(repo):
    """The label follows `git describe --always --dirty` conventions (concept 1)."""
    git(repo, "tag", "-a", "0.3.0", "-m", "release")
    write(repo, "tracked.txt", "post\n")
    git(repo, "commit", "-am", "post-release")
    described = git(repo, "describe", "--always", "--dirty").strip()
    assert version(repo) == described

    write(repo, "tracked.txt", "now dirty\n")
    described_dirty = git(repo, "describe", "--always", "--dirty").strip()
    label = version(repo)
    # git stops at "-dirty"; the label adds the content fingerprint concept 1 requires.
    assert label.startswith(described_dirty + "-")


def test_no_release_yields_the_g_sha_form(repo):
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()
    assert version(repo) == f"g{sha}"


# ---------------------------------------------------------------------------
# Concept 2: the manifest is content-idempotent and keeps its mtime
# ---------------------------------------------------------------------------


def test_manifest_is_byte_stable_for_the_same_state(repo):
    first = git_state(repo, "manifest")
    second = git_state(repo, "manifest")
    assert first.returncode == 0
    assert first.stdout == second.stdout


def test_manifest_differs_when_state_differs(repo):
    before = git_state(repo, "manifest").stdout
    write(repo, "tracked.txt", "changed\n")
    assert git_state(repo, "manifest").stdout != before


def test_update_keeps_mtime_stable_when_nothing_changed(repo):
    update(repo)
    manifest = repo / MANIFEST
    stat_before = manifest.stat()
    os.utime(manifest, (stat_before.st_atime, stat_before.st_mtime - 5))
    expected = manifest.stat().st_mtime_ns

    update(repo)

    assert manifest.stat().st_mtime_ns == expected, (
        "the manifest was rewritten for an unchanged state, which re-fires every "
        "downstream stamp"
    )


def test_update_rewrites_the_manifest_when_state_changed(repo):
    update(repo)
    manifest = repo / MANIFEST
    before = manifest.read_text()
    os.utime(manifest, (0, 0))

    write(repo, "tracked.txt", "changed\n")
    update(repo)

    assert manifest.read_text() != before
    assert manifest.stat().st_mtime_ns > 0, "mtime must advance on a real change"


def test_a_gitignored_file_does_not_touch_the_manifest(repo):
    update(repo)
    manifest = repo / MANIFEST
    os.utime(manifest, (0, 0))

    write(repo, "ignored.log", "noise\n")
    proc = update(repo)

    assert manifest.stat().st_mtime_ns == 0
    assert proc.stderr == ""


# ---------------------------------------------------------------------------
# Concept 2: observability
# ---------------------------------------------------------------------------


def test_update_is_silent_when_nothing_changed(repo):
    update(repo)
    assert update(repo).stderr == ""


def test_first_update_reports_the_label(repo):
    label = version(repo)
    stderr = update(repo, label).stderr
    assert f"BUILD_VERSION set to '{label}'" in stderr


def test_update_reports_the_new_label_on_a_change(repo):
    update(repo)
    write(repo, "tracked.txt", "changed\n")
    label = version(repo)
    assert f"BUILD_VERSION updated to '{label}'" in update(repo, label).stderr


def test_verbose_lists_the_files_responsible(repo):
    update(repo)
    write(repo, "tracked.txt", "changed\n")
    write(repo, "appeared.txt", "new\n")

    stderr = update(repo, env={"VERBOSE_BUILD_VERSION": "1"}).stderr

    assert "changes have been detected" in stderr
    assert "+ tracked.txt" in stderr, "a newly-dirty tracked file is reported"
    assert "+ appeared.txt" in stderr, "a new untracked file is reported"


def test_verbose_reports_a_file_that_is_no_longer_dirty(repo):
    write(repo, "tracked.txt", "changed\n")
    update(repo)
    write(repo, "tracked.txt", "one\n")  # back to the committed content

    stderr = update(repo, env={"VERBOSE_BUILD_VERSION": "1"}).stderr

    assert "- tracked.txt" in stderr


def test_the_terse_mode_stays_terse(repo):
    """Without VERBOSE, a change is one line — the build log is not a diff viewer."""
    update(repo)
    write(repo, "tracked.txt", "changed\n")
    stderr = update(repo).stderr
    assert len([line for line in stderr.splitlines() if line.strip()]) == 1


# ---------------------------------------------------------------------------
# Concept 2: optional projection to VERSION_LOCATION
# ---------------------------------------------------------------------------


def test_version_location_is_written_and_content_idempotent(repo):
    update(repo, "1.2.3", version_location="pkg/git_version.py")
    generated = repo / "pkg" / "git_version.py"
    assert generated.read_text() == '__git_version__ = "1.2.3"\n'

    os.utime(generated, (0, 0))
    write(repo, "tracked.txt", "changed\n")
    update(repo, "1.2.3", version_location="pkg/git_version.py")
    assert generated.stat().st_mtime_ns == 0, "same label, so the file must not be rewritten"

    update(repo, "1.2.4", version_location="pkg/git_version.py")
    assert generated.read_text() == '__git_version__ = "1.2.4"\n'


def test_the_manifest_is_the_pivot_without_a_version_location(repo):
    """VERSION_LOCATION is optional; the manifest is written regardless (concept 2)."""
    update(repo)
    assert (repo / MANIFEST).is_file()


# ---------------------------------------------------------------------------
# Outside a repository
# ---------------------------------------------------------------------------


def test_update_outside_a_repository_still_writes_the_manifest(tmp_path):
    """Otherwise make sees a prerequisite it cannot create, and rebuilds forever."""
    plain = tmp_path / "tarball"
    plain.mkdir()

    first = git_state(plain, "update", "unknown", MANIFEST, "ver.py")
    assert first.returncode == 0, first.stderr
    manifest = plain / MANIFEST
    assert manifest.is_file()
    assert (plain / "ver.py").read_text() == '__git_version__ = "unknown"\n'

    os.utime(manifest, (0, 0))
    second = git_state(plain, "update", "unknown", MANIFEST, "ver.py")
    assert second.returncode == 0
    assert second.stderr == "", "a state that cannot change must produce no output"
    assert manifest.stat().st_mtime_ns == 0


def test_version_outside_a_repository_is_unknown(tmp_path):
    plain = tmp_path / "tarball"
    plain.mkdir()
    assert version(plain) == "unknown"
