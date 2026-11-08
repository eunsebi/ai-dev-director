"""
포크: DB 연결 닫기 (2026-11-09)

`with sqlite3.connect(...)`는 트랜잭션만 끝내고 연결을 닫지 않는다. Python 3.14에서는 연결이 순환 참조에 남아
GC가 돌 때까지 파일 핸들이 열려 있어서, 오래 켜 둔 서버가 256개 한도에 닿아 화면 파일을 못 열었다.
"""
import gc
import os
import sqlite3

import pytest

from core import db_path
from core.state_machine import StateMachine
from core.chat_manager import ChatManager
from core.provider_manager import ProviderManager
from core import usage


def _open_fds():
    return len(os.listdir("/dev/fd"))


def test_connect_closes_on_exit_and_rolls_back_on_error(tmp_path):
    db = tmp_path / "x.db"
    with db_path.connect(db) as c:
        c.execute("create table t(a)")
        c.execute("insert into t values (1)")
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("select 1")                         # closed
    with pytest.raises(RuntimeError):
        with db_path.connect(db) as c2:
            c2.execute("insert into t values (2)")
            raise RuntimeError("boom")
    with db_path.connect(db) as c3:
        assert [tuple(r) for r in c3.execute("select a from t")] == [(1,)]
        assert isinstance(c3.execute("select a from t").fetchone(), sqlite3.Row)


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="needs /dev/fd")
def test_repeated_calls_do_not_leave_descriptors_open():
    sm, cm, pm = StateMachine(), ChatManager(), ProviderManager()
    gc.collect()
    gc.disable()
    try:
        before = _open_fds()
        for _ in range(60):
            sm._init_db(); cm._init_db(); pm._sync_from_db()
            with usage._conn() as c:
                c.execute("select 1")
        assert _open_fds() - before < 5
    finally:
        gc.enable()
