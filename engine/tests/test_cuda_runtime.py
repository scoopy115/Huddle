"""The optional cuBLAS download for CTranslate2 on NVIDIA PCs."""
import zipfile
from pathlib import Path

from huddle_engine.providers import cuda_runtime as cr


def _wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for lib in cr.LIBS:
            z.writestr(f"nvidia/cublas/bin/{lib}", b"\x00" * 64)
        z.writestr("nvidia/cublas/bin/nvblas64_12.dll", b"x")      # not wanted
        z.writestr("nvidia/cublas/include/cublas.h", b"//")
        z.writestr("nvidia_cublas_cu12-12.9.1.4.dist-info/METADATA", b"Name: nvidia-cublas-cu12")
    return path


def test_only_the_cublas_libraries_are_extracted(tmp_path):
    got = cr.extract_libs(_wheel(tmp_path / "w.whl"), tmp_path / "cuda")
    assert sorted(got) == sorted(cr.LIBS)
    assert sorted(p.name for p in (tmp_path / "cuda").iterdir()) == sorted(cr.LIBS)


def test_installed_reflects_huddles_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "_models_dir", tmp_path)
    monkeypatch.setattr(cr, "_activated", False)
    monkeypatch.setattr(cr, "system_cublas", lambda: None)
    assert not cr.installed()
    cr.extract_libs(_wheel(tmp_path / "w.whl"), cr.runtime_dir())
    assert cr.installed()
    cr.activate()
    assert str(cr.runtime_dir()) in __import__("os").environ["PATH"]
    cr.remove()
    assert not cr.installed()


def test_system_toolkit_counts_as_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "_models_dir", tmp_path)
    monkeypatch.setattr(cr, "system_cublas", lambda: "/usr/lib/libcublas.so.12")
    assert cr.installed()
