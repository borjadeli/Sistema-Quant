import pytest

from qa.db.session import session_scope


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}"


@pytest.fixture
def session(db_url):
    with session_scope(db_url) as s:
        yield s
