from __future__ import annotations

import pytest

from app.services.metadata.identifiers import find_identifiers_in_text, parse_query

PII = "S0142061525003874"


@pytest.mark.parametrize(
    "q,expected",
    [
        ("2405.12345", ("arxiv", "2405.12345")),
        ("2405.12345v2", ("arxiv", "2405.12345")),
        ("arXiv:2405.12345", ("arxiv", "2405.12345")),
        ("https://arxiv.org/abs/2405.12345v3", ("arxiv", "2405.12345")),
        ("https://arxiv.org/pdf/2405.12345v1.pdf", ("arxiv", "2405.12345")),
        ("https://arxiv.org/pdf/2405.12345", ("arxiv", "2405.12345")),
        ("hep-th/9901001", ("arxiv", "hep-th/9901001")),
        ("hep-th/9901001v2", ("arxiv", "hep-th/9901001")),
        ("math.GT/0309136", ("arxiv", "math.GT/0309136")),
        ("https://arxiv.org/abs/hep-th/9901001v2", ("arxiv", "hep-th/9901001")),
        ("10.1145/3651890", ("doi", "10.1145/3651890")),
        ("doi:10.1145/3651890", ("doi", "10.1145/3651890")),
        ("https://doi.org/10.1145/3651890", ("doi", "10.1145/3651890")),
        ("http://dx.doi.org/10.1145/3651890", ("doi", "10.1145/3651890")),
        ("10.1002/ABC.123).", ("doi", "10.1002/abc.123")),
        ("10.48550/arXiv.2405.12345", ("arxiv", "2405.12345")),
        (PII, ("pii", PII)),
        ("s0142-0615(25)00387-4", ("pii", PII)),
        (f"https://www.sciencedirect.com/science/article/pii/{PII}", ("pii", PII)),
        (
            f"https://pdf.sciencedirectassets.com/271/main.pdf?X-Amz-Security-Token=FAKE&pii={PII}&X-Amz-Signature=deadbeef",
            ("pii", PII),
        ),
        ("https://example.com/foo?pii=" + PII, ("pii", PII)),
        ("Scalable BGP Routing in Clos Fabrics", ("title", "Scalable BGP Routing in Clos Fabrics")),
        ("1234", ("title", "1234")),
    ],
)
def test_parse_query(q: str, expected: tuple[str, str]) -> None:
    p = parse_query(q)
    assert (p.kind, p.value) == expected


def test_pii_value_never_contains_url_remainder() -> None:
    p = parse_query(f"https://x.sciencedirectassets.com/a.pdf?X-Amz-Signature=sekret&pii={PII}")
    assert "sekret" not in p.value


def test_sciencedirect_host_must_match_exactly() -> None:
    # A look-alike host is not ScienceDirect: its /pii/ path is not taken as an identifier.
    assert parse_query(f"https://www.sciencedirect.com/science/article/pii/{PII}").kind == "pii"
    assert parse_query(f"https://sciencedirect.com/science/article/pii/{PII}").kind == "pii"
    for host in ("evilsciencedirect.com", "sciencedirect.com.example.net", "notsciencedirectassets.com"):
        assert parse_query(f"https://{host}/science/article/pii/{PII}").kind == "title", host


def test_unknown_url_is_title() -> None:
    assert parse_query("https://example.com/paper").kind == "title"


def test_find_in_text() -> None:
    doi, ax = find_identifiers_in_text("arXiv:2405.12345v1 [cs.NI] 3 May 2024\nfoo")
    assert (doi, ax) == (None, "2405.12345")
    doi, ax = find_identifiers_in_text("see doi: 10.1145/3651890.")
    assert (doi, ax) == ("10.1145/3651890", None)
    doi, ax = find_identifiers_in_text("https://doi.org/10.1145/3651890), arXiv:hep-th/9901001v2")
    assert (doi, ax) == ("10.1145/3651890", "hep-th/9901001")
    assert find_identifiers_in_text("nothing here 2024.1") == (None, None)
