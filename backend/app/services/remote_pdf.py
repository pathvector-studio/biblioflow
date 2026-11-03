"""Download a paper PDF from a URL / DOI / arXiv id / PII (SSRF-safe, size-capped)."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from app.core.net import BlockedDestination, safe_stream, validate_url
from app.services.metadata import identifiers
from app.services.storage.files import StagedFile, TooLarge, receive_stream

CHUNK = 64 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
_ARXIV_URL = re.compile(r"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/(?P<id>[^?#\s]+?)(?:\.pdf)?/?(?:[?#].*)?$", re.I)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z_:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_PDF_META_NAMES = ("citation_pdf_url", "bepress_citation_pdf_url", "eprints.document_url")


class RemoteFetchError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Source:
    url: str
    identifier_hint: str | None  # arXiv id / DOI / PII recognised in the input, if any


def resolve_source(raw: str) -> Source:
    """Map user input to a download URL plus an identifier hint for metadata resolution.

    Accepts a direct PDF URL, a landing-page URL, ``arxiv.org/abs/...``, a DOI, an arXiv id or a PII.
    """
    raw = raw.strip()
    parsed = identifiers.parse_query(raw)
    hint = parsed.value if parsed.kind != "title" else None
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        m = _ARXIV_URL.match(raw)
        if m and parsed.kind == "arxiv":
            return Source(f"https://arxiv.org/pdf/{parsed.value}", hint)
        return Source(raw, hint)
    if parsed.kind == "arxiv":
        return Source(f"https://arxiv.org/pdf/{parsed.value}", hint)
    if parsed.kind == "doi":
        return Source(f"https://doi.org/{quote(parsed.value, safe='/:;()._-')}", hint)
    if parsed.kind == "pii":
        return Source(f"https://www.sciencedirect.com/science/article/pii/{parsed.value}", hint)
    raise RemoteFetchError(422, "Expected a URL, DOI, arXiv id or PII")


def find_pdf_link(page: str, base_url: str) -> str | None:
    """Return the absolute ``citation_pdf_url`` (Highwire/Google Scholar meta tag) of a landing page."""
    for tag in _META_TAG.findall(page):
        attrs = {k.lower(): (a or b or c) for k, a, b, c in _ATTR.findall(tag)}
        if attrs.get("name", "").lower() in _PDF_META_NAMES and attrs.get("content"):
            link = html.unescape(attrs["content"]).strip()
            try:
                return str(httpx.URL(base_url).join(link))
            except httpx.InvalidURL:
                return None
    return None


def _is_html(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    return ctype in ("text/html", "application/xhtml+xml")


def filename_for(url: str) -> str:
    name = PurePosixPath(httpx.URL(url).path).name or "download.pdf"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


async def fetch_pdf(
    client_factory: Callable[[], httpx.AsyncClient], url: str, *, root, max_bytes: int
) -> tuple[StagedFile, str]:
    """Stream the PDF at ``url`` into the library's ``.incoming`` dir.

    If the URL serves an HTML landing page, follow its ``citation_pdf_url`` once. Every hop (incl.
    redirects) is re-validated by ``safe_stream``. Raises ``RemoteFetchError`` / ``BlockedDestination``.
    """
    try:
        validate_url(url)
    except (BlockedDestination, ValueError):
        raise BlockedDestination("URL not allowed") from None
    async with client_factory() as client:
        for hop in range(2):
            async with safe_stream(client, url) as resp:
                if not 200 <= resp.status_code < 300:
                    raise RemoteFetchError(502, f"Remote server returned HTTP {resp.status_code}")
                final_url = str(resp.url)
                if _is_html(resp):
                    if hop:
                        raise RemoteFetchError(415, "Landing page link did not lead to a PDF")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(CHUNK):
                        body += chunk
                        if len(body) > MAX_HTML_BYTES:
                            break
                    link = find_pdf_link(body.decode("utf-8", "replace"), final_url)
                    if not link:
                        raise RemoteFetchError(415, "No PDF found at URL (landing page without a PDF link)")
                    url = link
                    continue
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise TooLarge("declared length too large")
                staged = await receive_stream(resp.aiter_bytes(CHUNK), root, max_bytes)
                return staged, filename_for(final_url)
    raise RemoteFetchError(415, "No PDF found at URL")
