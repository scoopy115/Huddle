"""Audio preprocessing: decode any container (WAV/MP3/M4A/MP4/WebM/…) to 16 kHz mono
PCM16 WAV using PyAV (bundled with faster-whisper), so no ffmpeg binary is needed.
Imported and recorded audio go through exactly the same path."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SR = 16000
SUPPORTED_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".aac", ".flac", ".ogg", ".opus",
                 ".mov", ".mkv", ".wma", ".aiff", ".aif", ".caf"}


def decode_to_wav(src: Path, dst: Path) -> tuple[float, int]:
    """Decode ``src`` into a 16 kHz mono PCM16 WAV at ``dst``. Returns (duration_sec, size_bytes)."""
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(src), sampling_rate=TARGET_SR)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), audio, TARGET_SR, subtype="PCM_16")
    return len(audio) / TARGET_SR, dst.stat().st_size


def write_playback_mix(mic_wav: Path, system_wav: Path, out: Path) -> Path | None:
    """Microphone + system audio in one file for *listening* (the 16 kHz `processed.wav` is
    for the models). Written at the microphone's own sample rate, 16-bit mono, peak-normalised
    so the louder stream does not clip the mix. Returns None when the system stream is empty."""
    from faster_whisper.audio import decode_audio

    if not system_wav.exists() or system_wav.stat().st_size <= 44:
        return None
    mic, sr = sf.read(str(mic_wav), dtype="float32")
    if mic.ndim > 1:
        mic = mic.mean(axis=1)
    other = np.asarray(decode_audio(str(system_wav), sampling_rate=sr), dtype=np.float32)
    if other.ndim > 1:
        other = other.mean(axis=1)
    n = max(len(mic), len(other))
    mix = np.zeros(n, dtype=np.float32)
    mix[: len(mic)] += mic
    mix[: len(other)] += other
    peak = float(np.max(np.abs(mix))) if n else 0.0
    if peak > 0.99:
        mix /= peak
    tmp = out.with_suffix(".tmp.wav")
    sf.write(str(tmp), mix, sr, subtype="PCM_16")
    tmp.replace(out)
    return out


def duration_of(path: Path) -> float | None:
    try:
        info = sf.info(str(path))
        return info.frames / info.samplerate
    except Exception:
        return None
