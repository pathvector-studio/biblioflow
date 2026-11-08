"""Parsers for BibTeX, RIS, CSL JSON and Zotero RDF (pure functions: no database needed)."""

from pathlib import Path

import pytest

from app.models.paper import IdentifierType
from app.services.import_apply import internal_identifier, to_metadata
from app.services.importers import ImportFormatError, detect_format, parse_file
from app.services.importers import bibtex as bib

FIX = Path(__file__).parent / "fixtures" / "import"


def load(name):
    return parse_file((FIX / name).read_bytes(), name)


def by_title(result, start):
    return next(i for i in result.items if i.title.startswith(start))


# ------------------------------------------------------------------ BibTeX
def test_bibtex_items_and_problems():
    r = load("library.bib")
    assert r.format == "bibtex"
    assert [i.citekey for i in r.items] == ["vaswani2017attention", "he2016deep", "mueller2023accents", "plain2021"]
    assert [p.ref for p in r.problems] == ["broken2020"]  # the broken entry does not swallow the next one


def test_bibtex_fields_tags_notes_file_and_raw_entry():
    r = load("library.bib")
    v = by_title(r, "Attention")
    assert v.title == "Attention Is All You Need"  # protective braces dropped
    assert v.arxiv_id == "1706.03762" and v.doi is None and v.year == 2017
    assert v.venue == "Advances in Neural Information Processing Systems"
    assert v.tags == ["transformers", "attention", "nlp"]  # comma and semicolon separated
    assert v.notes == ["Read in the 2024 reading group"]
    assert v.attachments == ["files/12/Vaswani_2017.pdf"]  # "Description:path:mime" -> path
    assert v.abstract.startswith("The dominant sequence") and "\n" in v.abstract
    assert v.bibtex.startswith("@inproceedings{vaswani2017attention,") and "keywords" in v.bibtex  # original text kept
    assert v.item_type == "inproceedings"
    he = by_title(r, "Deep Residual")
    assert he.doi == "10.1109/cvpr.2016.90" and he.venue == "Proceedings of CVPR"  # @string expanded


def test_bibtex_latex_accents_and_braced_names():
    m = by_title(load("library.bib"), "Na")
    assert m.title == "Naïve Routing in BGP & Clos Fabrics"
    assert (m.authors[0].family, m.authors[0].given) == ("Müller", "Zoë")
    assert m.authors[1].given == "José"
    assert [a.family for a in m.authors][2:] == ["World Health Organization"]  # {braced} -> one name
    assert m.venue == "Journal of Café Networking"
    assert m.notes == ["Second note"]  # annote
    assert m.attachments == ["../../etc/passwd"]  # kept as a candidate; never opened here


def test_bibtex_authors_and_file_field_helpers():
    assert [a.family for a in bib.parse_authors("Barnes and Noble and {Barnes and Noble Inc.}")] == [
        "Barnes", "Noble", "Barnes and Noble Inc.",
    ]
    a = bib.parse_authors("Ludwig van Beethoven")[0]
    assert (a.family, a.given) == ("van Beethoven", "Ludwig")
    assert bib.parse_file_field("a/b.pdf;:c/d.pdf:application/pdf") == ["a/b.pdf", "c/d.pdf"]
    assert bib.parse_file_field(r"C:\Users\x\p.pdf") == [r"C:\Users\x\p.pdf"]
    assert bib.parse_file_field("") == []


def test_bibtex_no_entries_is_an_error():
    with pytest.raises(ImportFormatError):
        parse_file(b"@comment{nothing}\n", "x.bib")


def test_bibtex_missing_title_is_a_problem():
    r = parse_file(b"@article{k1, author={A, B}, year={2020}}\n@article{k2, title={Ok}}", "x.bib")
    assert [i.citekey for i in r.items] == ["k2"]
    assert r.problems[0].ref == "k1" and r.problems[0].reason == "missing title"


# ------------------------------------------------------------------ RIS
def test_ris_mendeley_style():
    r = load("mendeley.ris")
    assert r.format == "ris" and len(r.items) == 3
    assert [p.reason for p in r.problems] == ["missing title"]
    s = by_title(r, "Mastering")
    assert s.doi == "10.1038/nature16961" and s.year == 2016 and s.venue == "Nature"
    assert [a.family for a in s.authors] == ["Silver", "Huang", "Maddison"]
    assert s.tags == ["reinforcement learning", "games"] and s.notes == ["Seminal paper"]
    assert s.attachments == ["file:///home/example/Mendeley/silver.pdf"]  # L1 kept; the https UR is not a path
    assert s.citekey == "silver2016"
    assert s.abstract.endswith("for artificial intelligence.") and "\n" in s.abstract  # continuation line joined
    adam = by_title(r, "Adam")
    assert adam.arxiv_id == "1412.6980" and adam.doi is None and adam.citekey is None
    nid = by_title(r, "A Paper With")
    assert nid.year == 2019 and nid.venue == "Workshop on Examples" and nid.authors[0].family == "Roe"  # A1 / Y1 / T2


def test_ris_variants_bom_crlf_single_space_and_no_er():
    data = "\ufeffTY - JOUR\r\nTI - One\r\nAU - Doe, J\r\nER - \r\n\r\nTY - JOUR\r\nT1 - Two\r\nKW - a, b".encode()
    r = parse_file(data, "x.ris")
    assert [i.title for i in r.items] == ["One", "Two"] and r.items[1].tags == ["a", "b"]


# ------------------------------------------------------------------ CSL JSON
def test_csljson_items():
    r = load("library.csl.json")
    assert r.format == "csljson" and len(r.items) == 2
    assert {p.reason for p in r.problems} == {"missing title", "not a JSON object"}
    d = by_title(r, "Deep learning")
    assert d.doi == "10.1038/nature14539" and d.year == 2015 and d.venue == "Nature"
    assert [(a.family, a.given) for a in d.authors] == [("LeCun", "Yann"), ("Bengio", "Yoshua"), ("Hinton", "Geoffrey")]
    assert d.tags == ["deep learning", "review"] and d.citekey is None  # a URL id is not a citekey
    b = by_title(r, "BERT")
    assert b.arxiv_id == "1810.04805" and b.year == 2018 and b.citekey == "devlin2019bert"


def test_csljson_wrapped_in_object_and_invalid():
    assert len(parse_file(b'{"items":[{"title":"X","issued":{"date-parts":[["2020"]]}}]}', "a.json").items) == 1
    with pytest.raises(ImportFormatError):
        parse_file(b"[1, 2", "a.json")
    with pytest.raises(ImportFormatError):
        parse_file(b"[]", "a.json")


# ------------------------------------------------------------------ Zotero RDF
def test_zotero_rdf_items_collections_notes_attachments():
    r = load("zotero.rdf")
    assert r.format == "zotero_rdf" and len(r.items) == 3
    assert [p.reason for p in r.problems] == ["missing title"]  # the item without a title; attachments / notes are not items
    d = by_title(r, "Deep learning")
    assert d.doi == "10.1038/nature14539" and d.year == 2015 and d.venue == "Nature"
    assert [a.family for a in d.authors] == ["LeCun", "Bengio"]
    assert d.tags == ["deep learning", "review"]
    assert d.collections == [["Networking", "BGP"], ["Reading list"]]  # nested path, plus a top-level collection
    assert d.notes == ["Great survey.\n\n- read intro"]  # HTML memo -> text
    assert d.attachments == ["files/2/LeCun_2015.pdf"]
    adam = by_title(r, "Adam")
    assert adam.arxiv_id == "1412.6980" and adam.collections == [["Networking", "BGP"]]
    book = by_title(r, "A Book")  # unknown item types are imported as papers
    assert book.item_type == "book" and book.collections == [["Networking"]] and book.year == 2001


def test_zotero_rdf_rejects_dtd_and_entities():
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
    with pytest.raises(ImportFormatError):
        parse_file(evil, "x.rdf")
    with pytest.raises(ImportFormatError):
        parse_file(b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'><unclosed>", "x.rdf")


def test_zotero_rdf_collection_cycle_terminates():
    xml = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:z="http://www.zotero.org/namespaces/export#"
      xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:bib="http://purl.org/net/biblio#">
      <bib:Article rdf:about="#i1"><z:itemType>journalArticle</z:itemType><dc:title>T</dc:title></bib:Article>
      <z:Collection rdf:about="#c1"><dc:title>A</dc:title><dcterms:hasPart rdf:resource="#c2"/><dcterms:hasPart rdf:resource="#i1"/></z:Collection>
      <z:Collection rdf:about="#c2"><dc:title>B</dc:title><dcterms:hasPart rdf:resource="#c1"/></z:Collection>
    </rdf:RDF>"""
    # Both collections are nested in each other, so neither is a root: the item simply has no collection.
    assert parse_file(xml, "x.rdf").items[0].collections == []


# ------------------------------------------------------------------ detection
def test_detect_format_by_content_then_extension():
    assert detect_format("@article{a, title={x}}", "whatever.txt") == "bibtex"
    assert detect_format("TY  - JOUR\nER  - ", "a.bib") == "ris"  # content wins over the extension
    assert detect_format('[{"title":"x"}]') == "csljson"
    assert detect_format('<?xml version="1.0"?><rdf:RDF/>') == "zotero_rdf"
    assert detect_format("no idea", "refs.ris") == "ris"
    with pytest.raises(ImportFormatError):
        detect_format("no idea", "refs.txt")


def test_decode_latin1_and_utf16():
    r = parse_file("@article{a, title={Caf\u00e9}}".encode("latin-1"), "a.bib")
    assert r.items[0].title == "Café"
    r = parse_file("TY  - JOUR\nTI  - \u00dcber\nER  - \n".encode("utf-16"), "a.ris")
    assert r.items[0].title == "Über"


# ------------------------------------------------------------------ identity
def test_to_metadata_precedence_and_deterministic_internal_id():
    items = {i.title[:4]: i for i in load("library.bib").items}
    assert to_metadata(items["Atte"]).identifier_type == IdentifierType.arxiv
    assert to_metadata(items["Deep"]).identifier_type == IdentifierType.doi
    plain = items["A St"]
    m = to_metadata(plain)
    assert m.identifier_type == IdentifierType.internal and m.identifier.startswith("BF-") and len(m.identifier) == 13
    assert internal_identifier(plain) == m.identifier == internal_identifier(load("library.bib").items[3])
    plain.year = 2022
    assert internal_identifier(plain) != m.identifier
