from __future__ import annotations

import sys
import time
import logging
import weakref
from typing import Any

import pytest

import psycopg
from psycopg import errors as e
from psycopg import pq
from psycopg.rows import tuple_row
from psycopg.conninfo import conninfo_to_dict, timeout_from_conninfo

from .acompat import asleep, skip_async, skip_sync
from .fix_crdb import crdb_anydb
from .test_adapt import make_bin_dumper, make_dumper
from ._test_cursor import my_row_factory
from ._test_connection import testctx  # noqa: F401  # fixture
from ._test_connection import conninfo_params_timeout, tx_params, tx_params_isolation
from ._test_connection import tx_values_map

MULTI_FAILURE_MESSAGE = "Multiple connection attempts failed. All failures were:"


async def test_connect(aconn_cls, dsn):
    conn = await aconn_cls.connect(dsn)
    assert not conn.closed
    assert conn.pgconn.status == pq.ConnStatus.OK
    await conn.close()


@crdb_anydb
async def test_connect_bad(aconn_cls, dsn):
    with pytest.raises(psycopg.OperationalError):
        await aconn_cls.connect(dsn, dbname="nosuchdb")


@pytest.mark.slow
async def test_connect_error_single_host_original_message_preserved(aconn_cls, proxy):
    with proxy.deaf_listen():
        with pytest.raises(psycopg.OperationalError) as e:
            await aconn_cls.connect(proxy.client_dsn, connect_timeout=2)

    msg = str(e)
    assert "connection timeout expired" in msg
    assert MULTI_FAILURE_MESSAGE not in msg


@pytest.mark.slow
async def test_connect_error_multi_hosts_each_message_preserved(aconn_cls):
    args = {
        # IPv4 address blocks reserved for documentation.
        # https://datatracker.ietf.org/doc/rfc5737/
        "host": "192.0.2.1,198.51.100.1",
        "port": "1234,5678",
    }
    with pytest.raises(psycopg.OperationalError) as e:
        await aconn_cls.connect(**args, connect_timeout=2)

    msg = str(e.value)
    assert MULTI_FAILURE_MESSAGE in msg

    host1, host2 = args["host"].split(",")
    port1, port2 = args["port"].split(",")

    msg_lines = msg.splitlines()

    expected_host1 = f"host: '{host1}', port: '{port1}', hostaddr: '{host1}'"
    expected_host2 = f"host: '{host2}', port: '{port2}', hostaddr: '{host2}'"
    expected_error = "connection timeout expired"

    assert any(expected_host1 in line and expected_error in line for line in msg_lines)
    assert any(expected_host2 in line and expected_error in line for line in msg_lines)


async def test_connect_str_subclass(aconn_cls, dsn):
    class MyString(str):
        pass

    conn = await aconn_cls.connect(MyString(dsn))
    assert not conn.closed
    assert conn.pgconn.status == pq.ConnStatus.OK
    await conn.close()


@pytest.mark.slow
@pytest.mark.timing
async def test_connect_timeout(aconn_cls, proxy):
    with proxy.deaf_listen():
        t0 = time.time()
        with pytest.raises(psycopg.OperationalError, match="timeout expired"):
            await aconn_cls.connect(proxy.client_dsn, connect_timeout=2)
        elapsed = time.time() - t0
    assert elapsed == pytest.approx(2.0, 0.1)


@pytest.mark.slow
@pytest.mark.timing
async def test_multi_hosts(aconn_cls, proxy, dsn, monkeypatch):
    args = conninfo_to_dict(dsn)
    args["host"] = f"{proxy.client_host},{proxy.server_host}"
    args["port"] = f"{proxy.client_port},{proxy.server_port}"
    args.pop("hostaddr", None)
    monkeypatch.setattr(psycopg.conninfo, "_DEFAULT_CONNECT_TIMEOUT", 2)
    with proxy.deaf_listen():
        t0 = time.time()
        async with await aconn_cls.connect(**args) as conn:
            elapsed = time.time() - t0
            assert elapsed == pytest.approx(2.0, 0.1)
            assert conn.info.port == int(proxy.server_port)
            assert conn.info.host == proxy.server_host


@pytest.mark.slow
@pytest.mark.timing
async def test_multi_hosts_timeout(aconn_cls, proxy, dsn):
    args = conninfo_to_dict(dsn)
    args["host"] = f"{proxy.client_host},{proxy.server_host}"
    args["port"] = f"{proxy.client_port},{proxy.server_port}"
    args.pop("hostaddr", None)
    args["connect_timeout"] = "2"
    with proxy.deaf_listen():
        t0 = time.time()
        async with await aconn_cls.connect(**args) as conn:
            elapsed = time.time() - t0
            assert elapsed == pytest.approx(2.0, 0.1)
            assert conn.info.port == int(proxy.server_port)
            assert conn.info.host == proxy.server_host


async def test_close(aconn):
    assert not aconn.closed
    assert not aconn.broken

    cur = aconn.cursor()

    await aconn.close()
    assert aconn.closed
    assert not aconn.broken
    assert aconn.pgconn.status == pq.ConnStatus.BAD

    await aconn.close()
    assert aconn.closed
    assert aconn.pgconn.status == pq.ConnStatus.BAD

    with pytest.raises(psycopg.OperationalError):
        await cur.execute("select 1")


@pytest.mark.crdb_skip("pg_terminate_backend")
async def test_broken(aconn):
    with pytest.raises(psycopg.OperationalError):
        await aconn.execute(
            "select pg_terminate_backend(%s)", [aconn.pgconn.backend_pid]
        )
    assert aconn.closed
    assert aconn.broken
    await aconn.close()
    assert aconn.closed
    assert aconn.broken


async def test_cursor_closed(aconn):
    await aconn.close()
    with pytest.raises(psycopg.OperationalError):
        async with aconn.cursor("foo"):
            pass
    with pytest.raises(psycopg.OperationalError):
        aconn.cursor("foo")
    with pytest.raises(psycopg.OperationalError):
        aconn.cursor()


# TODO: the INERROR started failing in the C implementation in Python 3.12a7
# compiled with Cython-3.0.0b3, not before.
@pytest.mark.slow
async def test_connection_warn_close(aconn_cls, dsn, recwarn, gc_collect):
    # First create all the connections to test, to avoid some to reuse the same
    # address, resulting in an omitted warning.
    conn1 = await aconn_cls.connect(dsn)
    conn2 = await aconn_cls.connect(dsn)
    conn3 = await aconn_cls.connect(dsn)
    conn4 = await aconn_cls.connect(dsn)

    async with await aconn_cls.connect(dsn) as conn5:
        pass
    del conn5
    assert not recwarn, [str(w.message) for w in recwarn.list]

    await conn1.close()
    del conn1
    assert not recwarn, [str(w.message) for w in recwarn.list]

    del conn2
    gc_collect()
    assert aconn_cls.__name__ in str(recwarn.pop(ResourceWarning).message)

    await conn3.execute("select 1")
    del conn3
    gc_collect()
    assert aconn_cls.__name__ in str(recwarn.pop(ResourceWarning).message)

    try:
        await conn4.execute("select wat")
    except psycopg.ProgrammingError:
        pass
    del conn4
    gc_collect()
    assert aconn_cls.__name__ in str(recwarn.pop(ResourceWarning).message)


@pytest.mark.usefixtures("testctx")
async def test_context_commit(aconn_cls, aconn, dsn):
    async with aconn:
        async with aconn.cursor() as cur:
            await cur.execute("insert into testctx values (42)")

    assert aconn.closed
    assert not aconn.broken

    async with await aconn_cls.connect(dsn) as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("select * from testctx")
            assert await cur.fetchall() == [(42,)]


@pytest.mark.usefixtures("testctx")
async def test_context_rollback(aconn_cls, aconn, dsn):
    with pytest.raises(ZeroDivisionError):
        async with aconn:
            async with aconn.cursor() as cur:
                await cur.execute("insert into testctx values (42)")
                1 / 0

    assert aconn.closed
    assert not aconn.broken

    async with await aconn_cls.connect(dsn) as aconn:
        async with aconn.cursor() as cur:
            await cur.execute("select * from testctx")
            assert await cur.fetchall() == []


async def test_context_close(aconn):
    async with aconn:
        await aconn.execute("select 1")
        await aconn.close()


@pytest.mark.crdb_skip("pg_terminate_backend")
async def test_context_inerror_rollback_no_clobber(aconn_cls, conn, dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg")

    with pytest.raises(ZeroDivisionError):
        async with await aconn_cls.connect(dsn) as conn2:
            await conn2.execute("select 1")
            conn.execute(
                "select pg_terminate_backend(%s::int)",
                [conn2.pgconn.backend_pid],
            )
            1 / 0

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert "in rollback" in rec.message


@pytest.mark.crdb_skip("copy")
async def test_context_active_rollback_no_clobber(aconn_cls, dsn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg")

    with pytest.raises(ZeroDivisionError):
        async with await aconn_cls.connect(dsn) as conn:
            conn.pgconn.exec_(b"copy (select generate_series(1, 10)) to stdout")
            assert not conn.pgconn.error_message
            status = conn.info.transaction_status
            assert status == pq.TransactionStatus.ACTIVE
            1 / 0

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert "in rollback" in rec.message


@pytest.mark.slow
async def test_weakref(aconn_cls, dsn, gc_collect):
    conn = await aconn_cls.connect(dsn)
    w = weakref.ref(conn)
    await conn.close()
    del conn
    gc_collect()
    assert w() is None


async def test_commit(aconn):
    aconn.pgconn.exec_(b"drop table if exists foo")
    aconn.pgconn.exec_(b"create table foo (id int primary key)")
    aconn.pgconn.exec_(b"begin")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS
    aconn.pgconn.exec_(b"insert into foo values (1)")
    await aconn.commit()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE
    res = aconn.pgconn.exec_(b"select id from foo where id = 1")
    assert res.get_value(0, 0) == b"1"

    await aconn.close()
    with pytest.raises(psycopg.OperationalError):
        await aconn.commit()


@pytest.mark.crdb_skip("deferrable")
async def test_commit_error(aconn):
    await aconn.execute(
        """
        drop table if exists selfref;
        create table selfref (
            x serial primary key,
            y int references selfref (x) deferrable initially deferred)
        """
    )
    await aconn.commit()

    await aconn.execute("insert into selfref (y) values (-1)")
    with pytest.raises(e.ForeignKeyViolation):
        await aconn.commit()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE
    cur = await aconn.execute("select 1")
    assert await cur.fetchone() == (1,)


async def test_rollback(aconn):
    aconn.pgconn.exec_(b"drop table if exists foo")
    aconn.pgconn.exec_(b"create table foo (id int primary key)")
    aconn.pgconn.exec_(b"begin")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS
    aconn.pgconn.exec_(b"insert into foo values (1)")
    await aconn.rollback()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE
    res = aconn.pgconn.exec_(b"select id from foo where id = 1")
    assert res.ntuples == 0

    await aconn.close()
    with pytest.raises(psycopg.OperationalError):
        await aconn.rollback()


async def test_auto_transaction(aconn):
    aconn.pgconn.exec_(b"drop table if exists foo")
    aconn.pgconn.exec_(b"create table foo (id int primary key)")

    cur = aconn.cursor()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE

    await cur.execute("insert into foo values (1)")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS

    await aconn.commit()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE
    await cur.execute("select * from foo")
    assert await cur.fetchone() == (1,)
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS


async def test_auto_transaction_fail(aconn):
    aconn.pgconn.exec_(b"drop table if exists foo")
    aconn.pgconn.exec_(b"create table foo (id int primary key)")

    cur = aconn.cursor()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE

    await cur.execute("insert into foo values (1)")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS

    with pytest.raises(psycopg.DatabaseError):
        await cur.execute("meh")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INERROR

    with pytest.raises(psycopg.errors.InFailedSqlTransaction):
        await cur.execute("select 1")

    await aconn.commit()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE
    await cur.execute("select * from foo")
    assert await cur.fetchone() is None
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS


@skip_sync
async def test_autocommit_readonly_property(aconn):
    with pytest.raises(AttributeError):
        aconn.autocommit = True
    assert not aconn.autocommit


async def test_autocommit(aconn):
    assert aconn.autocommit is False
    await aconn.set_autocommit(True)
    assert aconn.autocommit
    cur = aconn.cursor()
    await cur.execute("select 1")
    assert await cur.fetchone() == (1,)
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.IDLE

    await aconn.set_autocommit("")
    assert isinstance(aconn.autocommit, bool)
    assert aconn.autocommit is False

    await aconn.set_autocommit("yeah")
    assert isinstance(aconn.autocommit, bool)
    assert aconn.autocommit is True


@skip_async
def test_autocommit_property(conn):
    assert conn.autocommit is False

    conn.autocommit = True
    assert conn.autocommit
    cur = conn.cursor()
    cur.execute("select 1")
    assert cur.fetchone() == (1,)
    assert conn.pgconn.transaction_status == pq.TransactionStatus.IDLE

    conn.autocommit = ""
    assert isinstance(conn.autocommit, bool)
    assert conn.autocommit is False

    conn.autocommit = "yeah"
    assert isinstance(conn.autocommit, bool)
    assert conn.autocommit is True


async def test_autocommit_connect(aconn_cls, dsn):
    aconn = await aconn_cls.connect(dsn, autocommit=True)
    assert aconn.autocommit
    await aconn.close()


async def test_autocommit_intrans(aconn):
    cur = aconn.cursor()
    await cur.execute("select 1")
    assert await cur.fetchone() == (1,)
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INTRANS
    with pytest.raises(psycopg.ProgrammingError):
        await aconn.set_autocommit(True)
    assert not aconn.autocommit


async def test_autocommit_inerror(aconn):
    cur = aconn.cursor()
    with pytest.raises(psycopg.DatabaseError):
        await cur.execute("meh")
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.INERROR
    with pytest.raises(psycopg.ProgrammingError):
        await aconn.set_autocommit(True)
    assert not aconn.autocommit


async def test_autocommit_unknown(aconn):
    await aconn.close()
    assert aconn.pgconn.transaction_status == pq.TransactionStatus.UNKNOWN
    with pytest.raises(psycopg.OperationalError):
        await aconn.set_autocommit(True)
    assert not aconn.autocommit


@pytest.mark.parametrize(
    "args, kwargs, want",
    [
        ((), {}, ""),
        (("",), {}, ""),
        (("host=foo.com user=bar",), {}, "host=foo.com user=bar hostaddr=1.1.1.1"),
        (("host=foo.com",), {"user": "baz"}, "host=foo.com user=baz hostaddr=1.1.1.1"),
        (
            ("dbname=foo port=5433",),
            {"dbname": "qux", "user": "joe"},
            "dbname=qux user=joe port=5433",
        ),
        (("host=foo.com",), {"user": None}, "host=foo.com hostaddr=1.1.1.1"),
    ],
)
async def test_connect_args(
    aconn_cls, monkeypatch, setpgenv, pgconn, fake_resolve, args, kwargs, want
):
    got_conninfo: str

    def fake_connect(conninfo, *, timeout=0.0):
        nonlocal got_conninfo
        got_conninfo = conninfo
        return pgconn
        yield

    setpgenv({})
    monkeypatch.setattr(psycopg.generators, "connect", fake_connect)
    conn = await aconn_cls.connect(*args, **kwargs)
    assert conninfo_to_dict(got_conninfo) == conninfo_to_dict(want)
    await conn.close()


@pytest.mark.parametrize(
    "args, kwargs, exctype",
    [
        (("host=foo", "host=bar"), {}, TypeError),
        (("", ""), {}, TypeError),
        ((), {"nosuchparam": 42}, psycopg.ProgrammingError),
    ],
)
async def test_connect_badargs(aconn_cls, monkeypatch, pgconn, args, kwargs, exctype):
    with pytest.raises(exctype):
        await aconn_cls.connect(*args, **kwargs)


@pytest.mark.crdb_skip("pg_terminate_backend")
async def test_broken_connection(aconn):
    cur = aconn.cursor()
    with pytest.raises(psycopg.DatabaseError):
        await cur.execute("select pg_terminate_backend(pg_backend_pid())")
    assert aconn.closed


@pytest.mark.crdb_skip("do")
async def test_notice_handlers(aconn, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg")
    messages = []
    severities = []

    def cb1(diag):
        messages.append(diag.message_primary)

    def cb2(res):
        raise Exception("hello from cb2")

    aconn.add_notice_handler(cb1)
    aconn.add_notice_handler(cb2)
    aconn.add_notice_handler("the wrong thing")
    aconn.add_notice_handler(lambda diag: severities.append(diag.severity_nonlocalized))

    aconn.pgconn.exec_(b"set client_min_messages to notice")
    cur = aconn.cursor()
    await cur.execute("do $$begin raise notice 'hello notice'; end$$ language plpgsql")
    assert messages == ["hello notice"]
    assert severities == ["NOTICE"]

    assert len(caplog.records) == 2
    rec = caplog.records[0]
    assert rec.levelno == logging.ERROR
    assert "hello from cb2" in rec.message
    rec = caplog.records[1]
    assert rec.levelno == logging.ERROR
    assert "the wrong thing" in rec.message

    aconn.remove_notice_handler(cb1)
    aconn.remove_notice_handler("the wrong thing")
    await cur.execute(
        "do $$begin raise warning 'hello warning'; end$$ language plpgsql"
    )
    assert len(caplog.records) == 3
    assert messages == ["hello notice"]
    assert severities == ["NOTICE", "WARNING"]

    with pytest.raises(ValueError):
        aconn.remove_notice_handler(cb1)


async def test_execute(aconn):
    cur = await aconn.execute("select %s, %s", [10, 20])
    assert await cur.fetchone() == (10, 20)
    assert cur.format == 0
    assert cur.pgresult.fformat(0) == 0

    cur = await aconn.execute("select %(a)s, %(b)s", {"a": 11, "b": 21})
    assert await cur.fetchone() == (11, 21)

    cur = await aconn.execute("select 12, 22")
    assert await cur.fetchone() == (12, 22)


async def test_execute_binary(aconn):
    cur = await aconn.execute("select %s, %s", [10, 20], binary=True)
    assert await cur.fetchone() == (10, 20)
    assert cur.format == 1
    assert cur.pgresult.fformat(0) == 1


async def test_row_factory(aconn_cls, dsn):
    defaultconn = await aconn_cls.connect(dsn)
    assert defaultconn.row_factory is tuple_row
    await defaultconn.close()

    conn = await aconn_cls.connect(dsn, row_factory=my_row_factory)
    assert conn.row_factory is my_row_factory

    cur = await conn.execute("select 'a' as ve")
    assert await cur.fetchone() == ["Ave"]

    async with conn.cursor(row_factory=lambda c: lambda t: set(t)) as cur1:
        await cur1.execute("select 1, 1, 2")
        assert await cur1.fetchall() == [{1, 2}]

    async with conn.cursor(row_factory=tuple_row) as cur2:
        await cur2.execute("select 1, 1, 2")
        assert await cur2.fetchall() == [(1, 1, 2)]

    # TODO: maybe fix something to get rid of 'type: ignore' below.
    conn.row_factory = tuple_row
    cur3 = await conn.execute("select 'vale'")
    r = await cur3.fetchone()
    assert r and r == ("vale",)
    await conn.close()


async def test_str(aconn):
    assert "[IDLE]" in str(aconn)
    await aconn.close()
    assert "[BAD]" in str(aconn)


async def test_fileno(aconn):
    assert aconn.fileno() == aconn.pgconn.socket
    await aconn.close()
    with pytest.raises(psycopg.OperationalError):
        aconn.fileno()


async def test_cursor_factory(aconn):
    assert aconn.cursor_factory is psycopg.AsyncCursor

    class MyCursor(psycopg.AsyncCursor[psycopg.rows.Row]):
        pass

    aconn.cursor_factory = MyCursor
    async with aconn.cursor() as cur:
        assert isinstance(cur, MyCursor)

    async with await aconn.execute("select 1") as cur:
        assert isinstance(cur, MyCursor)


async def test_cursor_factory_connect(aconn_cls, dsn):
    class MyCursor(psycopg.AsyncCursor[psycopg.rows.Row]):
        pass

    async with await aconn_cls.connect(dsn, cursor_factory=MyCursor) as conn:
        assert conn.cursor_factory is MyCursor
        cur = conn.cursor()
        assert type(cur) is MyCursor


async def test_server_cursor_factory(aconn):
    assert aconn.server_cursor_factory is psycopg.AsyncServerCursor

    class MyServerCursor(psycopg.AsyncServerCursor[psycopg.rows.Row]):
        pass

    aconn.server_cursor_factory = MyServerCursor
    async with aconn.cursor(name="n") as cur:
        assert isinstance(cur, MyServerCursor)


@pytest.mark.parametrize("param", tx_params)
async def test_transaction_param_default(aconn, param):
    assert getattr(aconn, param.name) is None
    cur = await aconn.execute(
        "select current_setting(%s), current_setting(%s)",
        [f"transaction_{param.guc}", f"default_transaction_{param.guc}"],
    )
    current, default = await cur.fetchone()
    assert current == default


@skip_sync
@pytest.mark.parametrize("param", tx_params)
async def test_transaction_param_readonly_property(aconn, param):
    with pytest.raises(AttributeError):
        setattr(aconn, param.name, None)


@pytest.mark.parametrize("autocommit", [True, False])
@pytest.mark.parametrize("param", tx_params_isolation)
async def test_set_transaction_param_implicit(aconn, param, autocommit):
    await aconn.set_autocommit(autocommit)
    for value in param.values:
        await getattr(aconn, f"set_{param.name}")(value)
        cur = await aconn.execute(
            "select current_setting(%s), current_setting(%s)",
            [f"transaction_{param.guc}", f"default_transaction_{param.guc}"],
        )
        pgval, default = await cur.fetchone()
        if autocommit:
            assert pgval == default
        else:
            assert tx_values_map[pgval] == value
        await aconn.rollback()


@pytest.mark.parametrize("param", tx_params_isolation)
async def test_set_transaction_param_reset(aconn, param):
    await aconn.execute(
        "select set_config(%s, %s, false)",
        [f"default_transaction_{param.guc}", param.non_default],
    )
    await aconn.commit()

    for value in param.values:
        await getattr(aconn, f"set_{param.name}")(value)
        cur = await aconn.execute(
            "select current_setting(%s)", [f"transaction_{param.guc}"]
        )
        (pgval,) = await cur.fetchone()
        assert tx_values_map[pgval] == value
        await aconn.rollback()

        await getattr(aconn, f"set_{param.name}")(None)
        cur = await aconn.execute(
            "select current_setting(%s)", [f"transaction_{param.guc}"]
        )
        (pgval,) = await cur.fetchone()
        assert tx_values_map[pgval] == tx_values_map[param.non_default]
        await aconn.rollback()


@pytest.mark.parametrize("autocommit", [True, False])
@pytest.mark.parametrize("param", tx_params_isolation)
async def test_set_transaction_param_block(aconn, param, autocommit):
    await aconn.set_autocommit(autocommit)
    for value in param.values:
        await getattr(aconn, f"set_{param.name}")(value)
        async with aconn.transaction():
            cur = await aconn.execute(
                "select current_setting(%s)", [f"transaction_{param.guc}"]
            )
            pgval = (await cur.fetchone())[0]
        assert tx_values_map[pgval] == value


@pytest.mark.parametrize("param", tx_params)
async def test_set_transaction_param_not_intrans_implicit(aconn, param):
    await aconn.execute("select 1")
    value = param.values[0]
    with pytest.raises(psycopg.ProgrammingError):
        await getattr(aconn, f"set_{param.name}")(value)


@pytest.mark.parametrize("param", tx_params)
async def test_set_transaction_param_not_intrans_block(aconn, param):
    value = param.values[0]
    async with aconn.transaction():
        with pytest.raises(psycopg.ProgrammingError):
            await getattr(aconn, f"set_{param.name}")(value)


@pytest.mark.parametrize("param", tx_params)
async def test_set_transaction_param_not_intrans_external(aconn, param):
    value = param.values[0]
    await aconn.set_autocommit(True)
    await aconn.execute("begin")
    with pytest.raises(psycopg.ProgrammingError):
        await getattr(aconn, f"set_{param.name}")(value)


@skip_async
@pytest.mark.crdb("skip", reason="transaction isolation")
def test_set_transaction_param_all_property(conn):
    params: list[Any] = tx_params[:]
    params[2] = params[2].values[0]

    for param in params:
        value = param.values[0]
        setattr(conn, param.name, value)

    for param in params:
        cur = conn.execute("select current_setting(%s)", [f"transaction_{param.guc}"])
        pgval = cur.fetchone()[0]
        assert tx_values_map[pgval] == value


@pytest.mark.crdb("skip", reason="transaction isolation")
async def test_set_transaction_param_all(aconn):
    params: list[Any] = tx_params[:]
    params[2] = params[2].values[0]

    for param in params:
        value = param.values[0]
        await getattr(aconn, f"set_{param.name}")(value)

    for param in params:
        cur = await aconn.execute(
            "select current_setting(%s)", [f"transaction_{param.guc}"]
        )
        pgval = (await cur.fetchone())[0]
        assert tx_values_map[pgval] == value


async def test_set_transaction_param_strange(aconn):
    for val in ("asdf", 0, 5):
        with pytest.raises(ValueError):
            await aconn.set_isolation_level(val)

    await aconn.set_isolation_level(psycopg.IsolationLevel.SERIALIZABLE.value)
    assert aconn.isolation_level is psycopg.IsolationLevel.SERIALIZABLE

    await aconn.set_read_only(1)
    assert aconn.read_only is True

    await aconn.set_deferrable(0)
    assert aconn.deferrable is False


@skip_async
def test_set_transaction_param_strange_property(conn):
    for val in ("asdf", 0, 5):
        with pytest.raises(ValueError):
            conn.isolation_level = val

    conn.isolation_level = psycopg.IsolationLevel.SERIALIZABLE.value
    assert conn.isolation_level is psycopg.IsolationLevel.SERIALIZABLE

    conn.read_only = 1
    assert conn.read_only is True

    conn.deferrable = 0
    assert conn.deferrable is False


@pytest.mark.parametrize("dsn, kwargs, exp", conninfo_params_timeout)
async def test_get_connection_params(aconn_cls, dsn, kwargs, exp, setpgenv):
    setpgenv({})
    params = await aconn_cls._get_connection_params(dsn, **kwargs)
    assert params == exp[0]
    assert timeout_from_conninfo(params) == exp[1]


async def test_connect_context_adapters(aconn_cls, dsn):
    ctx = psycopg.adapt.AdaptersMap(psycopg.adapters)
    ctx.register_dumper(str, make_bin_dumper("b"))
    ctx.register_dumper(str, make_dumper("t"))

    conn = await aconn_cls.connect(dsn, context=ctx)

    cur = await conn.execute("select %s", ["hello"])
    assert (await cur.fetchone())[0] == "hellot"
    cur = await conn.execute("select %b", ["hello"])
    assert (await cur.fetchone())[0] == "hellob"
    await conn.close()


async def test_connect_context_copy(aconn_cls, dsn, aconn):
    aconn.adapters.register_dumper(str, make_bin_dumper("b"))
    aconn.adapters.register_dumper(str, make_dumper("t"))

    conn2 = await aconn_cls.connect(dsn, context=aconn)

    cur = await conn2.execute("select %s", ["hello"])
    assert (await cur.fetchone())[0] == "hellot"
    cur = await conn2.execute("select %b", ["hello"])
    assert (await cur.fetchone())[0] == "hellob"
    await conn2.close()


async def test_cancel_closed(aconn):
    await aconn.close()
    aconn.cancel()


async def test_cancel_safe_closed(aconn):
    await aconn.close()
    await aconn.cancel_safe()


@pytest.mark.slow
@pytest.mark.timing
async def test_cancel_safe_error(aconn_cls, proxy, caplog):
    caplog.set_level(logging.WARNING, logger="psycopg")
    proxy.start()
    async with await aconn_cls.connect(proxy.client_dsn) as aconn:
        proxy.stop()
        with pytest.raises(
            e.OperationalError, match=r"(Connection refused)|(connect\(\) failed)"
        ) as ex:
            await aconn.cancel_safe(timeout=2)
        assert not caplog.records

        # Note: testing an internal method. It's ok if this behaviour changes
        await aconn._try_cancel(timeout=2.0)
        assert len(caplog.records) == 1
        caplog.records[0].message == str(ex.value)


@pytest.mark.slow
@pytest.mark.timing
@pytest.mark.libpq(">= 17")
async def test_cancel_safe_timeout(aconn_cls, proxy):
    proxy.start()
    async with await aconn_cls.connect(proxy.client_dsn) as aconn:
        proxy.stop()
        with proxy.deaf_listen():
            t0 = time.time()
            with pytest.raises(e.CancellationTimeout, match="timeout expired"):
                await aconn.cancel_safe(timeout=1)
    elapsed = time.time() - t0
    assert elapsed == pytest.approx(1.0, 0.1)


async def test_resolve_hostaddr_conn(aconn_cls, monkeypatch, fake_resolve):
    got = ""

    def fake_connect_gen(conninfo, **kwargs):
        nonlocal got
        got = conninfo
        1 / 0

    monkeypatch.setattr(aconn_cls, "_connect_gen", fake_connect_gen)

    with pytest.raises(ZeroDivisionError):
        await aconn_cls.connect("host=foo.com")

    assert conninfo_to_dict(got) == {"host": "foo.com", "hostaddr": "1.1.1.1"}


@pytest.mark.crdb_skip("pg_terminate_backend")
async def test_right_exception_on_server_disconnect(aconn):
    with pytest.raises(e.AdminShutdown):
        await aconn.execute(
            "select pg_terminate_backend(%s)", [aconn.pgconn.backend_pid]
        )


@pytest.mark.slow
@pytest.mark.crdb("skip", reason="error result not returned")
async def test_right_exception_on_session_timeout(aconn):
    want_ex: type[psycopg.Error] = e.IdleInTransactionSessionTimeout
    if sys.platform == "win32":
        # No idea why this is needed and `test_right_exception_on_server_disconnect`
        # works instead. Maybe the difference lies in the server we are testing
        # with, not in the client.
        want_ex = psycopg.OperationalError

    await aconn.execute("SET SESSION idle_in_transaction_session_timeout = 100")
    await asleep(0.2)
    with pytest.raises(want_ex) as ex:
        await aconn.execute("SELECT * from pg_tables")

    # This check is here to monitor if the behaviour on Window chamge.
    # Rreceiving the same exception of other platform will be acceptable.
    assert type(ex.value) is want_ex


@pytest.mark.libpq(">= 14")
@pytest.mark.parametrize("mode", ["any", "read-write", "primary", "prefer-standby"])
async def test_connect_tsa(aconn_cls, dsn, mode):
    # NOTE: assume that the test database is a "primary"
    params = conninfo_to_dict(dsn, target_session_attrs=mode)
    async with await aconn_cls.connect(**params) as aconn:
        assert aconn.pgconn.status == pq.ConnStatus.OK


@pytest.mark.libpq(">= 14")
@pytest.mark.parametrize("mode", ["read-only", "standby", "nosuchmode"])
async def test_connect_tsa_bad(aconn_cls, dsn, mode):
    # NOTE: assume that the test database is a "primary"
    params = conninfo_to_dict(dsn, target_session_attrs=mode)
    with pytest.raises(psycopg.OperationalError, match=mode):
        await aconn_cls.connect(**params)
