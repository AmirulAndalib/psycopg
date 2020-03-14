from select import select

import pytest

from psycopg3.pq_enums import ConnStatus, PollingStatus, Ping


def test_connectdb(pq, dsn):
    conn = pq.PGconn.connect(dsn)
    assert conn.status == ConnStatus.CONNECTION_OK, conn.error_message


def test_connectdb_bytes(pq, dsn):
    conn = pq.PGconn.connect(dsn.encode("utf8"))
    assert conn.status == ConnStatus.CONNECTION_OK, conn.error_message


def test_connectdb_error(pq):
    conn = pq.PGconn.connect("dbname=psycopg3_test_not_for_real")
    assert conn.status == ConnStatus.CONNECTION_BAD


@pytest.mark.parametrize("baddsn", [None, 42])
def test_connectdb_badtype(pq, baddsn):
    with pytest.raises(TypeError):
        pq.PGconn.connect(baddsn)


def test_connect_async(pq, dsn):
    conn = pq.PGconn.connect_start(dsn)
    while 1:
        assert conn.status != ConnStatus.CONNECTION_BAD
        rv = conn.connect_poll()
        if rv == PollingStatus.PGRES_POLLING_OK:
            break
        elif rv == PollingStatus.PGRES_POLLING_READING:
            select([conn.socket], [], [])
        elif rv == PollingStatus.PGRES_POLLING_WRITING:
            select([], [conn.socket], [])
        else:
            assert False, rv

    assert conn.status == ConnStatus.CONNECTION_OK


def test_connect_async_bad(pq, dsn):
    conn = pq.PGconn.connect_start("dbname=psycopg3_test_not_for_real")
    while 1:
        assert conn.status != ConnStatus.CONNECTION_BAD
        rv = conn.connect_poll()
        if rv == PollingStatus.PGRES_POLLING_FAILED:
            break
        elif rv == PollingStatus.PGRES_POLLING_READING:
            select([conn.socket], [], [])
        elif rv == PollingStatus.PGRES_POLLING_WRITING:
            select([], [conn.socket], [])
        else:
            assert False, rv

    assert conn.status == ConnStatus.CONNECTION_BAD


def test_defaults(pq, tempenv):
    tempenv["PGPORT"] = "15432"
    defs = pq.PGconn.get_defaults()
    assert len(defs) > 20
    port = [d for d in defs if d.keyword == "port"][0]
    assert port.envvar == "PGPORT"
    assert port.compiled == "5432"
    assert port.val == "15432"
    assert port.label == "Database-Port"
    assert port.dispatcher == ""
    assert port.dispsize == 6


def test_info(dsn, pgconn):
    info = pgconn.info
    assert len(info) > 20
    dbname = [d for d in info if d.keyword == "dbname"][0]
    assert dbname.envvar == "PGDATABASE"
    assert dbname.label == "Database-Name"
    assert dbname.dispatcher == ""
    assert dbname.dispsize == 20

    parsed = pgconn.parse_conninfo(dsn)
    name = [o.val for o in parsed if o.keyword == "dbname"][0]
    assert dbname.val == name


def test_conninfo_parse(pq):
    info = pq.PGconn.parse_conninfo(
        "postgresql://host1:123,host2:456/somedb"
        "?target_session_attrs=any&application_name=myapp"
    )
    info = {i.keyword: i.val for i in info if i.val is not None}
    assert info["host"] == "host1,host2"
    assert info["port"] == "123,456"
    assert info["dbname"] == "somedb"
    assert info["application_name"] == "myapp"


def test_conninfo_parse_bad(pq):
    with pytest.raises(pq.PQerror) as e:
        pq.PGconn.parse_conninfo("bad_conninfo=")
        assert "bad_conninfo" in str(e.value)


def test_reset(pgconn):
    assert pgconn.status == ConnStatus.CONNECTION_OK
    # TODO: break it
    pgconn.reset()
    assert pgconn.status == ConnStatus.CONNECTION_OK


def test_reset_async(pgconn):
    assert pgconn.status == ConnStatus.CONNECTION_OK
    # TODO: break it
    pgconn.reset_start()
    while 1:
        rv = pgconn.connect_poll()
        if rv == PollingStatus.PGRES_POLLING_READING:
            select([pgconn.socket], [], [])
        elif rv == PollingStatus.PGRES_POLLING_WRITING:
            select([], [pgconn.socket], [])
        else:
            break

    assert rv == PollingStatus.PGRES_POLLING_OK
    assert pgconn.status == ConnStatus.CONNECTION_OK


def test_ping(pq, dsn):
    rv = pq.PGconn.ping(dsn)
    assert rv == Ping.PQPING_OK

    rv = pq.PGconn.ping("port=99999")
    assert rv == Ping.PQPING_NO_RESPONSE


def test_db(pgconn):
    name = [o.val for o in pgconn.info if o.keyword == "dbname"][0]
    assert pgconn.db == name


def test_user(pgconn):
    user = [o.val for o in pgconn.info if o.keyword == "user"][0]
    assert pgconn.user == user


def test_password(pgconn):
    # not in info
    assert isinstance(pgconn.password, str)


def test_host(pgconn):
    # might be not in info
    assert isinstance(pgconn.host, str)


def test_hostaddr(pgconn):
    # not in info
    assert isinstance(pgconn.hostaddr, str)


def test_tty(pgconn):
    tty = [o.val for o in pgconn.info if o.keyword == "tty"][0]
    assert pgconn.tty == tty


def test_transaction_status(pq, pgconn):
    assert pgconn.transaction_status == pq.TransactionStatus.PQTRANS_IDLE
    # TODO: test other states
    pgconn.finish()
    assert pgconn.transaction_status == pq.TransactionStatus.PQTRANS_UNKNOWN


def test_parameter_status(pq, dsn, tempenv):
    tempenv["PGAPPNAME"] = "psycopg3 tests"
    pgconn = pq.PGconn.connect(dsn)
    assert pgconn.parameter_status('application_name') == "psycopg3 tests"
    assert pgconn.parameter_status('wat') is None
