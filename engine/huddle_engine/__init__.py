"""Huddle meeting-processing engine.

A local-only service that owns meeting storage (SQLite), the processing job
runner, provider abstractions (transcription / diarization / LLM), model
discovery + resolution, and the MCP server.
"""

__version__ = "0.1.0"
