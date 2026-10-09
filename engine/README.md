# Huddle engine

The Python engine behind Huddle: audio preparation, transcription (Whisper / Parakeet), speaker
separation (sherpa-onnx), notes (Ollama) and the MCP server. The desktop app spawns it as a
sidecar; [Huddle Server](https://github.com/scoopy115/huddle-server) runs it headless.

```
pip install -e .
python -m huddle_engine serve      # HTTP API (env: HUDDLE_DATA_DIR / HUDDLE_HOST / HUDDLE_PORT / HUDDLE_TOKEN)
python -m huddle_engine process <audio>
python -m huddle_engine doctor
```
