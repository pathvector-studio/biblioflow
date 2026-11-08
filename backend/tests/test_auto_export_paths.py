"""Pure unit tests (no database) for auto-export filenames, path containment and atomic writes."""

import os

import pytest

from app.services.auto_export import (
    ExportPathError,
    delete_export_file,
    resolve_export_path,
    validate_filename,
    write_atomic,
)


@pytest.mark.parametrize(
    "name,fmt",
    [("refs.bib", "bibtex"), ("My-Lab_2024.v2.bib", "bibtex"), ("a.ris", "ris"), ("x.json", "csljson")],
)
def test_valid_filenames(name, fmt):
    assert validate_filename(name, fmt) == name


@pytest.mark.parametrize(
    "name,fmt",
    [
        ("../refs.bib", "bibtex"),
        ("a/b.bib", "bibtex"),
        ("a\\b.bib", "bibtex"),
        ("/etc/passwd.bib", "bibtex"),
        (".hidden.bib", "bibtex"),
        ("a..b.bib", "bibtex"),
        ("refs.bib\x00.png", "bibtex"),
        ("refs bib.bib", "bibtex"),
        ("refs.txt", "bibtex"),
        ("refs.bib", "ris"),
        ("refs.bib", "docx"),
        ("", "bibtex"),
        ("a" * 100 + ".bib", "bibtex"),
    ],
)
def test_invalid_filenames(name, fmt):
    with pytest.raises(ExportPathError):
        validate_filename(name, fmt)


def test_resolve_stays_inside_root(tmp_path):
    p = resolve_export_path(tmp_path, "lab-1234abcd", "refs.bib")
    assert p == (tmp_path / "lab-1234abcd" / "refs.bib").resolve()
    for slug, name in [("..", "refs.bib"), ("lab", "../refs.bib"), ("a/b", "refs.bib"), ("", "refs.bib"), ("lab", "")]:
        with pytest.raises(ExportPathError):
            resolve_export_path(tmp_path, slug, name)


def test_resolve_rejects_symlink_escape(tmp_path):
    root = tmp_path / "exports"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "lab")  # a workspace directory that points outside
    with pytest.raises(ExportPathError):
        resolve_export_path(root, "lab", "refs.bib")
    (root / "ok").mkdir()
    os.symlink(outside / "x.bib", root / "ok" / "refs.bib")  # the file itself points outside
    with pytest.raises(ExportPathError):
        resolve_export_path(root, "ok", "refs.bib")


def test_write_atomic_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "lab" / "refs.bib"
    write_atomic(target, b"one")
    assert target.read_bytes() == b"one"
    inode = target.stat().st_ino
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["refs.bib"]
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert target.stat().st_ino != inode  # replaced, not rewritten in place


def test_write_atomic_failure_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / "lab" / "refs.bib"
    write_atomic(target, b"old")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in target.parent.iterdir()] == ["refs.bib"]


def test_delete_export_file(tmp_path):
    write_atomic(tmp_path / "lab" / "refs.bib", b"x")
    delete_export_file(tmp_path, "lab", "refs.bib")
    assert not (tmp_path / "lab" / "refs.bib").exists()
    delete_export_file(tmp_path, "lab", "refs.bib")  # missing: no error
    delete_export_file(tmp_path, "..", "refs.bib")  # invalid: logged, no error
