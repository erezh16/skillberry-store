# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""HTTP behaviour of the /cli/* download surface.

docs/design/new_cli.md §8.2 #10, #13, #14, #16. Built on a minimal FastAPI app
rather than the full ``SBS``, because these assertions are about the routes'
status codes, headers and refusals — constructing the whole store would add the
vector DB, the plugin loader and the object handlers to a test about a
``Content-Disposition`` header.

Registration against the real ``SBS`` app, and the ACL floor that keeps these
routes reachable without a session, are covered in
``test_cli_api_access_control.py``.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillberry_store.fast_api.cli_api import (
    register_cli_api,
    render_install_ps1,
    render_install_sh,
)
from skillberry_store.services.cli_artifacts import (
    SLOT_PATTERN,
    CliArtifactService,
    CliArtifactSettings,
    UnacceptableURL,
)

PUBLIC_URL = "http://store.test:8000"

UA_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
UA_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)


def _fake_prebuilt(root: Path) -> Path:
    """Artifacts shaped like cli/build.sh's output, with patchable slots."""
    for platform in (
        "linux-amd64",
        "linux-arm64",
        "darwin-amd64",
        "darwin-arm64",
        "windows-amd64",
    ):
        d = root / platform
        d.mkdir(parents=True)
        name = "sbs.exe" if platform.startswith("windows-") else "sbs"
        (d / name).write_bytes(b"\x7fELF" + SLOT_PATTERN + platform.encode() * 8)
    (root / "LICENSE.restish").write_text("MIT License\n\nCopyright\n", encoding="utf-8")
    (root / "prebuilt-manifest.json").write_text(
        json.dumps(
            {
                "cli_version": "1.2.3",
                "engine": {"name": "restish", "version": "2.3.0"},
            }
        ),
        encoding="utf-8",
    )
    return root


def _make_app(tmp_path, *, prepare=True, public_url=PUBLIC_URL, **kw):
    settings = CliArtifactSettings(
        dist_dir=tmp_path / "dist",
        artifacts_dir=_fake_prebuilt(tmp_path / "prebuilt"),
        **kw,
    )
    service = CliArtifactService(settings, public_url=public_url, cli_commit="gabc123")
    if prepare:
        service.prepare_all()
    app = FastAPI()
    app.state.cli_artifacts = service
    register_cli_api(app, service=service)
    return app, service


@pytest.fixture
def client_and_service(tmp_path):
    app, service = _make_app(tmp_path)
    with TestClient(app) as client:
        yield client, service


# --------------------------------------------------------------------------- #
# Manifest — §8.2 #7
# --------------------------------------------------------------------------- #


def test_manifest_is_always_200(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/manifest")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["cli_name"] == "sbs"
    assert doc["public_url"] == PUBLIC_URL
    assert doc["engine"]["license_url"] == "/cli/license"
    assert doc["platforms"]["linux-amd64"]["state"] == "ready"


def test_manifest_is_200_even_with_nothing_prepared(tmp_path):
    """A client must be able to tell "not yet" from "never" from "no such store".

    Three outcomes a 404 or 503 on the manifest would collapse into one.
    """
    app, _ = _make_app(tmp_path, prepare=False)
    with TestClient(app) as client:
        resp = client.get("/cli/manifest")
        assert resp.status_code == 200
        assert resp.json()["platforms"]["linux-amd64"]["state"] != "ready"


def test_manifest_urls_are_relative(client_and_service):
    """So the document is correct behind any path prefix or ingress rewrite."""
    client, _ = client_and_service
    for entry in client.get("/cli/manifest").json()["platforms"].values():
        for key in ("download_url", "archive_url"):
            if key in entry:
                assert entry[key].startswith("/cli/"), entry[key]


def test_manifest_carries_vary(client_and_service):
    client, _ = client_and_service
    assert "Vary" in client.get("/cli/manifest").headers


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #


def test_download_serves_the_patched_artifact(client_and_service):
    client, service = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64&format=raw")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    # The bytes must be the prepared ones, carrying this store's URL.
    assert PUBLIC_URL.encode() in resp.content
    assert SLOT_PATTERN not in resp.content

    entry = service.resolve("linux-amd64")
    assert hashlib.sha256(resp.content).hexdigest() == entry.sha256
    assert resp.headers["etag"] == f'"{entry.sha256}"'


def test_download_sets_content_disposition(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64")
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    assert 'filename="sbs"' in disposition

    resp = client.get("/cli/download?platform=windows-amd64")
    assert 'filename="sbs.exe"' in resp.headers["content-disposition"]


def test_download_caching_headers(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64")
    assert resp.headers["cache-control"] == "public, max-age=300"
    # Without Vary, one shared cache hands a Windows user the Linux binary (B11).
    for header in ("Sec-CH-UA-Platform", "Sec-CH-UA-Arch", "User-Agent"):
        assert header in resp.headers["vary"]


def test_conditional_get_returns_304(client_and_service):
    """A repeat download of 32 MB should cost one round trip (§7.3)."""
    client, service = client_and_service
    etag = f'"{service.resolve("linux-amd64").sha256}"'
    resp = client.get(
        "/cli/download?platform=linux-amd64", headers={"If-None-Match": etag}
    )
    assert resp.status_code == 304
    assert not resp.content


def test_head_returns_headers_and_no_body(client_and_service):
    """§8.2 #13."""
    client, _ = client_and_service
    head = client.head("/cli/download?platform=linux-amd64")
    get = client.get("/cli/download?platform=linux-amd64")

    assert head.status_code == 200
    assert not head.content
    # The point of HEAD is to learn size and identity without transferring.
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-length"] == get.headers["content-length"]


def test_range_request_returns_206(client_and_service):
    """FileResponse gives resumable downloads for free — the reason for using it."""
    client, _ = client_and_service
    resp = client.get(
        "/cli/download?platform=linux-amd64", headers={"Range": "bytes=0-9"}
    )
    assert resp.status_code == 206
    assert len(resp.content) == 10
    assert "content-range" in resp.headers


def test_archive_format_serves_a_tarball(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64&format=archive")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert "sbs-linux-amd64.tar.gz" in resp.headers["content-disposition"]

    import io

    with tarfile.open(fileobj=io.BytesIO(resp.content)) as tf:
        names = tf.getnames()
        assert "sbs" in names
        # A browser download loses the executable bit; the archive preserves it.
        assert tf.getmember("sbs").mode & 0o111
        assert "LICENSE.restish" in names


def test_windows_archive_is_a_zip(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=windows-amd64&format=archive")
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content[:2] == b"PK"


# --------------------------------------------------------------------------- #
# Refusals — §8.2 #10, #14
# --------------------------------------------------------------------------- #


def test_unknown_platform_is_400_not_a_substitution(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=plan9-mips")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_platform"
    # Naming what is available turns a dead end into a next step.
    assert "linux-amd64" in detail["supported"]


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "linux-amd64/../../../etc/passwd",
        "/etc/passwd",
        "..",
        ".",
    ],
)
def test_traversal_attempts_are_400_and_read_nothing(client_and_service, hostile):
    """§8.2 #14 / B13: the value is checked against a closed enum first."""
    client, _ = client_and_service
    resp = client.get("/cli/download", params={"platform": hostile})
    assert resp.status_code == 400, resp.text
    body = resp.text
    assert "root:" not in body
    assert "/bin/bash" not in body


def test_bad_format_is_rejected(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64&format=exe")
    # FastAPI's pattern validation answers 422 for a malformed query value.
    assert resp.status_code == 422


def test_preparing_platform_is_503_with_retry_after(tmp_path):
    """§8.2 #10: Retry-After rather than holding the connection open."""
    app, service = _make_app(tmp_path, prepare=False)
    service.mark_preparing()
    with TestClient(app) as client:
        resp = client.get("/cli/download?platform=linux-amd64")
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "10"
        assert resp.json()["detail"] == "preparing"


def test_unavailable_platform_is_404_with_a_reason(tmp_path):
    app, service = _make_app(tmp_path, prepare=False)
    service.prepare_all()
    # Simulate a platform whose artifact was never bundled.
    service._platforms["windows-amd64"].state = "unavailable"
    service._platforms["windows-amd64"].reason = "not_bundled"
    with TestClient(app) as client:
        resp = client.get("/cli/download?platform=windows-amd64")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_bundled"


def test_manifest_disk_disagreement_is_503(client_and_service):
    """The dist dir was cleaned underneath us: truthful 503, not a 500."""
    client, service = client_and_service
    service.artifact_path("linux-amd64", "sbs").unlink()
    resp = client.get("/cli/download?platform=linux-amd64")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "10"


def test_rate_limit_returns_429(tmp_path):
    """§8.2 #10 / B12: an unauthenticated 32 MB GET is an amplification risk."""
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        codes = [
            client.get("/cli/download?platform=linux-amd64").status_code
            for _ in range(40)
        ]
    assert 429 in codes, "no request was ever rate limited"
    limited = next(i for i, c in enumerate(codes) if c == 429)
    # Generous enough not to bother a real user chasing a flaky download.
    assert limited >= 20, f"rate limited after only {limited} requests"


# --------------------------------------------------------------------------- #
# Platform detection through the endpoint — §8.2 #11
# --------------------------------------------------------------------------- #


def test_detection_header_reports_the_source(client_and_service):
    client, _ = client_and_service

    explicit = client.get("/cli/download?platform=linux-arm64")
    assert explicit.headers["x-sbs-platform-detection"] == "param"

    hinted = client.get(
        "/cli/download",
        headers={
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-CH-UA-Arch": '"x86"',
            "Sec-CH-UA-Bitness": '"64"',
        },
    )
    assert hinted.headers["x-sbs-platform-detection"] == "client-hints"

    # A Mac UA cannot reveal the CPU, so the answer is marked as a guess — which
    # is what makes a wrong one diagnosable from a single response.
    guessed = client.get("/cli/download", headers={"User-Agent": UA_MAC})
    assert guessed.headers["x-sbs-platform-detection"] == "guessed"


def test_detection_picks_the_right_artifact(client_and_service):
    """Not just the right header — the right bytes."""
    client, _ = client_and_service
    windows = client.get("/cli/download", headers={"User-Agent": UA_WINDOWS})
    assert 'filename="sbs.exe"' in windows.headers["content-disposition"]

    mac = client.get("/cli/download", headers={"User-Agent": UA_MAC})
    assert b"darwin-arm64" in mac.content


def test_version_header_is_present(client_and_service):
    client, _ = client_and_service
    resp = client.get("/cli/download?platform=linux-amd64")
    assert resp.headers["x-sbs-cli-version"] == "1.2.3"


# --------------------------------------------------------------------------- #
# Install scripts — §8.2 #16
# --------------------------------------------------------------------------- #


def test_install_sh_inlines_the_url_and_checksums(client_and_service):
    client, service = client_and_service
    resp = client.get("/cli/install.sh")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/x-shellscript")
    body = resp.text

    # Single-quoted, so even a URL that somehow passed validation cannot break
    # out of the string.
    assert f"STORE_URL='{PUBLIC_URL}'" in body
    # Per-platform digests, inlined: shell has no JSON parser, and grepping the
    # manifest for the first 64-hex string would compare the wrong platform's
    # hash and refuse every valid install.
    assert service.resolve("linux-amd64").sha256 in body
    assert service.resolve("darwin-arm64").sha256 in body
    assert "case \"$platform\" in" in body


def test_install_sh_is_valid_posix_shell(client_and_service, tmp_path):
    """A syntax error here is a broken install for everyone using the one-liner."""
    import subprocess

    client, _ = client_and_service
    script = tmp_path / "install.sh"
    script.write_text(client.get("/cli/install.sh").text, encoding="utf-8")
    proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, f"generated install.sh is not valid sh:\n{proc.stderr}"


def test_install_ps1_is_generated(client_and_service):
    client, service = client_and_service
    resp = client.get("/cli/install.ps1")
    assert resp.status_code == 200
    body = resp.text
    assert f"$StoreUrl = '{PUBLIC_URL}'" in body
    assert service.resolve("windows-amd64").sha256 in body


@pytest.mark.parametrize(
    "hostile",
    [
        'http://evil.com/"$(id)"',
        "http://evil.com/`id`",
        "http://evil.com/;id",
        "http://evil.com/$(whoami)",
        "http://evil.com/|cat /etc/passwd",
    ],
)
def test_install_script_refuses_a_hostile_url(hostile):
    """§5.7: the generator refuses to emit rather than emitting injection.

    A generated script is executed by `sh` on the user's machine, so a Host like
    `evil.com/"$(id)"` reaching it is remote code execution — and `Host` is
    client-controlled.
    """
    with pytest.raises(UnacceptableURL):
        render_install_sh(hostile)
    with pytest.raises(UnacceptableURL):
        render_install_ps1(hostile)


@pytest.mark.parametrize(
    "hostile_host",
    [
        'evil.com/"$(id)"',
        "evil com",
        "evil.com%0d",
        "evil_com",
    ],
)
def test_install_sh_503s_on_a_hostile_host(tmp_path, hostile_host):
    """§8.2 #16: a hostile Host yields **no script** and a logged refusal.

    This is the end-to-end form of the injection concern, and the vector is real
    rather than theoretical: with no ``SBS_PUBLIC_URL`` the URL is derived from
    ``request.base_url``, and a ``Host: evil.com/"$(id)"`` arrives there verbatim
    — verified, ``base_url`` becomes ``http://evil.com/"$(id)"/``. Inlined into a
    script the user pipes to ``sh``, that is command execution on their machine.
    """
    app, _ = _make_app(tmp_path, public_url=None)
    with TestClient(app) as client:
        resp = client.get("/cli/install.sh", headers={"Host": hostile_host})
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == "public_url_unavailable"
        # Nothing script-shaped came back, and the hostile value is not echoed.
        assert "#!/bin/sh" not in resp.text
        assert "$(id)" not in resp.text


def test_install_sh_accepts_a_single_label_host(tmp_path):
    """`localhost` and a Docker service name have no dot and must still work.

    Rejecting dotless hostnames would break the most common local setup of all
    (`http://localhost:8000`) and every container-network deployment, so the
    validator deliberately allows them — the grammar excludes shell
    metacharacters, which is what actually matters.
    """
    app, _ = _make_app(tmp_path, public_url=None)
    with TestClient(app, base_url="http://store:8000") as client:
        resp = client.get("/cli/install.sh")
        assert resp.status_code == 200
        assert "STORE_URL='http://store:8000'" in resp.text


def test_install_sh_uses_the_request_host_when_no_public_url_is_set(tmp_path):
    """Safe *here* because a generated script is consumed by the asking client.

    A baked artifact is shared, which is why §6 forbids this fallback there.
    """
    app, _ = _make_app(tmp_path, public_url=None)
    with TestClient(app, base_url="http://store.example.com") as client:
        resp = client.get("/cli/install.sh")
        assert resp.status_code == 200
        assert "STORE_URL='http://store.example.com'" in resp.text


def test_public_url_wins_over_the_request_host(client_and_service):
    """B14: a spoofed Host must never redirect users at an attacker's store."""
    client, _ = client_and_service
    resp = client.get("/cli/install.sh", headers={"Host": "evil.example.com"})
    assert f"STORE_URL='{PUBLIC_URL}'" in resp.text
    assert "evil.example.com" not in resp.text


# --------------------------------------------------------------------------- #
# Licence
# --------------------------------------------------------------------------- #


def test_license_is_served(client_and_service):
    """We redistribute an MIT-licensed binary, so its licence must be available."""
    client, _ = client_and_service
    resp = client.get("/cli/license")
    assert resp.status_code == 200
    assert "MIT" in resp.text


def test_license_falls_back_when_the_file_is_absent(tmp_path):
    app, service = _make_app(tmp_path)
    (service.settings.artifacts_dir / "LICENSE.restish").unlink()
    with TestClient(app) as client:
        resp = client.get("/cli/license")
        # Still 200 with a pointer: an absent file must not look like "no licence".
        assert resp.status_code == 200
        assert "MIT" in resp.text


# --------------------------------------------------------------------------- #
# The off switch — §9 rollback
# --------------------------------------------------------------------------- #


def test_download_off_unregisters_the_routes(tmp_path):
    """The routes genuinely do not exist, rather than existing and refusing."""
    app, _ = _make_app(tmp_path, prepare=False, enabled=False)
    with TestClient(app) as client:
        for path in (
            "/cli/manifest",
            "/cli/download",
            "/cli/install.sh",
            "/cli/install.ps1",
            "/cli/license",
        ):
            assert client.get(path).status_code == 404, path
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/cli")]
