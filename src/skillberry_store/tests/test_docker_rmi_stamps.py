# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Guards that `docker-rmi` only invalidates the variant it removed.

Background (PR #308 review, issue #17): `docker-rmi` ran
``rm -f .stamps/docker-build*`` and ``.stamps/docker-get*``, so removing the
core-only image also deleted the ``-full`` variant's stamps — and any
``CUSTOM_TAG`` build's — forcing needless rebuilds of images that were still
present. Cosmetic, but it is the kind of thing that gets rediscovered as "the
build is slow for no reason".

Since the build-fix merge the stamps are keyed on the whole primary image tag
(``TAG_STAMP_SFX = -$(IMAGE_TAG)``, concept 3 of docs/design/build_concepts.md),
which makes one variant's stamp name a *prefix* of another's: the core image's
``docker-get-<label>`` sits directly in front of ``docker-get-<label>-full``.
Any reintroduction of a glob would therefore be worse than before, so the same
guarantees are checked against tag-shaped suffixes.

The recipe is executed against a temporary stamp directory rather than
pattern-matched, so the test describes the behaviour, not the wording.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_MK = REPO_ROOT / "skillberry-common" / ".mk" / "docker.mk"

# The label part of the default scheme's tags: IMAGE_TAG is
# $(BUILD_VERSION)$(IMAGE_TAG_SUFFIX), so the core image's stamps end in the bare
# label and the -full variant's add the suffix on top of it.
LABEL = "0.2.1-7-gc9b7ddd"

# The three tagging schemes a checkout can have stamps for at once: core,
# the -full variant, and a CUSTOM_TAG build.
SUFFIXES = [f"-{LABEL}", f"-{LABEL}-full", "-my-experiment"]

ALL_STAMPS = [
    f"docker-{kind}{suffix}"
    for suffix in SUFFIXES
    for kind in ("build-local", "build-registry", "get")
]


def _stamp_cleanup_commands() -> str:
    """The `rm` lines of the docker-rmi recipe, as shell."""
    text = DOCKER_MK.read_text()
    match = re.search(r"^docker-rmi:.*?\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert match, "no docker-rmi recipe found in docker.mk"

    lines = []
    for raw in match.group(1).splitlines():
        line = raw[1:]  # strip make's tab
        if line.startswith("@#"):  # a comment-only recipe line
            continue
        if line.startswith("@"):
            line = line[1:]
        lines.append(line)
    script = "\n".join(lines)
    # Keep only the stamp cleanup: the docker rmi loop needs a real daemon.
    start = script.index("rm -f")
    return script[start:]


def _run_cleanup(tmp_path: Path, tag_stamp_sfx: str) -> set[str]:
    """Run the cleanup for one tagging scheme; return the surviving stamp names."""
    stamps = tmp_path / ".stamps"
    stamps.mkdir()
    for name in ALL_STAMPS:
        (stamps / name).touch()

    script = _stamp_cleanup_commands().replace("$(TAG_STAMP_SFX)", tag_stamp_sfx)
    assert "$(" not in script, f"unexpanded make variable: {script}"

    proc = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return {p.name for p in stamps.iterdir()}


def test_removing_the_core_image_keeps_the_full_variant_stamps(tmp_path):
    survivors = _run_cleanup(tmp_path, f"-{LABEL}")

    assert f"docker-build-local-{LABEL}-full" in survivors, (
        "removing the core image invalidated the -full variant's stamps, forcing a "
        "rebuild of an image that is still present"
    )
    assert f"docker-build-registry-{LABEL}-full" in survivors
    assert f"docker-get-{LABEL}-full" in survivors
    assert "docker-build-local-my-experiment" in survivors, (
        "a CUSTOM_TAG build's stamps must survive too"
    )


def test_removing_the_core_image_clears_its_own_stamps(tmp_path):
    """Both DBT variants: the image is gone either way, so neither may look current."""
    survivors = _run_cleanup(tmp_path, f"-{LABEL}")

    assert f"docker-build-local-{LABEL}" not in survivors
    assert f"docker-build-registry-{LABEL}" not in survivors
    assert f"docker-get-{LABEL}" not in survivors


def test_removing_the_full_variant_clears_only_its_own_stamps(tmp_path):
    survivors = _run_cleanup(tmp_path, f"-{LABEL}-full")

    assert f"docker-build-local-{LABEL}-full" not in survivors
    assert f"docker-build-registry-{LABEL}-full" not in survivors
    assert f"docker-get-{LABEL}-full" not in survivors
    assert {
        f"docker-build-local-{LABEL}",
        f"docker-build-registry-{LABEL}",
        f"docker-get-{LABEL}",
    } <= survivors, (
        "removing the -full image must not invalidate the core image's stamps — its "
        "stamp names are a prefix of the -full variant's"
    )


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_cleanup_touches_exactly_one_tagging_scheme(tmp_path, suffix):
    """Whatever the scheme, no other scheme's stamps may be collateral damage."""
    expected_removed = {
        f"docker-build-local{suffix}",
        f"docker-build-registry{suffix}",
        f"docker-get{suffix}",
    } & set(ALL_STAMPS)

    survivors = _run_cleanup(tmp_path, suffix)

    assert survivors == set(ALL_STAMPS) - expected_removed
