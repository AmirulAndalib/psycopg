"""
Constants to refer to OIDS in C
"""

# Copyright (C) 2020 The Psycopg Team

# Use tools/update_oids.py to update this data.

cdef enum:
    INVALID_OID = 0

    # autogenerated: start

    # Generated from PostgreSQL 17.0

    ACLITEM_OID = 1033
    BIT_OID = 1560
    BOOL_OID = 16
    BOX_OID = 603
    BPCHAR_OID = 1042
    BYTEA_OID = 17
    CHAR_OID = 18
    CID_OID = 29
    CIDR_OID = 650
    CIRCLE_OID = 718
    DATE_OID = 1082
    DATEMULTIRANGE_OID = 4535
    DATERANGE_OID = 3912
    FLOAT4_OID = 700
    FLOAT8_OID = 701
    GTSVECTOR_OID = 3642
    INET_OID = 869
    INT2_OID = 21
    INT2VECTOR_OID = 22
    INT4_OID = 23
    INT4MULTIRANGE_OID = 4451
    INT4RANGE_OID = 3904
    INT8_OID = 20
    INT8MULTIRANGE_OID = 4536
    INT8RANGE_OID = 3926
    INTERVAL_OID = 1186
    JSON_OID = 114
    JSONB_OID = 3802
    JSONPATH_OID = 4072
    LINE_OID = 628
    LSEG_OID = 601
    MACADDR_OID = 829
    MACADDR8_OID = 774
    MONEY_OID = 790
    NAME_OID = 19
    NUMERIC_OID = 1700
    NUMMULTIRANGE_OID = 4532
    NUMRANGE_OID = 3906
    OID_OID = 26
    OIDVECTOR_OID = 30
    PATH_OID = 602
    PG_LSN_OID = 3220
    POINT_OID = 600
    POLYGON_OID = 604
    RECORD_OID = 2249
    REFCURSOR_OID = 1790
    REGCLASS_OID = 2205
    REGCOLLATION_OID = 4191
    REGCONFIG_OID = 3734
    REGDICTIONARY_OID = 3769
    REGNAMESPACE_OID = 4089
    REGOPER_OID = 2203
    REGOPERATOR_OID = 2204
    REGPROC_OID = 24
    REGPROCEDURE_OID = 2202
    REGROLE_OID = 4096
    REGTYPE_OID = 2206
    TEXT_OID = 25
    TID_OID = 27
    TIME_OID = 1083
    TIMESTAMP_OID = 1114
    TIMESTAMPTZ_OID = 1184
    TIMETZ_OID = 1266
    TSMULTIRANGE_OID = 4533
    TSQUERY_OID = 3615
    TSRANGE_OID = 3908
    TSTZMULTIRANGE_OID = 4534
    TSTZRANGE_OID = 3910
    TSVECTOR_OID = 3614
    TXID_SNAPSHOT_OID = 2970
    UUID_OID = 2950
    VARBIT_OID = 1562
    VARCHAR_OID = 1043
    XID_OID = 28
    XID8_OID = 5069
    XML_OID = 142

    # autogenerated: end
