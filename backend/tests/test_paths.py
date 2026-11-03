import os
import re

import pytest

from app.services.storage.paths import (
    PathTraversalError,
    build_relative_path,
    generate_internal_identifier,
    resolve_in_library,
    sanitize_identifier,
    slugify_title,
    validate_template,
)

DEFAULT = "{year}/{identifier}_{slug_title}.pdf"
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def test_identifier_examples():
    assert sanitize_identifier("10.1145/3651890") == "10.1145_3651890"
    assert sanitize_identifier("2405.12345") == "2405.12345"
    assert sanitize_identifier("arXiv: 2405.12345v2") == "arXiv__2405.12345v2"


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "a/../..",
        "a\x00b/../../x",
        "..\\..\\windows\\system32",
        "．．／．．／etc／passwd",
        "foo/../../bar",
        "%2e%2e/x",
        "ab\r\ncd",
        "x" * 500,
    ],
)
def test_identifier_traversal_inputs_are_neutralised(raw):
    out = sanitize_identifier(raw)
    assert SAFE.match(out)
    assert ".." not in out and "/" not in out and "\\" not in out and "\x00" not in out
    assert not out.startswith((".", "_", "-"))
    assert len(out) <= 100


@pytest.mark.parametrize("raw", ["", ".", "..", "...", "/", "\x00", "．．", "///", "---", "日本語"])
def test_identifier_rejects_empty_results(raw):
    with pytest.raises(ValueError):
        sanitize_identifier(raw)


def test_identifier_rejects_non_string():
    with pytest.raises(ValueError):
        sanitize_identifier(None)  # type: ignore[arg-type]


def test_slug_title():
    assert slugify_title("Scalable BGP Routing in Clos Fabrics") == "scalable-bgp-routing-in-clos-fabrics"
    assert slugify_title("") == "untitled"
    assert slugify_title(None) == "untitled"
    assert slugify_title("日本語のタイトル") == "untitled"
    assert slugify_title("Café Déjà Vu!!") == "cafe-deja-vu"
    assert slugify_title("../../etc/passwd") == "etc-passwd"


def test_slug_truncates_on_word_boundary():
    s = slugify_title("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu", 50)
    assert len(s) <= 50
    assert not s.endswith("-")
    assert s.split("-")[-1] in {"alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu"}
    assert len(slugify_title("a" * 200)) == 50


def test_internal_identifier():
    ident = generate_internal_identifier()
    assert re.fullmatch(r"BF-[A-Z2-7]{10}", ident)
    assert ident != generate_internal_identifier()
    assert sanitize_identifier(ident) == ident


def test_default_template_valid():
    assert validate_template(DEFAULT) == DEFAULT
    assert validate_template("{identifier}.pdf")
    assert validate_template("papers/{year}/{identifier}-{slug_title}.pdf")


@pytest.mark.parametrize(
    "tpl",
    [
        "{identifier.__class__}.pdf",
        "{identifier.__init__.__globals__}.pdf",
        "{year!r}/{identifier}.pdf",
        "{year!s}/{identifier}.pdf",
        "{year:>1000}/{identifier}.pdf",
        "{identifier[0]}.pdf",
        "{unknown}/{identifier}.pdf",
        "{}/{identifier}.pdf",
        "{0}/{identifier}.pdf",
        "/abs/{year}.pdf",
        "/abs/{identifier}.pdf",
        "../{identifier}.pdf",
        "{year}/../{identifier}.pdf",
        "a/..{identifier}.pdf",
        "{identifier}.txt",
        "{identifier}.pdf.exe",
        "{identifier}",
        "{year}.pdf",  # no identifier -> collisions
        "",
        "{{identifier}}.pdf",
        "{identifier}\x00.pdf",
        "{identifier}\\x.pdf",
        "C:{identifier}.pdf",
        "{year}//{identifier}.pdf",
        ".hidden/{identifier}.pdf",
        "{identifier}.pdf" + "x" * 300,
        "{identifier",
    ],
)
def test_template_attacks_rejected(tpl):
    with pytest.raises(ValueError):
        validate_template(tpl)


def test_build_relative_path_spec_example():
    assert (
        build_relative_path(2024, "2405.12345", "Scalable BGP Routing in Clos Fabrics", DEFAULT)
        == "2024/2405.12345_scalable-bgp-routing-in-clos-fabrics.pdf"
    )
    assert build_relative_path(None, "10.1145/3651890", "X", DEFAULT) == "Unknown/10.1145_3651890_x.pdf"


@pytest.mark.parametrize("year", ["../../x", "2024/../..", -5, 0, 10**6, True, "２０２４", 3.5])
def test_year_cannot_inject(year):
    rel = build_relative_path(year, "2405.1", "t", DEFAULT)
    assert rel.startswith("Unknown/")


def test_build_relative_path_evil_inputs_stay_inside(tmp_path):
    rel = build_relative_path(2024, "../../etc/passwd", "../../../root/.ssh/id_rsa", DEFAULT)
    assert ".." not in rel and not rel.startswith("/")
    assert resolve_in_library(rel, tmp_path).is_relative_to(tmp_path.resolve())


def test_build_relative_path_rejects_bad_template():
    with pytest.raises(ValueError):
        build_relative_path(2024, "x", "y", "{identifier.__class__}.pdf")
    with pytest.raises(ValueError):
        build_relative_path(2024, "..", "y", DEFAULT)


def test_resolve_in_library_ok(tmp_path):
    p = resolve_in_library("2024/a.pdf", tmp_path)
    assert p == tmp_path.resolve() / "2024" / "a.pdf"


@pytest.mark.parametrize("rel", ["../x.pdf", "a/../../x.pdf", "/etc/passwd", "a\x00.pdf", "", ".", "a/..", "..", "\\x"])
def test_resolve_in_library_blocks_traversal(tmp_path, rel):
    with pytest.raises(PathTraversalError):
        resolve_in_library(rel, tmp_path)


def test_resolve_blocks_symlink_escape(tmp_path):
    root = tmp_path / "lib"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF-")
    os.symlink(outside, root / "link")
    with pytest.raises(PathTraversalError):
        resolve_in_library("link/secret.pdf", root)
    os.symlink(outside / "secret.pdf", root / "file.pdf")
    with pytest.raises(PathTraversalError):
        resolve_in_library("file.pdf", root)


def test_resolve_allows_internal_symlink(tmp_path):
    root = tmp_path / "lib"
    (root / "real").mkdir(parents=True)
    os.symlink(root / "real", root / "alias")
    assert resolve_in_library("alias/a.pdf", root) == (root / "real" / "a.pdf").resolve()


def test_with_collision_index():
    from app.services.storage.paths import with_collision_index

    assert with_collision_index("2024/x_y.pdf", 2) == "2024/x_y_2.pdf"
    assert with_collision_index("Unknown/BF-ABC_untitled.pdf", 13) == "Unknown/BF-ABC_untitled_13.pdf"
    for n in (0, 1, -3):
        with pytest.raises(ValueError):
            with_collision_index("2024/x_y.pdf", n)
    for bad in ("../x.pdf", "/abs/x.pdf", "a//b.pdf", "x.txt", ""):
        with pytest.raises(ValueError):
            with_collision_index(bad, 2)
