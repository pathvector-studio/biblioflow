"""Bibliographic metadata is filled in for PDFs without a clean identifier in their text layer.

Covers browser "print to PDF" files (letter-spaced text, ScienceDirect download name as the title),
identifiers in the file name, and the title-search fallback.
"""

import pytest

from app.services.metadata.identifiers import compact_letter_spacing, find_pii, strip_download_suffix
from app.services.pdf import inspect_pdf_isolated
from tests.helpers import make_meta
from tests.pdfgen import make_pdf
from tests.test_upload import upload

TITLE = "An optimization model for identifying backbone networks"
PII = "S0142061525003874"
SD_NAME = f"{TITLE} - 1-s2.0-{PII}-main.pdf"


def spaced(s: str) -> str:
    return " ".join(s.replace(" ", ""))


# ---------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (SD_NAME, TITLE),
        (f"{TITLE} – 1-s2.0-{PII}-main", TITLE),
        (f"1-s2.0-{PII}-main.pdf", ""),
        ("Plain Title", "Plain Title"),
        ("draft.pdf", "draft.pdf"),  # left alone: the caller rejects it as a junk title
    ],
)
def test_strip_download_suffix(raw, want):
    assert strip_download_suffix(raw) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (SD_NAME, PII),
        ("PII: S0142-0615(25)00387-4", PII),
        ("journal ref S01420615250038745", None),  # too many digits
        ("nothing here", None),
    ],
)
def test_find_pii(text, want):
    assert find_pii(text) == want


def test_compact_letter_spacing_only_touches_spaced_lines():
    text = "C o n t e n t s l i s t s a v a i l a b l e\nA normal line of text"
    assert compact_letter_spacing(text) == "Contentslistsavailable\nA normal line of text"


async def test_letter_spaced_doi_is_found(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf([spaced("https://doi.org/10.1000/own.9")]))
    info = await inspect_pdf_isolated(f)
    assert info.doi == "10.1000/own.9"


async def test_pii_from_pdf_title(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf(["body"], title=SD_NAME))
    info = await inspect_pdf_isolated(f)
    assert info.pii == PII and info.title == TITLE


# ---------------------------------------------------------------- upload
def sd_meta():
    return make_meta("10.1016/j.ijepes.2025.110839", title=TITLE, year=2025)


async def test_print_to_pdf_sciencedirect_file_resolves_via_pii(login, resolver, storage, enqueued):
    """The reported case: Firefox print of a ScienceDirect article (letter-spaced text, PII only in the name)."""
    resolver.items[PII.lower()] = sd_meta()
    _, c = await login("a@example.com")
    data = make_pdf([spaced("Contents lists available at ScienceDirect"), spaced(TITLE)], title=SD_NAME)
    r = await upload(c, data, name=SD_NAME)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["identifier"] == "10.1016/j.ijepes.2025.110839" and p["year"] == 2025 and p["title"] == TITLE


async def test_pii_in_file_name_only(login, resolver, storage, enqueued):
    resolver.items[PII.lower()] = sd_meta()
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["no identifiers on this page"]), name=SD_NAME)
    assert r.status_code == 201 and r.json()["identifier"] == "10.1016/j.ijepes.2025.110839"


async def test_letter_spaced_own_doi_resolves(login, resolver, storage, enqueued):
    resolver.add(make_meta("10.1000/own.9", title="Scalable BGP Routing in Clos Fabrics"))
    _, c = await login("a@example.com")
    data = make_pdf([spaced("Scalable BGP Routing in Clos Fabrics"), spaced("doi:10.1000/own.9")])
    r = await upload(c, data)
    assert r.status_code == 201 and r.json()["identifier"] == "10.1000/own.9"


async def test_letter_spaced_cited_doi_is_still_rejected(login, resolver, storage, enqueued):
    resolver.add(make_meta("10.1000/cited.9", title="Quantum Chromodynamics Lattice Survey"))
    _, c = await login("a@example.com")
    data = make_pdf([spaced("An Entirely Different Study of Networks"), spaced("doi:10.1000/cited.9")])
    r = await upload(c, data)
    assert r.status_code == 201 and r.json()["identifier_type"] == "internal"


async def test_arxiv_id_in_file_name(login, resolver, storage, enqueued):
    resolver.add(make_meta("2405.12345", title="Scalable BGP Routing in Clos Fabrics"))
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["Scalable BGP Routing in Clos Fabrics"]), name="2405.12345v2.pdf")
    assert r.status_code == 201 and r.json()["identifier"] == "2405.12345"


async def test_title_search_accepts_near_identical_title(login, resolver, storage, enqueued):
    meta = make_meta("10.1000/bytitle.1", title="Scalable BGP Routing in Clos Fabrics")
    resolver.items["scalable bgp routing in clos fabrics."] = meta
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["nothing"], title="Scalable BGP Routing in Clos Fabrics."))
    assert r.status_code == 201 and r.json()["identifier"] == "10.1000/bytitle.1"


async def test_title_search_rejects_different_title(login, resolver, storage, enqueued):
    meta = make_meta("10.1000/other.1", title="Scalable BGP Routing")
    resolver.items["scalable bgp routing in clos fabrics with ecmp"] = meta
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["nothing"], title="Scalable BGP Routing in Clos Fabrics with ECMP"))
    assert r.status_code == 201 and r.json()["identifier_type"] == "internal"


async def test_short_titles_are_not_searched(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    await upload(c, make_pdf(["nothing"], title="A Study of Things"))
    assert "A Study of Things" not in resolver.calls
