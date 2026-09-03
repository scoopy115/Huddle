from __future__ import annotations

import re
from pathlib import Path

# Runtimes we actually ship / can drive. Compatibility is (task, format, runtime), never by name.
RUNTIME_FASTER_WHISPER = "faster-whisper"     # CTranslate2 Whisper directories
RUNTIME_LLAMACPP = "llama.cpp"                # GGUF files via llama-cpp-python
RUNTIME_OLLAMA = "ollama"                     # served by the Ollama daemon
RUNTIME_LMSTUDIO = "lmstudio"                 # served by LM Studio's local server
RUNTIME_SHERPA = "sherpa-onnx"
RUNTIME_MLX = "mlx-whisper"                  # MLX Whisper on Apple Silicon

WHISPER_SIZE_RE = re.compile(
    r"(large-v3-turbo|large-v3|large-v2|large-v1|large|medium\.en|medium|small\.en|small|"
    r"base\.en|base|tiny\.en|tiny|distil-large-v3|distil-large-v2|distil-medium\.en|distil-small\.en|turbo)",
    re.IGNORECASE)


def dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() or p.is_symlink():
                    total += p.resolve().stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total





def whisper_size_from_name(name: str) -> str | None:
    m = WHISPER_SIZE_RE.search(name)
    return m.group(1).lower() if m else None


def gguf_quant_from_name(name: str) -> str | None:
    m = re.search(r"(IQ\d_[A-Z]+|Q\d_K_[SML]|Q\d_K|Q\d_\d|Q\d|F16|F32|BF16|fp16|MXFP4)", name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def llm_family_from_name(name: str) -> str | None:
    n = name.lower()
    for fam in ("qwen3.5", "qwen3", "qwen2.5", "qwen2", "qwen", "llama-3.3", "llama-3.2", "llama-3.1", "llama3",
                "llama", "gemma-3", "gemma3", "gemma", "mistral", "mixtral", "phi-4", "phi-3", "phi", "gpt-oss",
                "hermes", "deepseek", "command-r", "smollm", "granite", "nomic", "bge", "e5", "minilm", "mxbai"):
        if fam in n:
            return fam
    return None


def is_embedding_model(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("embed", "bge", "e5-", "minilm", "mxbai", "nomic-embed", "gte-", "arctic-embed"))
