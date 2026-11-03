from __future__ import annotations

from app.models.paper import IdentifierType
from app.schemas.metadata import Author, PaperMetadata
from app.services.metadata.bibtex import citekey_base, make_bibtex


def _meta(**kw) -> PaperMetadata:
    base = dict(
        identifier="10.1145/3651890",
        identifier_type=IdentifierType.doi,
        doi="10.1145/3651890",
        title="Towards the Scalable BGP Routing",
        authors=[Author(family="Müller-Łukasz", given="Ada"), Author(family="Turing", given="Alan")],
        year=2024,
        venue="PACMNET",
        source="crossref",
    )
    base.update(kw)
    return PaperMetadata(**base)


def test_citekey() -> None:
    assert citekey_base(_meta()) == "mullerukasz2024scalable"
    assert citekey_base(_meta(year=None)) == "mullerukaszndscalable"
    assert citekey_base(_meta(authors=[], title="The of")) == "2024"
    assert citekey_base(_meta(authors=[], year=None, title="the")) == "paper"
    assert len(citekey_base(_meta(title="x" * 200))) <= 60


def test_article_entry() -> None:
    out = make_bibtex(_meta(), "k2024")
    assert out == (
        "@article{k2024,\n"
        "  title = {Towards the Scalable BGP Routing},\n"
        "  author = {Müller-Łukasz, Ada and Turing, Alan},\n"
        "  year = {2024},\n"
        "  journal = {PACMNET},\n"
        "  doi = {10.1145/3651890},\n"
        "  url = {https://doi.org/10.1145/3651890}\n"
        "}\n"
    )


def test_arxiv_entry() -> None:
    m = _meta(identifier="2405.12345", identifier_type=IdentifierType.arxiv, arxiv_id="2405.12345", doi=None)
    out = make_bibtex(m, "k")
    assert out.startswith("@misc{k,")
    assert "eprint = {2405.12345}" in out
    assert "archivePrefix = {arXiv}" in out
    assert "url = {https://arxiv.org/abs/2405.12345}" in out
    assert "doi" not in out


def test_misc_without_venue() -> None:
    assert make_bibtex(_meta(venue=None), "k").startswith("@misc{k,")


def test_escaping_and_control_chars() -> None:
    out = make_bibtex(_meta(title="50% of A&B_c #1 $x$ ~y ^z \\q {w}\x00\n"), "k")
    assert r"title = {50\% of A\&B\_c \#1 \$x\$ \textasciitilde{}y \textasciicircum{}z \textbackslash{}q \{w\}}" in out
    assert "\x00" not in out
    assert make_bibtex(_meta(), "k") == make_bibtex(_meta(), "k")
