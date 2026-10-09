"""Cancelling: the job says "cancelling" at once, a queued job stays cancelled, and the model
answer is cut off at the next token."""
import json

import httpx

from huddle_engine.providers.llm import LlmCancelled, OllamaProvider
from huddle_engine.services import meetings as ms
from tests.test_jobs import STAGES, _meeting, _restore, _runner


def test_cancel_marks_the_job_cancelling_until_the_stage_lets_go(db, cfg):
    _meeting(db, cfg)
    r = _runner(db, cfg, {n: (lambda ctx: "ok") for n in STAGES})
    try:
        r.enqueue("m1")
        r.cancel("m1")
        assert ms.get_job(db, "m1").state == "cancelling"
        r._run("m1", list(STAGES))          # its turn comes: nothing runs, the previous result is kept
    finally:
        _restore()
    job = ms.get_job(db, "m1")
    assert job.state in ("ready", "failed") and job.current_stage is None
    assert "m1" not in r._cancelled


class _FakeStream:
    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = lines
        self.text = ""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        pass
    def raise_for_status(self):
        pass
    def iter_lines(self):
        yield from self._lines


def test_streamed_answer_is_joined_and_cut_off_on_cancel(monkeypatch):
    lines = [json.dumps({"message": {"content": w}, "done": False}) for w in ("Hel", "lo ", "world")] + [json.dumps({"message": {"content": ""}, "done": True})]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(lines))
    p = OllamaProvider("m", base_url="http://x")
    assert p.complete("s", "u") == "Hello world"
    seen = {"n": 0}
    def cancelled():
        seen["n"] += 1
        return seen["n"] > 2
    p.cancelled = cancelled
    try:
        p.complete("s", "u")
        raise AssertionError("expected LlmCancelled")
    except LlmCancelled:
        pass
