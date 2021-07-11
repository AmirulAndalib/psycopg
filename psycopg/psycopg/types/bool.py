"""
Adapters for booleans.
"""

# Copyright (C) 2020-2021 The Psycopg Team

from .. import postgres
from ..pq import Format
from ..adapt import Buffer, Dumper, Loader
from ..proto import AdaptContext


class BoolDumper(Dumper):

    format = Format.TEXT
    _oid = postgres.types["bool"].oid

    def dump(self, obj: bool) -> bytes:
        return b"t" if obj else b"f"

    def quote(self, obj: bool) -> bytes:
        return b"true" if obj else b"false"


class BoolBinaryDumper(Dumper):

    format = Format.BINARY
    _oid = postgres.types["bool"].oid

    def dump(self, obj: bool) -> bytes:
        return b"\x01" if obj else b"\x00"


class BoolLoader(Loader):

    format = Format.TEXT

    def load(self, data: Buffer) -> bool:
        return data == b"t"


class BoolBinaryLoader(Loader):

    format = Format.BINARY

    def load(self, data: Buffer) -> bool:
        return data != b"\x00"


def register_default_adapters(context: AdaptContext) -> None:
    adapters = context.adapters
    adapters.register_dumper(bool, BoolDumper)
    adapters.register_dumper(bool, BoolBinaryDumper)
    adapters.register_loader("bool", BoolLoader)
    adapters.register_loader("bool", BoolBinaryLoader)
