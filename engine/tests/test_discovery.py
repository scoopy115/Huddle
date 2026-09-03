"""Provider/model discovery, compatibility, resolution (Ollama-only for AI), ownership and
storage estimation. No external model is ever touched: everything runs against temp dirs."""
import json
from pathlib import Path

import pytest

from huddle_engine.discovery import hf_cache, lmstudio, managed, ollama
from huddle_engine.discovery.registry import Registry, annotate_compatibility
from huddle_engine.resolver import (
    LLM_CANDIDATES,
    ResolverContext,
    additional_bytes,
    is_recommended_size,
    resolve_all,
    resolve_llm,
    resolve_transcription,
)
from huddle_engine.schemas import LocalModel, ProviderStatus

GB = 1024 ** 3


def _hf_repo(root: Path, repo: str, files: dict[str, int]) -> Path:
    d = root / f"models--{repo.replace('/', '--')}" / "snapshots" / "abc"
    d.mkdir(parents=True)
    for name, size in files.items():
        (d / name).write_bytes(b"0" * size)
    return d


def test_hf_cache_classifies_by_content_not_name(tmp_path):
    _hf_repo(tmp_path, "Systran/faster-whisper-large-v3", {"model.bin": 10, "vocabulary.txt": 1, "config.json": 1})
    _hf_repo(tmp_path, "openai/whisper-large-v3", {"model.safetensors": 10, "generation_config.json": 1, "config.json": 1})
    _hf_repo(tmp_path, "mlx-community/whisper-large-v3-mlx", {"weights.npz": 10, "config.json": 1})
    _hf_repo(tmp_path, "pyannote/speaker-diarization-3.1", {"config.yaml": 1})
    _hf_repo(tmp_path, "Qwen/Qwen3-8B-GGUF", {"Qwen3-8B-Q4_K_M.gguf": 20})
    status, models = hf_cache.discover(tmp_path)
    assert status.status == "available"
    by = {m.name: annotate_compatibility(m) for m in models}
    ct2 = by["Systran/faster-whisper-large-v3"]
    assert ct2.format == "CTranslate2" and ct2.compatible and ct2.meta["whisperSize"] == "large-v3"
    tf = by["openai/whisper-large-v3"]
    assert tf.format == "safetensors" and not tf.compatible and "CTranslate2" in tf.compatibility_note
    from huddle_engine.providers.transcription import mlx_available
    assert by["mlx-community/whisper-large-v3-mlx"].compatible == mlx_available()   # MLX only on Apple Silicon with mlx installed
    assert by["pyannote/speaker-diarization-3.1"].task == "diarization"
    gguf = by["Qwen/Qwen3-8B-GGUF · Qwen3-8B-Q4_K_M.gguf"]
    # A GGUF outside Ollama is listed but not usable: AI runs through Ollama only.
    assert gguf.format == "GGUF" and gguf.quantization == "Q4_K_M" and not gguf.compatible and "Ollama" in gguf.compatibility_note
    assert all(m.externally_managed for m in models)


def test_ollama_manifest_parsing_when_not_running(tmp_path, monkeypatch):
    mdir = tmp_path / "ollama"
    tag = mdir / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "9b"
    tag.parent.mkdir(parents=True)
    tag.write_text(json.dumps({"layers": [{"size": 5_000_000_000}, {"size": 1_000}]}))
    monkeypatch.setenv("OLLAMA_MODELS", str(mdir))
    monkeypatch.setattr(ollama, "_api_models", lambda: None)
    status, models = ollama.discover()
    assert status.status == "installed_not_running"
    assert models[0].id == "ollama:qwen3.5:9b" and models[0].size_bytes == 5_000_001_000
    assert models[0].externally_managed and models[0].meta["running"] is False
    assert annotate_compatibility(models[0]).compatible


def test_ollama_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "nope"))
    monkeypatch.setattr(ollama, "_api_models", lambda: None)
    monkeypatch.setattr(ollama, "installed", lambda: False)
    status, models = ollama.discover()
    assert status.status == "not_found" and models == []


def test_lmstudio_dir_scan_is_read_only_and_not_used_for_ai(tmp_path, monkeypatch):
    mdir = tmp_path / "lms" / "models" / "lmstudio-community"
    mdir.mkdir(parents=True)
    (mdir / "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf").write_bytes(b"0" * 10)
    monkeypatch.setattr(lmstudio, "_models_dir", lambda: tmp_path / "lms" / "models")
    monkeypatch.setattr(lmstudio.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    status, models = lmstudio.discover()
    assert status.status == "installed_not_running"
    assert models[0].externally_managed and not annotate_compatibility(models[0]).compatible


def test_managed_models_are_the_only_deletable_ones(db, cfg):
    _hf_repo(cfg.models_dir / "whisper", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", {"model.bin": 10, "vocabulary.json": 1})
    statuses, models = managed.discover(cfg.models_dir)
    assert all(not m.externally_managed and m.source == "our_app" for m in models)
    reg = Registry(db, cfg.models_dir)
    reg._save(statuses, models, {"our_app"})
    reg._save([], [LocalModel(id="ollama:x", name="x", task="llm", source="ollama", format="GGUF", externally_managed=True,
                              compatible_runtimes=["ollama"])], {"ollama"})
    with pytest.raises(PermissionError):
        reg.delete_managed("ollama:x")
    with pytest.raises(KeyError):
        reg.delete_managed("does-not-exist")
    whisper = next(m for m in reg.models() if m.task == "transcription")
    reg.delete_managed(whisper.id)
    assert not list((cfg.models_dir / "whisper").glob("models--*"))
    assert reg.model("ollama:x") is not None          # external untouched


def _ctx(db, cfg, models, settings=None, mem=32 * GB, ollama_status="available"):
    reg = Registry(db, cfg.models_dir)
    reg._save([ProviderStatus(id="ollama", kind="llm", name="Ollama", status=ollama_status, checked_at=0)],
              models, {m.source for m in models})
    return ResolverContext(registry=reg, settings=settings or {}, memory_bytes=mem)


def _oll(name, params, running=True, family="qwen3"):
    return LocalModel(id=f"ollama:{name}", name=name, family=family, task="llm", source="ollama", format="GGUF",
                      externally_managed=True, compatible_runtimes=["ollama"], size_bytes=int(params * 0.6e9),
                      meta={"parameterSize": f"{params}B", "running": running})


def test_resolution_priority_and_storage(db, cfg):
    # Nothing local, Ollama running → whisper download + ollama pull recommended.
    ctx = _ctx(db, cfg, [])
    res = resolve_all(ctx)
    assert [r.status for r in res] == ["download_required", "builtin", "download_required"]
    assert res[2].download.source == "ollama" and res[2].download.url == "qwen3.5:4b"
    assert res[0].download.task == "transcription" and res[0].download.size_bytes > 0
    assert additional_bytes(res) == res[0].download.size_bytes + LLM_CANDIDATES[0].size_bytes
    # 8 GB machine → smaller AI model recommended
    small = resolve_all(_ctx(db, cfg, [], mem=8 * GB))
    assert small[2].download.url == "qwen3.5:4b"

    # Ollama not installed → unavailable with install guidance, nothing to download yet.
    r = resolve_llm(_ctx(db, cfg, [], ollama_status="not_found"))
    assert r.status == "unavailable" and "Install" in r.reason

    # Ollama has models → the general chat model in the recommended band wins; coder model skipped.
    q9 = _oll("qwen3.5:9b", 9.7, family="qwen35")
    coder = _oll("qwen3-coder:30b", 30)
    big = _oll("gpt-oss:20b", 20, family="gptoss")
    hf = LocalModel(id="huggingface:Systran/faster-whisper-large-v3", name="Systran/faster-whisper-large-v3", family="whisper",
                    task="transcription", source="huggingface", format="CTranslate2", externally_managed=True,
                    compatible_runtimes=["faster-whisper"], path="/x", size_bytes=3 * GB, meta={"whisperSize": "large-v3"})
    ctx = _ctx(db, cfg, [q9, coder, big, hf])
    res = resolve_all(ctx)
    assert res[0].status == "ready" and res[0].model.id == hf.id and "Hugging Face" in res[0].reason
    assert res[2].status == "ready" and res[2].model.id == q9.id and res[2].provider == "ollama"
    assert additional_bytes(res) == 0
    q4 = _oll("qwen3.5:4b", 4.0, family="qwen35")
    assert is_recommended_size(q4, 32 * GB) and not is_recommended_size(q9, 32 * GB) and not is_recommended_size(big, 32 * GB)

    # Explicit selection wins; a vanished selection is 'unavailable', never silently substituted.
    r = resolve_llm(_ctx(db, cfg, [q9], settings={"models.ai": "ollama:qwen3.5:9b"}))
    assert r.status == "ready" and r.reason == "Selected in Settings"
    r = resolve_llm(_ctx(db, cfg, [q9], settings={"models.ai": "ollama:gone"}))
    assert r.status == "unavailable" and r.model is None
    r = resolve_transcription(_ctx(db, cfg, [hf], settings={"models.whisper": "nope"}))
    assert r.status == "unavailable"

    # Managed whisper preferred over cache when both are equivalent.
    ours = LocalModel(id="our_app:mobiuslabsgmbh/faster-whisper-large-v3-turbo", name="turbo", family="whisper",
                      task="transcription", source="our_app", format="CTranslate2", externally_managed=False,
                      compatible_runtimes=["faster-whisper"], path="/z", size_bytes=1, meta={"whisperSize": "large-v3-turbo"})
    r = resolve_transcription(_ctx(db, cfg, [hf, ours]))
    assert r.model.id == ours.id and r.reason == "Installed"

    # Ollama installed but not running → the model is known but summaries cannot run.
    r = resolve_llm(_ctx(db, cfg, [_oll("qwen3.5:9b", 9.7, running=False)], ollama_status="installed_not_running"))
    assert r.status == "unavailable" and "not running" in r.reason
