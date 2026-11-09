import json

from app.models.paper import IdentifierType
from app.schemas.metadata import Author
from app.services import export as ex


def rec(**kw):
    base = dict(
        identifier="2405.12345", identifier_type=IdentifierType.arxiv, title="Scalable BGP & 100% fabrics_v2",
        authors=[Author(family="Müller", given="Zoë"), Author(family="Chan")], citekey="muller2024scalable",
        arxiv_id="2405.12345", year=2024, abstract="An abstract.\nSecond line.",
    )
    base.update(kw)
    return ex.ExportRecord(**base)


def test_bibtex_generated_escapes_latex_and_keeps_unicode():
    out = ex.to_bibtex([rec()])
    assert out.startswith("@misc{muller2024scalable,")
    assert r"Scalable BGP \& 100\% fabrics\_v2" in out
    assert "Müller, Zoë and Chan" in out
    assert "eprint = {2405.12345}" in out


def test_bibtex_stored_entry_gets_current_citekey():
    stored = "@article{oldkey,\n  title = {T},\n  volume = {3}\n}\n"
    out = ex.to_bibtex([rec(bibtex=stored, citekey="newkey")])
    assert out.startswith("@article{newkey,") and "volume = {3}" in out and "oldkey" not in out


def test_citekeys_unique_and_stable():
    recs = [rec(), rec(identifier="x", identifier_type=IdentifierType.internal), rec(bibtex="@misc{muller2024scalable,\n}")]
    keys = ex.unique_keys(recs)
    assert keys == ["muller2024scalable", "muller2024scalablea", "muller2024scalableb"]
    out = ex.to_bibtex(recs)
    for k in keys:
        assert f"{{{k}," in out
    assert len(set(ex.unique_keys([rec(citekey=None)] * 3))) == 3


def test_missing_citekey_falls_back_to_generated():
    assert ex.unique_keys([rec(citekey=None)]) == ["muller2024scalable"]


def test_many_collisions_use_multi_letter_suffix():
    keys = ex.unique_keys([rec() for _ in range(28)])
    assert keys[26] == "muller2024scalablez" and keys[27] == "muller2024scalableaa" and len(set(keys)) == 28


def test_ris():
    out = ex.to_ris([rec(doi="10.1/x", venue="Journal", identifier_type=IdentifierType.doi, arxiv_id=None)])
    lines = out.splitlines()
    assert lines[0] == "TY  - JOUR" and "ID  - muller2024scalable" in lines
    assert "AU  - Müller, Zoë" in lines and "AU  - Chan" in lines
    assert "PY  - 2024" in lines and "JO  - Journal" in lines and "DO  - 10.1/x" in lines
    assert "UR  - https://doi.org/10.1/x" in lines
    assert "AB  - An abstract. Second line." in lines
    assert "ER  - " in lines


def test_ris_arxiv_url():
    assert "UR  - https://arxiv.org/abs/2405.12345" in ex.to_ris([rec()])


def test_csl_json_structure():
    other = rec(year=None, authors=[], identifier_type=IdentifierType.doi, arxiv_id=None, doi="10.1/y", venue="V")
    a, b = json.loads(ex.to_csl_json([rec(), other]))
    assert a["id"] == "muller2024scalable" and a["type"] == "article" and a["title"].startswith("Scalable")
    assert a["author"] == [{"family": "Müller", "given": "Zoë"}, {"family": "Chan"}]
    assert a["issued"] == {"date-parts": [[2024]]}
    assert a["URL"] == "https://arxiv.org/abs/2405.12345" and a["abstract"].startswith("An abstract")
    assert b["type"] == "article-journal" and b["DOI"] == "10.1/y" and b["container-title"] == "V"
    assert "issued" not in b and b["author"] == [] and b["id"] != a["id"]
