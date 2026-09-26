# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Serve the native `sbs` CLI from the store it talks to.

docs/design/new_cli.md §5.5. Five routes, all unauthenticated in every ACL mode::

    GET       /cli/manifest      per-platform availability, version, sha256
    GET/HEAD  /cli/download      the artifact (?platform=, ?format=raw|archive)
    GET       /cli/install.sh    generated installer, this store's URL inlined
    GET       /cli/install.ps1   the Windows twin
    GET       /cli/license       restish's MIT licence, since we redistribute it

"Unauthenticated in every mode" is enforced by a **mandatory floor** in the
access-control loader (``_ALWAYS_UNAUTH_PATHS``), not by listing the paths in the
default allow-list — an operator's own ``unauthenticated_paths`` *replaces* the
defaults, so a default-list entry would be lost on exactly the deployments that
customise it (§5.5.3). Consequently these routes carry **no** ``@requires``
marker, which is what the RBAC audit expects for an allow-listed path — the same
as ``/health`` and ``/admin/metrics``.

Two transport choices are load-bearing rather than stylistic:

* **``FileResponse``, not ``StreamingResponse``.** Range requests,
  ``Last-Modified`` and conditional GETs come for free, which is what makes a
  32 MB download resumable and a repeat nearly free. ``admin_api``'s backup route
  streams because it generates its body; these files are on disk.
* **``Vary`` on the detection inputs.** Without it, one shared cache happily
  hands a Windows user the Linux binary (B11).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from skillberry_store.fast_api.platform_detect import (
    SUPPORTED_PLATFORMS,
    VARY_HEADER,
    detect_platform,
    is_supported_platform,
)
from skillberry_store.services.cli_artifacts import (
    STATE_PREPARING,
    STATE_READY,
    CliArtifactService,
    UnacceptableURL,
    artifact_checksums,
    validate_public_url,
)

logger = logging.getLogger(__name__)

# Artifacts are immutable for a given ETag, so a short shared-cache lifetime is
# safe and makes a repeat download nearly free (§7.3). Five minutes rather than
# a year because a deployment's SBS_PUBLIC_URL can change and re-prepare the
# artifact under the same URL path.
_CACHE_CONTROL = "public, max-age=300"

# Crude per-IP rate limiting (§7.3, B12). An unauthenticated 32 MB GET is an
# amplification opportunity, and this is honestly per-replica — operators should
# let an ingress or CDN carry /cli/*. It exists so that a single careless script
# cannot saturate a small deployment, not as a serious DoS defence.
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_PER_WINDOW = 30


class _DownloadLimiter:
    """Per-IP token bucket plus a global concurrency cap.

    Deliberately simple and in-process: a shared store would be a new dependency
    for a defence that an ingress does better anyway. Both limits are honest about
    being per-replica.
    """

    def __init__(self, max_concurrent: int):
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._max_concurrent = max_concurrent

    def allow(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - _RATE_WINDOW_SECONDS
        with self._lock:
            hits = [t for t in self._hits.get(client_ip, ()) if t > cutoff]
            if len(hits) >= _RATE_MAX_PER_WINDOW:
                self._hits[client_ip] = hits
                return False
            hits.append(now)
            self._hits[client_ip] = hits
            # Opportunistic sweep: without it this dict grows once per distinct
            # client IP and never shrinks, which is a slow leak on a public host.
            if len(self._hits) > 4096:
                self._hits = {
                    ip: times
                    for ip, times in self._hits.items()
                    if any(t > cutoff for t in times)
                }
            return True

    def acquire_slot(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release_slot(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:  # pragma: no cover - defensive
            logger.debug("Download semaphore over-released")


def _effective_public_url(request: Request, service: CliArtifactService) -> str:
    """The URL to inline into a generated install script.

    ``SBS_PUBLIC_URL`` is authoritative when set, because it is the only value
    that is known to be reachable from a user's terminal: ``uvicorn.run`` is
    called without ``forwarded_allow_ips``, so behind an ingress
    ``request.base_url`` reports the internal bind address.

    Falling back to ``request.base_url`` is safe *here* and nowhere else: a
    script is generated per request and consumed immediately by the client that
    asked, so a client-controlled Host only ever affects that client. A baked
    artifact is shared, which is why §6 forbids this fallback there.

    Raises ``UnacceptableURL`` when the derived value fails the §5.7 grammar,
    which the caller turns into a 503 rather than emitting a script. A Host like
    ``evil.com/"$(id)"`` inlined into a shell script is command execution on the
    user's machine (B14).
    """
    if service.public_url:
        return service.public_url
    derived = str(request.base_url).rstrip("/")
    return validate_public_url(derived)


def register_cli_api(
    app: FastAPI,
    service: Optional[CliArtifactService] = None,
    tags: str = "cli",
) -> None:
    """Register the CLI distribution routes, unless ``SBS_CLI_DOWNLOAD=off``.

    Registration is gated on the flag rather than the handlers refusing, so with
    it off the surface genuinely does not exist: ``/cli/manifest`` is a plain 404,
    no route appears in the app's table, and nothing shows in ``/openapi.json``.
    That is also the documented rollback for this whole feature (§9).
    """
    if service is None:
        service = getattr(app.state, "cli_artifacts", None)
    if service is None:
        raise RuntimeError("register_cli_api requires a CliArtifactService")

    if not service.settings.enabled:
        logger.info("SBS_CLI_DOWNLOAD is off — /cli/* is not registered")
        return

    limiter = _DownloadLimiter(service.settings.max_concurrent_downloads)

    logger.info(
        "CLI downloads are ON — serving GET /cli/manifest and /cli/download "
        "(artifacts=%s, dist=%s, mode=%s, prepare=%s, public_url=%s)",
        service.settings.artifacts_dir,
        service.dist_dir,
        service.settings.build_mode,
        service.settings.prepare,
        service.public_url or "(unset — artifacts stay pristine)",
    )

    # ----------------------------------------------------------------- manifest

    @app.get(
        "/cli/manifest",
        tags=[tags],
        operation_id="cli_manifest",
        summary="Describe the CLI artifacts this store can serve",
        # No x-mcp-tool: a 32 MB octet-stream is not an agent tool, and the
        # curated-surface audit is where that choice is recorded (§5.5.4).
        openapi_extra={"x-cli-name": "cli-manifest"},
    )
    async def cli_manifest(request: Request) -> JSONResponse:
        """Always 200, even when nothing is ready.

        A client needs to distinguish "not ready yet, come back" from "never
        coming" and from "this store does not serve the CLI at all" — three
        outcomes that a 404 or a 503 would collapse into one. The per-platform
        ``state`` carries that, so the document is always available to read.
        """
        return JSONResponse(
            content=service.manifest(),
            headers={
                "Cache-Control": "no-cache",
                "Vary": VARY_HEADER,
            },
        )

    # ----------------------------------------------------------------- download

    @app.api_route(
        "/cli/download",
        methods=["GET"],
        tags=[tags],
        operation_id="download_cli",
        summary="Download the CLI artifact for a platform",
        openapi_extra={"x-cli-name": "download-cli"},
        response_class=FileResponse,
    )
    async def download_cli(
        request: Request,
        platform: Optional[str] = Query(
            None,
            description=(
                "Artifact platform id (<goos>-<goarch>). Detected from the "
                "request when omitted."
            ),
        ),
        format: str = Query(
            "raw",
            pattern="^(raw|archive)$",
            description=(
                "`raw` for the bare executable, `archive` for a tar.gz/zip that "
                "preserves the executable bit and carries the licence."
            ),
        ),
    ):
        detection = detect_platform(request.headers, platform)

        # An explicitly requested unknown platform is a 400, never a silent
        # substitution: handing someone a Linux binary because they asked for
        # `darwin-arm64` is worse than telling them no. This is also what makes
        # `platform=../../etc/passwd` a 400 with nothing read (B13, §8.2 #14) —
        # the value is checked against a closed enum before it reaches any path.
        if not is_supported_platform(detection.platform):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_platform",
                    "platform": detection.platform,
                    "supported": list(SUPPORTED_PLATFORMS),
                },
            )

        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            return Response(
                status_code=429,
                content='{"detail":"rate_limited"}',
                media_type="application/json",
                headers={"Retry-After": str(_RATE_WINDOW_SECONDS)},
            )

        entry = service.resolve(detection.platform)

        if entry.state == STATE_PREPARING:
            # Retry-After instead of queueing: holding the connection open would
            # tie up a worker for a build, and the client can ask again.
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "preparing",
                    "platform": detection.platform,
                    "message": (
                        "This platform's CLI artifact is still being prepared. "
                        "Try again shortly."
                    ),
                },
                headers={"Retry-After": "10", "Vary": VARY_HEADER},
            )

        if entry.state != STATE_READY:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": entry.reason or "unavailable",
                    "platform": detection.platform,
                },
                headers={"Vary": VARY_HEADER},
            )

        if format == "archive":
            path = service.archive_path(detection.platform)
            filename = entry.archive_filename or path.name
            etag = entry.archive_sha256 or entry.sha256
            media_type = (
                "application/zip"
                if detection.platform.startswith("windows-")
                else "application/gzip"
            )
        else:
            path = service.artifact_path(detection.platform, entry.filename)
            filename = entry.filename
            etag = entry.sha256
            media_type = "application/octet-stream"

        if not path.is_file():
            # The manifest and the disk disagree — the dist dir was cleaned
            # underneath us, or another replica is mid-write. Truthful 503: a
            # background re-preparation will fix it.
            logger.warning(
                "Manifest advertises %s for %s but the file is missing",
                path,
                detection.platform,
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "preparing", "platform": detection.platform},
                headers={"Retry-After": "10", "Vary": VARY_HEADER},
            )

        headers = {
            # Quoted per RFC 7232; an unquoted ETag is ignored by some caches.
            "ETag": f'"{etag}"',
            "Cache-Control": _CACHE_CONTROL,
            "Vary": VARY_HEADER,
            "X-SBS-Platform-Detection": detection.source,
            "X-SBS-CLI-Version": service.cli_version,
        }

        # Conditional GET before touching the file: a repeat download of a 32 MB
        # artifact should cost one round trip, not 32 MB (§7.3).
        if_none_match = request.headers.get("if-none-match", "")
        if etag and etag in if_none_match:
            return Response(status_code=304, headers=headers)

        if not limiter.acquire_slot():
            return Response(
                status_code=503,
                content='{"detail":"too_many_downloads"}',
                media_type="application/json",
                headers={"Retry-After": "5", **headers},
            )
        try:
            # FileResponse handles Range, If-Range and Last-Modified itself, and
            # sets content-length — which is what makes a HEAD answer identical
            # to the GET's headers with no body (§8.2 #13).
            return FileResponse(
                path=str(path),
                media_type=media_type,
                filename=filename,
                headers=headers,
            )
        finally:
            # Released immediately rather than after the body is sent: Starlette
            # streams the file after this function returns, so holding the slot
            # would require wrapping the response. The cap therefore limits
            # download *starts* per instant, which is what the amplification
            # concern is actually about.
            limiter.release_slot()

    # HEAD on the same path, as a separate registration.
    #
    # It has to exist: the ACL audit requires every method on a route to be
    # allow-listed (the reason /ui lists HEAD today), and a client checking size
    # or ETag before committing to a 32 MB transfer should not have to GET.
    #
    # Separate rather than `methods=["GET", "HEAD"]` because FastAPI emits one
    # OpenAPI operation per method and both would carry operation_id
    # `download_cli` — a duplicate-operation-id warning, an ambiguous generated
    # SDK, and an `x-cli-name` collision in the CLI surface. Excluded from the
    # schema for the same reason; the GET is the documented operation.
    #
    # Starlette returns the GET's headers with no body for a HEAD, so delegating
    # keeps the two answers identical by construction instead of by duplication.
    @app.head("/cli/download", include_in_schema=False)
    async def head_cli_download(
        request: Request,
        platform: Optional[str] = Query(None),
        format: str = Query("raw", pattern="^(raw|archive)$"),
    ):
        return await download_cli(request, platform=platform, format=format)

    # ---------------------------------------------------------- install scripts

    @app.get("/cli/install.sh", include_in_schema=False)
    async def cli_install_sh(request: Request) -> Response:
        try:
            url = _effective_public_url(request, service)
        except UnacceptableURL as exc:
            # Refuse to emit rather than emit something dangerous (§5.7). A
            # generated script is executed by `sh` on the user's machine, so an
            # unvalidated URL in it is remote code execution.
            logger.error("Refusing to generate install.sh: %s", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "public_url_unavailable",
                    "message": (
                        "This store cannot generate an install script because its "
                        "public URL could not be determined safely. Set "
                        "SBS_PUBLIC_URL."
                    ),
                },
            )
        return Response(
            content=render_install_sh(url, artifact_checksums(service)),
            media_type="text/x-shellscript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/cli/install.ps1", include_in_schema=False)
    async def cli_install_ps1(request: Request) -> Response:
        try:
            url = _effective_public_url(request, service)
        except UnacceptableURL as exc:
            logger.error("Refusing to generate install.ps1: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "public_url_unavailable"},
            )
        return Response(
            content=render_install_ps1(url, artifact_checksums(service)),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache"},
        )

    # ------------------------------------------------------------------ licence

    @app.get("/cli/license", include_in_schema=False)
    async def cli_license() -> Response:
        """restish's MIT licence.

        We redistribute a restish-derived binary, so its licence has to travel
        with it. Served here as well as bundled in every archive, because a raw
        download carries no second file.
        """
        candidates = [
            service.settings.artifacts_dir / "LICENSE.restish",
            service.dist_dir / "LICENSE.restish",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return PlainTextResponse(
                    candidate.read_text(encoding="utf-8"),
                    headers={"Cache-Control": "public, max-age=3600"},
                )
        return PlainTextResponse(
            "The embedded restish engine is distributed under the MIT License.\n"
            "See https://github.com/rest-sh/restish/blob/main/LICENSE.md\n",
            status_code=200,
            headers={"Cache-Control": "no-cache"},
        )


# --------------------------------------------------------------------------- #
# Install-script generation
# --------------------------------------------------------------------------- #
#
# The URL is **validated, not escaped**, before it reaches these templates
# (§5.7). It is then single-quoted anyway, as a second line of defence. The
# grammar admits no quote, backtick, dollar, semicolon, pipe or whitespace, so a
# value that passed cannot break out of the quoting — but relying on only one of
# the two would be relying on the other never having a bug.


def _checksum_case_sh(checksums: dict[str, str]) -> str:
    """A shell `case` mapping platform id to expected sha256.

    Inlined rather than parsed out of the manifest at install time (§5.7: "the
    URL *and expected sha256* inlined"). The first version of this script grepped
    the manifest JSON and took the first 64-hex string it found — which is
    whichever platform sorts first, not the one being installed. It would have
    compared `darwin-amd64`'s hash against a `linux-amd64` download and refused
    every valid install. Shell has no JSON parser and `jq` is not on a minimal
    host, so the right fix is for the server, which already knows every hash, to
    emit the mapping.

    Platform ids come from the closed SUPPORTED_PLATFORMS enum and the digests are
    hex from hashlib, so neither can carry shell metacharacters.
    """
    lines = []
    for platform in SUPPORTED_PLATFORMS:
        digest = checksums.get(platform, "")
        if digest:
            lines.append(f"    {platform}) expected='{digest}' ;;")
    lines.append("    *) expected='' ;;")
    return "\n".join(lines)


def render_install_sh(public_url: str, checksums: Optional[dict] = None) -> str:
    """The POSIX install script served at /cli/install.sh."""
    url = validate_public_url(public_url)
    checksum_case = _checksum_case_sh(checksums or {})
    return f"""#!/bin/sh
# Install the Skillberry Store CLI (sbs) from {url}
#
# Usage:  curl -fsSL {url}/cli/install.sh | sh
#
# Override the install directory with SBS_INSTALL_DIR.
set -eu

STORE_URL='{url}'
INSTALL_DIR="${{SBS_INSTALL_DIR:-$HOME/.local/bin}}"

# `uname -sm` is exact, unlike the User-Agent sniffing a browser download has to
# fall back on -- it cannot tell Apple Silicon from Intel. So the script always
# sends an explicit ?platform=.
os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
    Linux)  goos=linux ;;
    Darwin) goos=darwin ;;
    *)
        echo "sbs: unsupported operating system: $os" >&2
        echo "  On Windows, use: irm $STORE_URL/cli/install.ps1 | iex" >&2
        exit 1 ;;
esac

case "$arch" in
    x86_64|amd64)   goarch=amd64 ;;
    arm64|aarch64)  goarch=arm64 ;;
    *)
        echo "sbs: unsupported architecture: $arch" >&2
        exit 1 ;;
esac

platform="$goos-$goarch"
echo "==> Detected $platform"

tmpdir="$(mktemp -d)"
# Clean up on every exit path, including a failed download -- otherwise a flaky
# network leaves a 32 MB file in /tmp on each attempt.
trap 'rm -rf "$tmpdir"' EXIT INT TERM

echo "==> Downloading sbs for $platform"
if ! curl -fsSL "$STORE_URL/cli/download?platform=$platform&format=raw" -o "$tmpdir/sbs"; then
    echo "sbs: download failed." >&2
    echo "  Check what this store offers: curl -fsS $STORE_URL/cli/manifest" >&2
    exit 1
fi

# Verify before installing anything (B18). The store hands out an executable, so
# an unverified download is an unacceptable trust jump. The expected digests were
# inlined by the store when it generated this script -- per platform, so there is
# no JSON to parse and no chance of comparing against the wrong platform's hash.
case "$platform" in
{checksum_case}
esac

actual=''
if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$tmpdir/sbs" | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$tmpdir/sbs" | cut -d' ' -f1)"
fi

if [ -n "$actual" ]; then
    echo "==> sha256 $actual"
    # A mismatch is fatal. A *missing* expected value means the store had no
    # prepared artifact for this platform when it generated the script, in which
    # case the download above would already have failed -- so this branch only
    # fires in a genuine race, and warning beats refusing.
    if [ -n "$expected" ] && [ "$expected" != "$actual" ]; then
        echo "sbs: checksum mismatch (this store published $expected)." >&2
        echo "  Refusing to install. Re-run to pick up a freshly prepared build." >&2
        exit 1
    fi
    if [ -z "$expected" ]; then
        echo "==> WARNING: this store published no checksum for $platform" >&2
    fi
else
    echo "==> WARNING: no sha256sum/shasum available; skipping verification" >&2
fi

mkdir -p "$INSTALL_DIR"
chmod 0755 "$tmpdir/sbs"
mv "$tmpdir/sbs" "$INSTALL_DIR/sbs"

echo "==> Installed $INSTALL_DIR/sbs"

# A binary the user cannot invoke is not installed, so say so explicitly rather
# than letting `sbs` fail with "command not found" a moment later.
case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *)
        echo ""
        echo "NOTE: $INSTALL_DIR is not on your PATH. Add it with:"
        echo "  export PATH=\\"$INSTALL_DIR:\\$PATH\\""
        ;;
esac

echo ""
echo "This build talks to {url}. Try:"
echo "  sbs list-skills"
"""


def render_install_ps1(public_url: str, checksums: Optional[dict] = None) -> str:
    """The PowerShell install script served at /cli/install.ps1.

    The expected digest is inlined for the same reason as in the POSIX script,
    even though PowerShell *can* parse the manifest properly: one source of truth
    for "what should this be", and the install keeps working if the manifest's
    shape ever changes.
    """
    url = validate_public_url(public_url)
    expected = (checksums or {}).get("windows-amd64", "")
    return f"""# Install the Skillberry Store CLI (sbs) from {url}
#
# Usage:  irm {url}/cli/install.ps1 | iex
#
# Override the install directory with $env:SBS_INSTALL_DIR.
$ErrorActionPreference = 'Stop'

$StoreUrl = '{url}'
$InstallDir = if ($env:SBS_INSTALL_DIR) {{ $env:SBS_INSTALL_DIR }} else {{ "$env:LOCALAPPDATA\\Programs\\sbs" }}

# Only windows-amd64 is built today. On ARM64 Windows the amd64 binary runs under
# emulation, so this is a working answer rather than a wrong one.
$platform = 'windows-amd64'
Write-Host "==> Downloading sbs for $platform"

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {{
    $exe = Join-Path $tmp 'sbs.exe'
    Invoke-WebRequest -Uri "$StoreUrl/cli/download?platform=$platform&format=raw" -OutFile $exe -UseBasicParsing

    # Verify before installing (B18). The digest was inlined by the store when it
    # generated this script.
    $expected = '{expected}'
    $actual = (Get-FileHash -Path $exe -Algorithm SHA256).Hash.ToLower()
    Write-Host "==> sha256 $actual"
    if ($expected -and ($expected.ToLower() -ne $actual)) {{
        throw "checksum mismatch: this store published $expected. Refusing to install."
    }}
    if (-not $expected) {{
        Write-Warning "This store published no checksum for $platform"
    }}

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Move-Item -Force -Path $exe -Destination (Join-Path $InstallDir 'sbs.exe')
    Write-Host "==> Installed $InstallDir\\sbs.exe"

    if (-not ($env:PATH -split ';' | Where-Object {{ $_ -eq $InstallDir }})) {{
        Write-Host ""
        Write-Host "NOTE: $InstallDir is not on your PATH. Add it with:"
        Write-Host "  [Environment]::SetEnvironmentVariable('PATH', \\"$InstallDir;`$env:PATH\\", 'User')"
    }}

    Write-Host ""
    Write-Host "This build talks to {url}. Try:"
    Write-Host "  sbs list-skills"
}}
finally {{
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}}
"""
