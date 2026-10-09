"""Hugging Face snapshots whose symlinks Windows will not follow are turned into plain files."""
import os
from pathlib import Path

import pytest

from huddle_engine.discovery.hf_layout import dereference_snapshot


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A cache root with the huggingface_hub 2.x layout: a shared blob two hops away from the
    snapshot file, plus a one-hop small file."""
    root = tmp_path / "whisper"
    repo = root / "models--org--model"
    shared = root / "blobs" / "ab"
    shared.mkdir(parents=True)
    (shared / "abcdef").write_bytes(b"M" * 1000)
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "0123").write_text('{"k": 1}')
    snap = repo / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    try:
        os.symlink(os.path.join("..", "..", "blobs", "ab", "abcdef"), repo / "blobs" / "big")
        os.symlink(os.path.join("..", "..", "blobs", "big"), snap / "model.bin")
        os.symlink(os.path.join("..", "..", "blobs", "0123"), snap / "config.json")
    except OSError as e:  # no symlink privilege on this runner
        pytest.skip(f"symlinks not available: {e}")
    return root, snap


def test_links_are_replaced_by_their_files(tmp_path):
    root, snap = _repo(tmp_path)
    assert dereference_snapshot(snap, only_broken=False) == 2
    assert not (snap / "model.bin").is_symlink() and (snap / "model.bin").read_bytes() == b"M" * 1000
    assert not (snap / "config.json").is_symlink() and (snap / "config.json").read_text() == '{"k": 1}'
    assert not (root / "blobs" / "ab" / "abcdef").exists()   # moved, not copied
    # idempotent
    assert dereference_snapshot(snap, only_broken=False) == 0


def test_working_links_are_left_alone_unless_broken(tmp_path):
    _, snap = _repo(tmp_path)
    if os.name != "nt":
        assert dereference_snapshot(snap) == 0            # not Windows: never touched
        assert (snap / "model.bin").is_symlink()
    else:
        # Windows: only links the OS cannot follow are materialised; whether this runner can
        # follow them decides, and either outcome leaves a readable model.bin.
        dereference_snapshot(snap)
        assert (snap / "model.bin").read_bytes() == b"M" * 1000


def test_dangling_link_is_reported_not_crashed(tmp_path, caplog):
    snap = tmp_path / "snap"
    snap.mkdir()
    try:
        os.symlink("nowhere.bin", snap / "model.bin")
    except OSError as e:
        pytest.skip(f"symlinks not available: {e}")
    assert dereference_snapshot(snap, only_broken=False) == 0
    assert "points nowhere" in caplog.text
