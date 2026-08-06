import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aifaq import db as db_module  # noqa: E402
from aifaq.config import Settings  # noqa: E402
from aifaq.repositories import Repositories  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "aifaq.db")


@pytest.fixture
def conn(settings):
    c = db_module.connect(settings)
    db_module.init_db(c)
    yield c
    c.close()


@pytest.fixture
def repos(conn):
    return Repositories.build(conn)
