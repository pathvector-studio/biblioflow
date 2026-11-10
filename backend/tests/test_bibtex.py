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


def test_doi_and_url_are_verbatim() -> None:
    # Springer chapter DOIs have underscores; "\_" in a doi / url field breaks the link.
    out = make_bibtex(_meta(doi="10.1007/978-3-030-58452-8_13", identifier="10.1007/978-3-030-58452-8_13"), "k")
    assert "doi = {10.1007/978-3-030-58452-8_13}" in out
    assert "url = {https://doi.org/10.1007/978-3-030-58452-8_13}" in out
    arxiv = make_bibtex(_meta(identifier="cs/0101_1", identifier_type=IdentifierType.arxiv, arxiv_id=None, doi="10.1/a%b"), "k")
    assert "eprint = {cs/0101_1}" in arxiv and "doi = {10.1/a%b}" in arxiv
    assert "{" not in make_bibtex(_meta(doi="10.1/a}b{c", venue=None), "k").split("doi = {")[1].split("}")[0]


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False
    return depth == 0


def test_braces_in_a_title_never_unbalance_the_entry() -> None:
    # BibTeX counts braces even after a backslash: "\}" would close the field early.
    for title in ("Unbalanced } brace { in title", "Only open {", "Only close }", "Fine {nested {pair}} here"):
        out = make_bibtex(_meta(title=title), "k")
        assert _balanced(out), title
    assert r"\textbraceright{} brace \textbraceleft{}" in make_bibtex(_meta(title="Unbalanced } brace { in title"), "k")
    assert r"Fine \{nested \{pair\}\} here" in make_bibtex(_meta(title="Fine {nested {pair}} here"), "k")


def test_names_that_bibtex_would_split() -> None:
    authors = [
        Author(family="World Health Organization"),
        Author(family="Barnes and Noble"),
        Author(family="Müller"),
        Author(family="van der Berg", given="Jan"),
        Author(family="Smith, Jr.", given="A and B"),
    ]
    out = make_bibtex(_meta(authors=authors), "k")
    assert (
        "author = {{World Health Organization} and {Barnes and Noble} and Müller and van der Berg, Jan and {Smith, Jr.}, {A and B}}"
        in out
    )
