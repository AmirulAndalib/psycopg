# WARNING: this file is auto-generated by 'async_to_sync.py'
# from the original file 'test_pool_null_async.py'
# DO NOT CHANGE! Change the original file instead.
from __future__ import annotations

import logging
from typing import Any

import pytest
from packaging.version import parse as ver  # noqa: F401  # used in skipif

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import Row, TupleRow, class_row

from ..utils import assert_type, set_autocommit
from ..acompat import Event, gather, skip_sync, sleep, spawn
from .test_pool_common import delay_connection, ensure_waiting

try:
    import psycopg_pool as pool
except ImportError:
    # Tests should have been skipped if the package is not available
    pass


def test_default_sizes(dsn):
    with pool.NullConnectionPool(dsn) as p:
        assert p.min_size == p.max_size == 0


def test_min_size_max_size(dsn):
    with pool.NullConnectionPool(dsn, min_size=0, max_size=2) as p:
        assert p.min_size == 0
        assert p.max_size == 2


@pytest.mark.parametrize("min_size, max_size", [(1, None), (-1, None), (0, -2)])
def test_bad_size(dsn, min_size, max_size):
    with pytest.raises(ValueError):
        pool.NullConnectionPool(min_size=min_size, max_size=max_size)


class MyRow(dict[str, Any]):
    pass


def test_generic_connection_type(dsn):

    def configure(conn: psycopg.Connection[Any]) -> None:
        set_autocommit(conn, True)

    class MyConnection(psycopg.Connection[Row]):
        pass

    with pool.NullConnectionPool(
        dsn,
        connection_class=MyConnection[MyRow],
        kwargs={"row_factory": class_row(MyRow)},
        configure=configure,
    ) as p1:
        with p1.connection() as conn1:
            cur1 = conn1.execute("select 1 as x")
            (row1,) = cur1.fetchall()
    assert_type(p1, pool.NullConnectionPool[MyConnection[MyRow]])
    assert_type(conn1, MyConnection[MyRow])
    assert_type(row1, MyRow)
    assert conn1.autocommit
    assert row1 == {"x": 1}

    with pool.NullConnectionPool(dsn, connection_class=MyConnection[TupleRow]) as p2:
        with p2.connection() as conn2:
            cur2 = conn2.execute("select 2 as y")
            (row2,) = cur2.fetchall()
    assert_type(p2, pool.NullConnectionPool[MyConnection[TupleRow]])
    assert_type(conn2, MyConnection[TupleRow])
    assert_type(row2, TupleRow)
    assert row2 == (2,)


def test_non_generic_connection_type(dsn):

    def configure(conn: psycopg.Connection[Any]) -> None:
        set_autocommit(conn, True)

    class MyConnection(psycopg.Connection[MyRow]):

        def __init__(self, *args: Any, **kwargs: Any):
            kwargs["row_factory"] = class_row(MyRow)
            super().__init__(*args, **kwargs)

    with pool.NullConnectionPool(
        dsn, connection_class=MyConnection, configure=configure
    ) as p1:
        with p1.connection() as conn1:
            (row1,) = conn1.execute("select 1 as x").fetchall()
    assert_type(p1, pool.NullConnectionPool[MyConnection])
    assert_type(conn1, MyConnection)
    assert_type(row1, MyRow)
    assert conn1.autocommit
    assert row1 == {"x": 1}


@pytest.mark.crdb_skip("backend pid")
def test_its_no_pool_at_all(dsn):
    with pool.NullConnectionPool(dsn, max_size=2) as p:
        with p.connection() as conn:
            pid1 = conn.info.backend_pid

            with p.connection() as conn2:
                pid2 = conn2.info.backend_pid

        with p.connection() as conn:
            assert conn.info.backend_pid not in (pid1, pid2)


@pytest.mark.slow
@pytest.mark.timing
def test_wait_ready(dsn, monkeypatch):
    delay_connection(monkeypatch, 0.2)
    with pytest.raises(pool.PoolTimeout):
        with pool.NullConnectionPool(dsn, num_workers=1) as p:
            p.wait(0.1)

    with pool.NullConnectionPool(dsn, num_workers=1) as p:
        p.wait(0.4)


def test_configure(dsn):
    inits = 0

    def configure(conn):
        nonlocal inits
        inits += 1
        with conn.transaction():
            conn.execute("set default_transaction_read_only to on")

    with pool.NullConnectionPool(dsn, configure=configure) as p:
        with p.connection() as conn:
            assert inits == 1
            res = conn.execute("show default_transaction_read_only")
            assert res.fetchone() == ("on",)

        with p.connection() as conn:
            assert inits == 2
            res = conn.execute("show default_transaction_read_only")
            assert res.fetchone() == ("on",)
            conn.close()

        with p.connection() as conn:
            assert inits == 3
            res = conn.execute("show default_transaction_read_only")
            assert res.fetchone() == ("on",)


@pytest.mark.crdb_skip("backend pid")
def test_reset(dsn):
    resets = 0

    def setup(conn):
        with conn.transaction():
            conn.execute("set timezone to '+1:00'")

    def reset(conn):
        nonlocal resets
        resets += 1
        with conn.transaction():
            conn.execute("set timezone to utc")

    pids = []

    def worker():
        with p.connection() as conn:
            assert resets == 1
            cur = conn.execute("show timezone")
            assert cur.fetchone() == ("UTC",)
            pids.append(conn.info.backend_pid)

    with pool.NullConnectionPool(dsn, max_size=1, reset=reset) as p:
        with p.connection() as conn:
            # Queue the worker so it will take the same connection a second time
            # instead of making a new one.
            t = spawn(worker)
            ensure_waiting(p)

            assert resets == 0
            conn.execute("set timezone to '+2:00'")
            pids.append(conn.info.backend_pid)

        gather(t)
        p.wait()

    assert resets == 1
    assert pids[0] == pids[1]


@pytest.mark.crdb_skip("backend pid")
def test_reset_badstate(dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")

    def reset(conn):
        conn.execute("reset all")

    pids = []

    def worker():
        with p.connection() as conn:
            conn.execute("select 1")
            pids.append(conn.info.backend_pid)

    with pool.NullConnectionPool(dsn, max_size=1, reset=reset) as p:
        with p.connection() as conn:
            t = spawn(worker)
            ensure_waiting(p)

            conn.execute("select 1")
            pids.append(conn.info.backend_pid)

        gather(t)

    assert pids[0] != pids[1]
    assert caplog.records
    assert "INTRANS" in caplog.records[0].message


@pytest.mark.crdb_skip("backend pid")
def test_reset_broken(dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")

    def reset(conn):
        with conn.transaction():
            conn.execute("WAT")

    pids = []

    def worker():
        with p.connection() as conn:
            conn.execute("select 1")
            pids.append(conn.info.backend_pid)

    with pool.NullConnectionPool(dsn, max_size=1, reset=reset) as p:
        with p.connection() as conn:
            t = spawn(worker)
            ensure_waiting(p)

            conn.execute("select 1")
            pids.append(conn.info.backend_pid)

        gather(t)

    assert pids[0] != pids[1]
    assert caplog.records
    assert "WAT" in caplog.records[0].message


@pytest.mark.slow
@pytest.mark.skipif("ver(psycopg.__version__) < ver('3.0.8')")
def test_no_queue_timeout(proxy):
    with pool.NullConnectionPool(
        kwargs={"host": proxy.client_host, "port": proxy.client_port}
    ) as p:
        with proxy.deaf_listen(), pytest.raises(pool.PoolTimeout):
            with p.connection(timeout=1):
                pass


@pytest.mark.crdb_skip("backend pid")
def test_intrans_rollback(dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    pids = []

    def worker():
        with p.connection() as conn:
            pids.append(conn.info.backend_pid)
            assert conn.info.transaction_status == TransactionStatus.IDLE
            cur = conn.execute(
                "select 1 from pg_class where relname = 'test_intrans_rollback'"
            )
            assert not cur.fetchone()

    with pool.NullConnectionPool(dsn, max_size=1) as p:
        conn = p.getconn()

        # Queue the worker so it will take the connection a second time instead
        # of making a new one.
        t = spawn(worker)
        ensure_waiting(p)

        pids.append(conn.info.backend_pid)
        conn.execute("create table test_intrans_rollback ()")
        assert conn.info.transaction_status == TransactionStatus.INTRANS
        p.putconn(conn)
        gather(t)

    assert pids[0] == pids[1]
    assert len(caplog.records) == 1
    assert "INTRANS" in caplog.records[0].message


@pytest.mark.crdb_skip("backend pid")
def test_inerror_rollback(dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    pids = []

    def worker():
        with p.connection() as conn:
            pids.append(conn.info.backend_pid)
            assert conn.info.transaction_status == TransactionStatus.IDLE

    with pool.NullConnectionPool(dsn, max_size=1) as p:
        conn = p.getconn()

        # Queue the worker so it will take the connection a second time instead
        # of making a new one.
        t = spawn(worker)
        ensure_waiting(p)

        pids.append(conn.info.backend_pid)
        with pytest.raises(psycopg.ProgrammingError):
            conn.execute("wat")
        assert conn.info.transaction_status == TransactionStatus.INERROR
        p.putconn(conn)
        gather(t)

    assert pids[0] == pids[1]
    assert len(caplog.records) == 1
    assert "INERROR" in caplog.records[0].message


@pytest.mark.crdb_skip("backend pid")
@pytest.mark.crdb_skip("copy")
def test_active_close(dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    pids = []

    def worker():
        with p.connection() as conn:
            pids.append(conn.info.backend_pid)
            assert conn.info.transaction_status == TransactionStatus.IDLE

    with pool.NullConnectionPool(dsn, max_size=1) as p:
        conn = p.getconn()

        t = spawn(worker)
        ensure_waiting(p)

        pids.append(conn.info.backend_pid)
        conn.pgconn.exec_(b"copy (select * from generate_series(1, 10)) to stdout")
        assert conn.info.transaction_status == TransactionStatus.ACTIVE
        p.putconn(conn)
        gather(t)

    assert pids[0] != pids[1]
    assert len(caplog.records) == 2
    assert "ACTIVE" in caplog.records[0].message
    assert "BAD" in caplog.records[1].message


@pytest.mark.crdb_skip("backend pid")
def test_fail_rollback_close(dsn, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    pids = []

    def worker():
        with p.connection() as conn:
            pids.append(conn.info.backend_pid)
            assert conn.info.transaction_status == TransactionStatus.IDLE

    with pool.NullConnectionPool(dsn, max_size=1) as p:
        conn = p.getconn()
        t = spawn(worker)
        ensure_waiting(p)

        def bad_rollback():
            conn.pgconn.finish()
            orig_rollback()

        # Make the rollback fail
        orig_rollback = conn.rollback
        monkeypatch.setattr(conn, "rollback", bad_rollback)

        pids.append(conn.info.backend_pid)
        with pytest.raises(psycopg.ProgrammingError):
            conn.execute("wat")
        assert conn.info.transaction_status == TransactionStatus.INERROR
        p.putconn(conn)
        gather(t)

    assert pids[0] != pids[1]
    assert len(caplog.records) == 3
    assert "INERROR" in caplog.records[0].message
    assert "OperationalError" in caplog.records[1].message
    assert "BAD" in caplog.records[2].message


def test_closed_putconn(dsn):
    with pool.NullConnectionPool(dsn) as p:
        with p.connection() as conn:
            pass
        assert conn.closed


@pytest.mark.parametrize("min_size, max_size", [(1, None), (-1, None), (0, -2)])
def test_bad_resize(dsn, min_size, max_size):
    with pool.NullConnectionPool() as p:
        with pytest.raises(ValueError):
            p.resize(min_size=min_size, max_size=max_size)


@pytest.mark.slow
@pytest.mark.timing
@pytest.mark.crdb_skip("backend pid")
def test_max_lifetime(dsn):
    pids: list[int] = []

    def worker():
        with p.connection() as conn:
            pids.append(conn.info.backend_pid)
            sleep(0.1)

    with pool.NullConnectionPool(dsn, max_size=1, max_lifetime=0.2) as p:
        ts = [spawn(worker) for i in range(5)]
        gather(*ts)

    assert pids[0] == pids[1] != pids[4], pids


def test_check(dsn):
    # no.op
    with pool.NullConnectionPool(dsn) as p:
        p.check()


@pytest.mark.slow
def test_stats_connect(dsn, proxy, monkeypatch):
    proxy.start()
    delay_connection(monkeypatch, 0.2)
    with pool.NullConnectionPool(proxy.client_dsn, max_size=3) as p:
        p.wait()
        stats = p.get_stats()
        assert stats["connections_num"] == 1
        assert stats.get("connections_errors", 0) == 0
        assert stats.get("connections_lost", 0) == 0
        assert 200 <= stats["connections_ms"] < 300


@skip_sync
def test_cancellation_in_queue(dsn):
    # https://github.com/psycopg/psycopg/issues/509

    nconns = 3

    with pool.NullConnectionPool(dsn, min_size=0, max_size=nconns, timeout=1) as p:
        p.wait()

        got_conns = []
        ev = Event()

        def worker(i):
            try:
                logging.info("worker %s started", i)
                with p.connection() as conn:
                    logging.info("worker %s got conn", i)
                    cur = conn.execute("select 1")
                    assert cur.fetchone() == (1,)

                    got_conns.append(conn)
                    if len(got_conns) >= nconns:
                        ev.set()

                    sleep(5)
            except BaseException as ex:
                logging.info("worker %s stopped: %r", i, ex)
                raise

        # Start tasks taking up all the connections and getting in the queue
        tasks = [spawn(worker, (i,)) for i in range(nconns * 3)]

        # wait until the pool has served all the connections and clients are queued.
        ev.wait(3.0)
        for i in range(10):
            if p.get_stats().get("requests_queued", 0):
                break
            else:
                sleep(0.1)
        else:
            pytest.fail("no client got in the queue")

        [task.cancel() for task in reversed(tasks)]
        gather(*tasks, return_exceptions=True, timeout=1.0)

        stats = p.get_stats()
        assert stats.get("requests_waiting", 0) == 0

        with p.connection() as conn:
            cur = conn.execute("select 1")
            assert cur.fetchone() == (1,)
