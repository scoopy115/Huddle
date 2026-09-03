import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="huddle-test-"))
os.environ["HUDDLE_DATA_DIR"] = str(_TMP)
os.environ.pop("HUDDLE_TOKEN", None)

from huddle_engine.db import Database  # noqa: E402
from huddle_engine.settings import EngineConfig  # noqa: E402


@pytest.fixture
def cfg(tmp_path) -> EngineConfig:
    c = EngineConfig(data_dir=tmp_path / "data")
    c.ensure_dirs()
    return c


@pytest.fixture
def db(cfg) -> Database:
    d = Database(cfg.db_path)
    yield d
    d.close()
