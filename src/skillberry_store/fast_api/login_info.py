"""Serving the operator's login message on the UI entry point.

The message itself is resolved and sanitized once, at config load, into
``AccessControlConfig.login_info``. This module owns the *serving* half: the
``<meta>`` tag it becomes, where that tag is injected, and the response type
that replaces ``FileResponse`` when it is.

Why serve-time injection rather than a Vite build input: the runtime image
cannot rebuild the bundle (``DEPLOY_ONLY`` drops ``ui-build`` from ``make
run``), and the standalone access-control config is realistically mounted or
edited at runtime — so a baked message would be fixed for the life of the
image, stale exactly where the feature matters most. See §3 and §6 of
docs/design/login-info.md.

Everything here is inert when no message is configured: ``LoginInfoPage.build``
returns an instance whose ``response_for_*`` methods answer ``None``, and the
caller keeps serving ``index.html`` through the same ``FileResponse`` as
before, ETag / Last-Modified / Range support intact.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import Response

from skillberry_store.access_control.login_banner import LoginBanner

logger = logging.getLogger(__name__)

# The tag name the SPA looks for. LoginPage.tsx queries this same literal, and
# `test_the_built_bundle_reads_the_meta_name_the_server_writes` pins the pair
# together — nothing else keeps them in step.
LOGIN_INFO_META_NAME = "sbs-login-info"

# The rich banner's payload, injected alongside — never instead of — the plain
# tag above (docs/design/login-banner.md §6). Two tags rather than one because
# they answer different questions: the plain one is the message, the rich one
# is how to present it, and a bundle that cannot read the second still has the
# first to fall back to.
LOGIN_BANNER_META_NAME = "sbs-login-banner"

_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)

_INDEX_FILENAME = "index.html"


def render_login_info_meta(message: str) -> str:
    """Return the ``<meta>`` tag carrying ``message``.

    The operator's text is escaped into an inert attribute value rather than
    into an inline ``<script>``: an attribute cannot execute, while
    ``window.__SBS_LOGIN_INFO__ = "..."`` would put operator text in a
    JavaScript parsing context — a strictly worse position to defend. React
    escapes it a second time when the login page renders it as a text child.
    """
    return (
        f'<meta name="{LOGIN_INFO_META_NAME}" '
        f'content="{html.escape(message, quote=True)}">'
    )


def render_login_banner_meta(banner: LoginBanner) -> str:
    """Return the ``<meta>`` tag carrying the rich banner's JSON payload.

    Same reasoning as :func:`render_login_info_meta`, and for the same reason
    the payload is JSON in an attribute rather than a ``<script
    type="application/json">`` block: ``html.escape`` makes an attribute inert
    in one step, whereas escaping for a script context means worrying about
    ``</script>`` and ``<!--`` inside the data.

    The payload is already validated — every value in it came off an
    allow-list in ``access_control/login_banner.py`` — so this function only
    has to serialize and escape. ``ensure_ascii`` stays on so an emoji in an
    icon or a message survives a bundle served as anything but UTF-8.
    """
    payload = json.dumps(banner.to_dict(), separators=(",", ":"), ensure_ascii=True)
    return (
        f'<meta name="{LOGIN_BANNER_META_NAME}" '
        f'content="{html.escape(payload, quote=True)}">'
    )


def inject_login_info(
    index_html: bytes,
    message: Optional[str] = None,
    banner: Optional[LoginBanner] = None,
) -> bytes:
    """Return ``index_html`` with the login ``<meta>`` tag(s) before ``</head>``.

    Matched case-insensitively on the first occurrence; the Vite-generated
    entry point always has one. HTML with no ``</head>`` is returned unchanged
    with a warning — a banner is not worth failing a page load over.

    A rich banner adds a second tag rather than replacing the first: the plain
    message is the SPA's fallback if the payload is unusable, and the two are
    independent (a banner made only of presentation has no plain text, and a
    plain message has no banner).
    """
    tags = []
    if message:
        tags.append(render_login_info_meta(message))
    if banner:
        tags.append(render_login_banner_meta(banner))
    if not tags:
        return index_html
    tag = "".join(tags)
    text = index_html.decode("utf-8")
    injected, count = _HEAD_CLOSE_RE.subn(lambda m: tag + m.group(0), text, count=1)
    if not count:
        logger.warning(
            "UI entry point has no </head>; serving it without the login "
            "message <meta> tag"
        )
        return index_html
    return injected.encode("utf-8")


def _html_bytes_response(request: Request, body: bytes, cache_control: str) -> Response:
    """Serve pre-rendered HTML bytes, suppressing the body for HEAD.

    ``FileResponse`` handles HEAD itself (``send_header_only``); a plain
    ``Response`` always sends its body, and the ``/ui/{path:path}`` route
    serves GET *and* HEAD. Starlette skips populating its own
    ``content-length`` when the caller supplies one, so a HEAD can report the
    GET body's length without sending it. See §6.2 of
    docs/design/login-info.md.
    """
    headers = {"Cache-Control": cache_control}
    if request.method == "HEAD":
        headers["Content-Length"] = str(len(body))
        return Response(content=b"", media_type="text/html", headers=headers)
    return Response(content=body, media_type="text/html", headers=headers)


class LoginInfoPage:
    """The UI entry point, pre-rendered with the login message injected.

    Built once at startup: the message cannot change without a server restart
    (§4.4 of docs/design/login-info.md), so there is no per-request work.

    ``active`` is False whenever there is nothing to show — no message
    configured, or no entry point on disk — and every ``response_for_*`` method
    then answers ``None``, meaning "you serve this the way you always did".
    """

    def __init__(self, index_path: Path, body: Optional[bytes]) -> None:
        self._index_path = index_path
        self._body = body

    @classmethod
    def build(
        cls,
        ui_root: Path,
        message: Optional[str],
        banner: Optional[LoginBanner] = None,
    ) -> "LoginInfoPage":
        """Render the entry point for ``message``/``banner``, or an inert instance.

        Either is enough to make the page active: a rich banner need not have
        plain text (a bare logo has none), and a plain message need not have a
        banner (``format: plain``, which is the default).
        """
        index_path = (ui_root / _INDEX_FILENAME).resolve()
        if not (message or banner) or not index_path.is_file():
            return cls(index_path, None)
        logger.info(
            "Login %s will be injected into %s",
            "banner" if banner else "message",
            index_path,
        )
        return cls(
            index_path, inject_login_info(index_path.read_bytes(), message, banner)
        )

    @property
    def active(self) -> bool:
        return self._body is not None

    def response_for_asset(
        self, request: Request, asset: Path, cache_control: str
    ) -> Optional[Response]:
        """The injected page when ``asset`` *is* the entry point, else ``None``.

        Matched on the resolved path rather than on an ``.html`` suffix: only
        the entry point has a pre-rendered copy, so any additional HTML file in
        the bundle must keep going through ``FileResponse``.
        """
        if self._body is None or asset != self._index_path:
            return None
        return _html_bytes_response(request, self._body, cache_control)

    def response_for_fallback(
        self, request: Request, cache_control: str
    ) -> Optional[Response]:
        """The injected page for an SPA deep link (``/ui/``, ``/ui/login``).

        Both this and :meth:`response_for_asset` must serve the injected copy:
        covering only one would show the banner on ``/ui/login`` and not on the
        literal ``/ui/index.html``, which is what a bookmark or an ingress
        rewrite sends.
        """
        if self._body is None:
            return None
        return _html_bytes_response(request, self._body, cache_control)
