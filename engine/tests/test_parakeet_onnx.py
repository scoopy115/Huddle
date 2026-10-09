"""Parakeet through sherpa-onnx on PCs, and the marketplace rows a PC with an NVIDIA card sees."""
from pathlib import Path

from huddle_engine.discovery.managed import _scan_models_dir
from huddle_engine.discovery.registry import Registry
from huddle_engine.providers import cuda_runtime
from huddle_engine.providers import parakeet_onnx as po
from huddle_engine.providers.compute import GB, describe
from huddle_engine.resolver import ResolverContext, _whisper_download, candidates_for
from huddle_engine.schemas import ProviderStatus


def test_tokens_become_words_with_start_and_end_times():
    words = po.words_from_tokens(["▁Good", "▁morning", "▁every", "one", "."], [0.0, 0.4, 0.9, 1.1, 1.3], 1.6)
    assert [w["word"] for w in words] == [" Good", " morning", " everyone."]
    assert [(w["start"], w["end"]) for w in words] == [(0.0, 0.4), (0.4, 0.9), (0.9, 1.6)]
    assert po.words_from_tokens([], [], 1.0) == []


def test_parakeet_folder_is_a_managed_onnx_model(tmp_path):
    d = tmp_path / "parakeet" / po.ARCHIVE_NAME
    d.mkdir(parents=True)
    for f in po.MODEL_FILES:
        (d / f).write_bytes(b"x" * 10)
    (tmp_path / "parakeet" / "half").mkdir()
    (tmp_path / "parakeet" / "half" / "tokens.txt").write_text("a 1")
    models = _scan_models_dir(tmp_path, source="our_app", managed=True)
    assert [m.id for m in models] == [f"our_app:parakeet/{po.ARCHIVE_NAME}"]
    m = models[0]
    assert m.family == "parakeet" and m.format == "ONNX" and m.compatible and m.path == str(d)


def _ctx(db, cfg, hw, models=()):
    reg = Registry(db, cfg)
    reg.models = lambda task=None: [m for m in models if task is None or m.task == task]  # type: ignore[method-assign]
    reg.providers = lambda: [ProviderStatus(id="ollama", name="Ollama", status="not_found")]  # type: ignore[method-assign]
    return ResolverContext(registry=reg, settings={}, memory_bytes=hw["memoryBytes"], hardware=hw)


RTX = {"name": "NVIDIA GeForce RTX 3070", "vendor": "nvidia", "vramBytes": 8 * GB, "integrated": False}
IRIS = {"name": "Intel(R) Iris(R) Xe Graphics", "vendor": "intel", "vramBytes": 128 * 1024 * 1024, "integrated": True}


def test_nvidia_pc_sees_the_gpu_and_cpu_rows_and_parakeet(db, cfg, monkeypatch):
    monkeypatch.setattr(cuda_runtime, "supported", lambda: True)
    monkeypatch.setattr(cuda_runtime, "installed", lambda: False)
    monkeypatch.setattr("huddle_engine.providers.parakeet.parakeet_available", lambda: False)
    monkeypatch.setattr("huddle_engine.providers.transcription.mlx_available", lambda: False)
    hw = describe("Windows", "AMD64", "AMD Ryzen", 32 * GB, "10.0.26100", [RTX], cpu_cores=16)
    rows = {c.id: c for c in candidates_for(_ctx(db, cfg, hw)) if c.task == "transcription"}
    assert "whisper:cuda-large-v3-turbo" in rows and "whisper:large-v3-turbo" in rows
    assert "parakeet:onnx-tdt-0.6b-v3" in rows and "parakeet:mlx-tdt-0.6b-v3" not in rows
    assert rows["whisper:cuda-large-v3-turbo"].recommended and not rows["whisper:large-v3-turbo"].recommended
    assert rows["whisper:cuda-large-v3-turbo"].name.endswith("(NVIDIA GPU)") and rows["whisper:large-v3-turbo"].name.endswith("(CPU)")
    assert rows["whisper:medium"].name == "Whisper medium (CPU)" and rows["parakeet:onnx-tdt-0.6b-v3"].name == "Parakeet TDT 0.6B v3 (CPU)"
    # the bundle's size includes cuBLAS until that is installed
    assert rows["whisper:cuda-large-v3-turbo"].size_bytes == rows["whisper:large-v3-turbo"].size_bytes + cuda_runtime.WHEEL_SIZE
    assert rows["whisper:cuda-large-v3-turbo"].installed is False
    assert _whisper_download(_ctx(db, cfg, hw)).id == "whisper:cuda-large-v3-turbo"


def test_with_cublas_every_ct2_row_is_the_gpu_row(db, cfg, monkeypatch):
    monkeypatch.setattr(cuda_runtime, "supported", lambda: True)
    monkeypatch.setattr(cuda_runtime, "installed", lambda: True)
    monkeypatch.setattr("huddle_engine.providers.parakeet.parakeet_available", lambda: False)
    monkeypatch.setattr("huddle_engine.providers.transcription.mlx_available", lambda: False)
    hw = describe("Windows", "AMD64", "AMD Ryzen", 32 * GB, "10.0.26100", [RTX], cpu_cores=16)
    rows = {c.id: c for c in candidates_for(_ctx(db, cfg, hw)) if c.task == "transcription"}
    assert "whisper:large-v3-turbo" not in rows                      # no CPU twin any more
    assert rows["whisper:cuda-large-v3-turbo"].size_bytes == 1_620_000_000   # cuBLAS no longer counted
    assert rows["whisper:medium"].name == "Whisper medium (NVIDIA GPU)"
    assert rows["parakeet:onnx-tdt-0.6b-v3"].name == "Parakeet TDT 0.6B v3 (CPU)"


def test_pc_without_nvidia_sees_no_gpu_row(db, cfg, monkeypatch):
    monkeypatch.setattr(cuda_runtime, "supported", lambda: True)
    monkeypatch.setattr("huddle_engine.providers.parakeet.parakeet_available", lambda: False)
    monkeypatch.setattr("huddle_engine.providers.transcription.mlx_available", lambda: False)
    hw = describe("Windows", "AMD64", "Intel Core i7", 16 * GB, "10.0.26100", [IRIS], cpu_cores=8)
    ids = [c.id for c in candidates_for(_ctx(db, cfg, hw))]
    assert "whisper:cuda-large-v3-turbo" not in ids and "whisper:large-v3-turbo" in ids
    assert _whisper_download(_ctx(db, cfg, hw)).id == "whisper:large-v3-turbo"


def test_model_dir_check(tmp_path):
    assert not po.is_model_dir(tmp_path)
    for f in po.MODEL_FILES:
        (tmp_path / f).write_bytes(b"x")
    assert po.is_model_dir(Path(tmp_path))
