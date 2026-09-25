# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Archive-shape contracts the npx well-known protocol depends on.

Two blockers from docs/design/npx.md: the export ZIP was not byte-deterministic
(§5.1), so every published digest was wrong and every skill was silently
dropped; and every entry was nested under ``<skill-name>/`` (§5.2), which the
CLI rejects because it looks up a *root* ``SKILL.md`` and strips no leading
component.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile

import pytest

from skillberry_store.tools.anthropic.exporter import (
    build_deterministic_zip,
    export_skill_to_anthropic_format,
    strip_skill_prefix,
    unsafe_archive_paths,
    _build_file_structure,
)

FILES = {
    "SKILL.md": b"---\nname: demo\n---\n",
    "scripts/fill.py": b"print('hi')\n",
    "reference/spec.md": b"# spec\n",
}


def test_repeated_calls_produce_identical_bytes():
    """The §5.1 regression: a wall-clock mtime made every digest wrong."""
    first = build_deterministic_zip(FILES)
    time.sleep(1.1)  # long enough for a DOS timestamp (2-second resolution)
    second = build_deterministic_zip(FILES)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_input_key_order_does_not_affect_output():
    reordered = {k: FILES[k] for k in reversed(list(FILES))}
    assert build_deterministic_zip(reordered) == build_deterministic_zip(FILES)


def test_round_trip_recovers_every_file():
    with zipfile.ZipFile(io.BytesIO(build_deterministic_zip(FILES))) as zf:
        assert sorted(zf.namelist()) == sorted(FILES)
        for path, content in FILES.items():
            assert zf.read(path) == content


def test_entries_are_sorted_and_stamped_at_the_zip_epoch():
    with zipfile.ZipFile(io.BytesIO(build_deterministic_zip(FILES))) as zf:
        assert zf.namelist() == sorted(FILES)
        for info in zf.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_DEFLATED


def test_empty_input_produces_a_valid_empty_archive():
    with zipfile.ZipFile(io.BytesIO(build_deterministic_zip({}))) as zf:
        assert zf.namelist() == []


def _skill(name="demo", description="A demo skill."):
    return {"name": name, "description": description}


def test_export_endpoint_bytes_are_now_deterministic():
    """§5.13: ``export-anthropic`` moved onto the one deterministic builder."""
    args = (_skill(), [], [{"tags": ["file:notes.md"], "content": "hello"}], {})
    first = export_skill_to_anthropic_format(*args)
    time.sleep(1.1)
    assert export_skill_to_anthropic_format(*args) == first


def test_export_endpoint_keeps_its_container_directory():
    """The path layout stays a caller choice — the download keeps the prefix."""
    payload = export_skill_to_anthropic_format(_skill(), [], [], {})
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert all(n.startswith("demo/") for n in zf.namelist())
        assert "demo/SKILL.md" in zf.namelist()


def test_strip_skill_prefix_puts_skill_md_at_the_root():
    files = _build_file_structure(
        _skill(),
        [],
        [{"tags": ["file:reference/spec.md"], "content": "# spec"}],
        {},
    )
    stripped = strip_skill_prefix(files, "demo")
    assert "SKILL.md" in stripped
    assert "reference/spec.md" in stripped
    assert not any(k.startswith("demo/") for k in stripped)


def test_strip_skill_prefix_leaves_unprefixed_keys_alone():
    assert strip_skill_prefix({"SKILL.md": b"x"}, "demo") == {"SKILL.md": b"x"}


def test_strip_skill_prefix_does_not_promote_a_nested_skill_md():
    """Only the leading ``<skill_name>/`` component is removed, once."""
    stripped = strip_skill_prefix({"demo/sub/SKILL.md": b"x"}, "demo")
    assert list(stripped) == ["sub/SKILL.md"]


def test_strip_skill_prefix_strips_only_one_occurrence():
    stripped = strip_skill_prefix({"demo/demo/SKILL.md": b"x"}, "demo")
    assert list(stripped) == ["demo/SKILL.md"]


def test_a_real_export_carries_no_path_the_cli_would_reject():
    """Documents the CLI's contract even though the builder cannot emit one.

    A future change to ``file:`` tag handling then fails here rather than in a
    user's terminal.
    """
    files = strip_skill_prefix(
        _build_file_structure(
            _skill(),
            [{"name": "t", "tags": [], "programming_language": "python"}],
            [{"tags": ["file:reference/spec.md"], "content": "# spec"}],
            {"t": "def t(): pass"},
        ),
        "demo",
    )
    assert unsafe_archive_paths(files) == []


@pytest.mark.parametrize(
    "path",
    [
        "/abs/path.txt",
        "../escape.txt",
        "a/../../escape.txt",
        "dir\\file.txt",
        "C:/windows/system32",
        "nul\x00byte.txt",
        "",
    ],
)
def test_unsafe_archive_paths_flags_every_cli_rejection(path):
    assert unsafe_archive_paths({path: b"x"}) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "SKILL.md",
        "scripts/fill.py",
        "a/b/c/d.txt",
        "..hidden/file.txt",
        "file..txt",
    ],
)
def test_unsafe_archive_paths_accepts_ordinary_relative_paths(path):
    assert unsafe_archive_paths({path: b"x"}) == []


def test_unsafe_archive_paths_reports_every_offender_sorted():
    files = {"SKILL.md": b"x", "/b": b"x", "../a": b"x"}
    assert unsafe_archive_paths(files) == ["../a", "/b"]


def test_no_entry_is_a_symlink_or_directory():
    """§5.3 #4: the CLI rejects symlink and hardlink entries outright."""
    with zipfile.ZipFile(io.BytesIO(build_deterministic_zip(FILES))) as zf:
        for info in zf.infolist():
            assert not info.is_dir()
            mode = info.external_attr >> 16
            assert not (mode & 0o170000 == 0o120000), info.filename
