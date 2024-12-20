# WARNING: this file is auto-generated by 'async_to_sync.py'
# from the original file 'test_notify_async.py'
# DO NOT CHANGE! Change the original file instead.
from __future__ import annotations

from time import time

import pytest
from psycopg import Notify

from .acompat import sleep, gather, spawn

pytestmark = pytest.mark.crdb_skip("notify")


def test_notify_handlers(conn):
    nots1 = []
    nots2 = []

    def cb1(n):
        nots1.append(n)

    conn.add_notify_handler(cb1)
    conn.add_notify_handler(lambda n: nots2.append(n))

    conn.set_autocommit(True)
    conn.execute("listen foo")
    conn.execute("notify foo, 'n1'")

    assert len(nots1) == 1
    n = nots1[0]
    assert n.channel == "foo"
    assert n.payload == "n1"
    assert n.pid == conn.pgconn.backend_pid

    assert len(nots2) == 1
    assert nots2[0] == nots1[0]

    conn.remove_notify_handler(cb1)
    conn.execute("notify foo, 'n2'")

    assert len(nots1) == 1
    assert len(nots2) == 2
    n = nots2[1]
    assert isinstance(n, Notify)
    assert n.channel == "foo"
    assert n.payload == "n2"
    assert n.pid == conn.pgconn.backend_pid
    assert hash(n)

    with pytest.raises(ValueError):
        conn.remove_notify_handler(cb1)


@pytest.mark.slow
@pytest.mark.timing
def test_notify(conn_cls, conn, dsn):
    npid = None

    def notifier():
        with conn_cls.connect(dsn, autocommit=True) as nconn:
            nonlocal npid
            npid = nconn.pgconn.backend_pid

            sleep(0.25)
            nconn.execute("notify foo, '1'")
            sleep(0.25)
            nconn.execute("notify foo, '2'")

    def receiver():
        conn.set_autocommit(True)
        cur = conn.cursor()
        cur.execute("listen foo")
        gen = conn.notifies()
        for n in gen:
            ns.append((n, time()))
            if len(ns) >= 2:
                gen.close()

    ns: list[tuple[Notify, float]] = []
    t0 = time()
    workers = [spawn(notifier), spawn(receiver)]
    gather(*workers)
    assert len(ns) == 2

    n, t1 = ns[0]
    assert n.pid == npid
    assert n.channel == "foo"
    assert n.payload == "1"
    assert t1 - t0 == pytest.approx(0.25, abs=0.05)

    n, t1 = ns[1]
    assert n.pid == npid
    assert n.channel == "foo"
    assert n.payload == "2"
    assert t1 - t0 == pytest.approx(0.5, abs=0.05)


@pytest.mark.slow
@pytest.mark.timing
def test_no_notify_timeout(conn):
    conn.set_autocommit(True)
    t0 = time()
    for n in conn.notifies(timeout=0.5):
        assert False
    dt = time() - t0
    assert 0.5 <= dt < 0.75


@pytest.mark.slow
@pytest.mark.timing
def test_notify_timeout(conn_cls, conn, dsn):
    conn.set_autocommit(True)
    conn.execute("listen foo")

    def notifier():
        with conn_cls.connect(dsn, autocommit=True) as nconn:
            sleep(0.25)
            nconn.execute("notify foo, '1'")

    worker = spawn(notifier)
    try:
        times = [time()]
        for n in conn.notifies(timeout=0.5):
            times.append(time())
        times.append(time())
    finally:
        gather(worker)

    assert len(times) == 3
    assert times[1] - times[0] == pytest.approx(0.25, 0.1)
    assert times[2] - times[1] == pytest.approx(0.25, 0.1)


@pytest.mark.slow
@pytest.mark.timing
def test_notify_timeout_0(conn_cls, conn, dsn):
    conn.set_autocommit(True)
    conn.execute("listen foo")

    ns = list(conn.notifies(timeout=0))
    assert not ns

    with conn_cls.connect(dsn, autocommit=True) as nconn:
        nconn.execute("notify foo, '1'")
        sleep(0.1)

    ns = list(conn.notifies(timeout=0))
    assert len(ns) == 1


@pytest.mark.slow
@pytest.mark.timing
def test_stop_after(conn_cls, conn, dsn):
    conn.set_autocommit(True)
    conn.execute("listen foo")

    def notifier():
        with conn_cls.connect(dsn, autocommit=True) as nconn:
            nconn.execute("notify foo, '1'")
            sleep(0.1)
            nconn.execute("notify foo, '2'")
            sleep(0.1)
            nconn.execute("notify foo, '3'")

    worker = spawn(notifier)
    try:
        ns = list(conn.notifies(timeout=1.0, stop_after=2))
        assert len(ns) == 2
        assert ns[0].payload == "1"
        assert ns[1].payload == "2"
    finally:
        gather(worker)

    ns = list(conn.notifies(timeout=0.0))
    assert len(ns) == 1
    assert ns[0].payload == "3"


@pytest.mark.timing
def test_stop_after_batch(conn_cls, conn, dsn):
    conn.set_autocommit(True)
    conn.execute("listen foo")

    def notifier():
        with conn_cls.connect(dsn, autocommit=True) as nconn:
            with nconn.transaction():
                nconn.execute("notify foo, '1'")
                nconn.execute("notify foo, '2'")

    worker = spawn(notifier)
    try:
        ns = list(conn.notifies(timeout=1.0, stop_after=1))
        assert len(ns) == 2
        assert ns[0].payload == "1"
        assert ns[1].payload == "2"
    finally:
        gather(worker)


@pytest.mark.slow
@pytest.mark.timing
def test_notifies_blocking(conn):

    def listener():
        for _ in conn.notifies(timeout=1):
            pass

    worker = spawn(listener)
    try:
        # Make sure the listener is listening
        if not conn.lock.locked():
            sleep(0.01)

        t0 = time()
        conn.execute("select 1")
        dt = time() - t0
    finally:
        gather(worker)

    assert dt > 0.5


@pytest.mark.slow
def test_generator_and_handler(conn, conn_cls, dsn):
    conn.set_autocommit(True)
    conn.execute("listen foo")

    n1 = None
    n2 = None

    def set_n2(n):
        nonlocal n2
        n2 = n

    conn.add_notify_handler(set_n2)

    def listener():
        nonlocal n1
        for n1 in conn.notifies(timeout=1, stop_after=1):
            pass

    worker = spawn(listener)
    try:
        # Make sure the listener is listening
        if not conn.lock.locked():
            sleep(0.01)

        with conn_cls.connect(dsn, autocommit=True) as nconn:
            nconn.execute("notify foo, '1'")
    finally:
        gather(worker)

    assert n1
    assert n2
