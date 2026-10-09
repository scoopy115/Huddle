"""Hardware detection and machine fit: three machine shapes (unified memory, a PC with a
dedicated graphics card, a CPU-only PC) decide which models are offered and recommended."""
from huddle_engine.discovery.registry import Registry
from huddle_engine.providers import compute
from huddle_engine.providers.compute import GB, adapter_record, assess, describe, gpu_vendor, is_integrated, parse_nvidia_smi
from huddle_engine.resolver import LLM_CANDIDATES, WHISPER_CANDIDATES, ResolverContext, candidates_for, fit, resolve_llm
from huddle_engine.schemas import ProviderStatus


def _m4(mem=16 * GB):
    return describe("Darwin", "arm64", "Apple M4 Pro", mem, "26.0",
                    [{"name": "Apple M4 Pro GPU", "vendor": "apple", "vramBytes": mem, "integrated": True}], cpu_cores=12)


def _pc(mem, gpus, cores=8):
    return describe("Windows", "AMD64", "Intel Core i7", mem, "10.0.26100", gpus, cpu_cores=cores)


RTX_4060 = {"name": "NVIDIA GeForce RTX 4060 Laptop GPU", "vendor": "nvidia", "vramBytes": 8 * GB, "integrated": False}
RTX_4090 = {"name": "NVIDIA GeForce RTX 4090", "vendor": "nvidia", "vramBytes": 24 * GB, "integrated": False}
MX_450 = {"name": "NVIDIA GeForce MX450", "vendor": "nvidia", "vramBytes": 2 * GB, "integrated": False}
IRIS = {"name": "Intel(R) Iris(R) Xe Graphics", "vendor": "intel", "vramBytes": 128 * 1024 * 1024, "integrated": True}


# ---- classification ---------------------------------------------------------------------- #

def test_gpu_vendor_from_name_or_pci_id():
    assert gpu_vendor("NVIDIA GeForce RTX 3060") == "nvidia"
    assert gpu_vendor("Something", "PCI\\VEN_10DE&DEV_2503") == "nvidia"
    assert gpu_vendor("AMD Radeon RX 7800 XT") == "amd"
    assert gpu_vendor("Intel(R) Iris(R) Xe Graphics") == "intel"
    assert gpu_vendor("Intel(R) Arc(TM) A770 Graphics") == "intel"
    assert gpu_vendor("Microsoft Basic Display Adapter") == "other"


def test_integrated_graphics_are_never_a_usable_card():
    assert is_integrated("Intel(R) Iris(R) Xe Graphics", "intel", 128 * 1024 * 1024)
    assert is_integrated("Intel(R) UHD Graphics 620", "intel", None)
    assert not is_integrated("Intel(R) Arc(TM) A770 Graphics", "intel", 16 * GB)
    assert is_integrated("AMD Radeon(TM) Graphics", "amd", 512 * 1024 * 1024)
    assert is_integrated("AMD Radeon 780M Graphics", "amd", None)
    assert not is_integrated("AMD Radeon RX 7800 XT", "amd", 16 * GB)
    assert not is_integrated("NVIDIA GeForce RTX 4060", "nvidia", 8 * GB)
    assert is_integrated("Some adapter", "other", 256 * 1024 * 1024)   # under 1 GB of its own memory


def test_nvidia_smi_output_is_parsed():
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 4070 Laptop GPU, 8188\nNVIDIA T600, 4096\n")
    assert [g["name"] for g in gpus] == ["NVIDIA GeForce RTX 4070 Laptop GPU", "NVIDIA T600"]
    assert gpus[0]["vramBytes"] == 8188 * 1024 * 1024 and not gpus[0]["integrated"]
    assert parse_nvidia_smi("") == [] and parse_nvidia_smi("garbage") == []


def test_registry_adapter_record_reads_qword_bytes():
    raw = (12 * GB).to_bytes(8, "little")
    rec = adapter_record("NVIDIA GeForce RTX 4070", "PCI\\VEN_10DE&DEV_2786", raw)
    assert rec["vramBytes"] == 12 * GB and rec["vendor"] == "nvidia" and not rec["integrated"]
    assert adapter_record("Intel(R) UHD Graphics", "PCI\\VEN_8086", None)["vramBytes"] is None
    assert adapter_record("x", "", 0)["vramBytes"] is None


# ---- describe / assess ------------------------------------------------------------------- #

def test_apple_silicon_is_unified_memory_and_full_tier():
    hw = _m4(16 * GB)
    assert hw["unifiedMemory"] and hw["acceleratorBytes"] == 16 * GB and hw["os"] == "darwin"
    assert hw["capability"]["tier"] == "full"
    assert assess(_m4(8 * GB))["tier"] == "full"          # 8 GB Macs run the 4B model


def test_pc_with_a_real_card_is_full_tier_by_vram():
    hw = _pc(16 * GB, [IRIS, RTX_4060])
    assert hw["acceleratorBytes"] == 8 * GB and hw["acceleratorName"] == RTX_4060["name"]
    assert hw["capability"]["tier"] == "full"
    assert any("graphics card" in d for d in hw["capability"]["details"])
    # The biggest card wins when there are several.
    assert _pc(32 * GB, [RTX_4060, RTX_4090])["acceleratorBytes"] == 24 * GB


def test_integrated_graphics_with_enough_memory_is_limited_tier():
    hw = _pc(16 * GB, [IRIS])
    assert hw["acceleratorBytes"] is None
    assert hw["capability"]["tier"] == "limited"
    assert any("CPU" in d for d in hw["capability"]["details"])


def test_small_card_counts_as_no_card():
    assert _pc(16 * GB, [MX_450])["capability"]["tier"] == "limited"
    assert _pc(8 * GB, [MX_450])["capability"]["tier"] == "minimal"


def test_little_memory_is_minimal_tier():
    assert _pc(8 * GB, [IRIS])["capability"]["tier"] == "minimal"
    assert _pc(4 * GB, [])["capability"]["tier"] == "minimal"
    assert _pc(16 * GB, [], cores=2)["capability"]["tier"] == "minimal"
    assert _pc(None, [])["capability"]["tier"] == "minimal"   # unknown memory: the conservative answer


def test_hardware_info_runs_on_this_machine():
    hw = compute.hardware_info()
    assert hw["cpuCores"] >= 1 and hw["capability"]["tier"] in ("full", "limited", "minimal")
    assert isinstance(hw["gpus"], list)


# ---- fit ----------------------------------------------------------------------------------- #

def _cand(cid):
    return next(c for c in WHISPER_CANDIDATES + LLM_CANDIDATES if c.id == cid)


def test_llm_fit_on_apple_silicon_goes_by_system_memory():
    assert fit(_cand("ollama:qwen3.5:4b"), _m4(8 * GB)) == ("ok", None)
    assert fit(_cand("ollama:qwen3.5:9b"), _m4(16 * GB)) == ("ok", None)
    f, why = fit(_cand("ollama:qwen3.5:9b"), _m4(8 * GB))
    assert f == "no" and "16 GB" in why


def test_llm_fit_on_a_pc_goes_by_graphics_memory():
    hw = _pc(16 * GB, [RTX_4060])
    assert fit(_cand("ollama:qwen3.5:4b"), hw) == ("ok", None)
    f, why = fit(_cand("ollama:qwen3.5:9b"), hw)
    assert f == "no" and "12 GB" in why                 # too big for the card, too big for 16 GB of RAM
    f, why = fit(_cand("ollama:qwen3.5:9b"), _pc(32 * GB, [RTX_4060]))
    assert f == "slow" and "graphics card" in why      # 32 GB of RAM: falls back to the CPU
    assert fit(_cand("ollama:qwen3.5:9b"), _pc(16 * GB, [RTX_4090])) == ("ok", None)


def test_llm_fit_without_a_card_needs_twice_the_memory_and_is_slow():
    f, why = fit(_cand("ollama:qwen3.5:4b"), _pc(16 * GB, [IRIS]))
    assert f == "slow" and "CPU" in why
    f, why = fit(_cand("ollama:qwen3.5:4b"), _pc(8 * GB, [IRIS]))
    assert f == "no" and "16 GB" in why and "6 GB" in why
    assert fit(_cand("ollama:qwen3.5:9b"), _pc(32 * GB, []))[0] == "slow"
    assert fit(_cand("ollama:qwen3.5:9b"), _pc(16 * GB, []))[0] == "no"


def test_whisper_fit():
    assert fit(_cand("whisper:large-v3-turbo"), _pc(8 * GB, [IRIS])) == ("ok", None)
    assert fit(_cand("whisper:large-v3"), _pc(8 * GB, [IRIS]))[0] == "no"
    assert fit(_cand("whisper:large-v3"), _pc(16 * GB, [IRIS]))[0] == "slow"
    assert fit(_cand("whisper:large-v3"), _m4(16 * GB)) == ("ok", None)
    assert fit(_cand("whisper:small"), _pc(4 * GB, []))[0] == "ok"
    assert fit(_cand("whisper:medium"), _pc(4 * GB, []))[0] == "no"
    assert fit(_cand("whisper:large-v3-turbo"), None) == ("ok", None)   # unknown machine: not judged


# ---- marketplace and resolution ---------------------------------------------------------- #

def _ctx(db, cfg, hw, monkeypatch, mlx=False):
    from huddle_engine.providers import parakeet, transcription
    monkeypatch.setattr(transcription, "mlx_available", lambda: mlx)
    monkeypatch.setattr(parakeet, "parakeet_available", lambda: mlx)
    reg = Registry(db, cfg.models_dir)
    reg._save([ProviderStatus(id="ollama", kind="llm", name="Ollama", status="not_found", checked_at=0)], [], {"ollama"})
    return ResolverContext(registry=reg, settings={}, memory_bytes=hw["memoryBytes"], hardware=hw)


def test_marketplace_hides_apple_silicon_builds_on_a_pc(db, cfg, monkeypatch):
    cands = candidates_for(_ctx(db, cfg, _pc(16 * GB, [RTX_4060]), monkeypatch))
    assert not any("(Apple Silicon)" in c.name for c in cands)
    by = {c.id: c for c in cands}
    assert by["whisper:large-v3-turbo"].recommended and by["whisper:large-v3-turbo"].fit == "ok"
    assert by["ollama:qwen3.5:4b"].recommended and by["ollama:qwen3.5:4b"].fit == "ok"
    assert by["ollama:qwen3.5:9b"].fit == "no" and by["ollama:qwen3.5:9b"].fit_reason


def test_marketplace_on_apple_silicon_keeps_the_mlx_build(db, cfg, monkeypatch):
    cands = candidates_for(_ctx(db, cfg, _m4(16 * GB), monkeypatch, mlx=True))
    by = {c.id: c for c in cands}
    assert by["whisper:mlx-large-v3-turbo"].recommended
    assert by["parakeet:mlx-tdt-0.6b-v3"].fit == "ok"
    assert by["ollama:qwen3.5:9b"].fit == "ok"


def test_minimal_machine_gets_transcription_only(db, cfg, monkeypatch):
    ctx = _ctx(db, cfg, _pc(8 * GB, [IRIS]), monkeypatch)
    by = {c.id: c for c in candidates_for(ctx)}
    assert by["ollama:qwen3.5:4b"].fit == "no" and not by["ollama:qwen3.5:4b"].recommended
    assert by["whisper:medium"].recommended           # 8 GB: the medium model is the automatic pick
    res = resolve_llm(ctx)
    assert res.status == "unsupported" and res.download is None and "16 GB" in res.reason


def test_limited_machine_still_offers_the_small_model(db, cfg, monkeypatch):
    ctx = _ctx(db, cfg, _pc(16 * GB, [IRIS]), monkeypatch)
    res = resolve_llm(ctx)
    assert res.status == "download_required" and res.download and res.download.id == "ollama:qwen3.5:4b"
    assert {c.id: c for c in candidates_for(ctx)}["ollama:qwen3.5:4b"].fit == "slow"


def test_whisper_pick_follows_memory(db, cfg, monkeypatch):
    from huddle_engine.resolver import _whisper_download
    assert _whisper_download(_ctx(db, cfg, _pc(4 * GB, []), monkeypatch)).id == "whisper:small"
    assert _whisper_download(_ctx(db, cfg, _pc(8 * GB, []), monkeypatch)).id == "whisper:medium"
    assert _whisper_download(_ctx(db, cfg, _pc(16 * GB, []), monkeypatch)).id == "whisper:large-v3-turbo"
