"""
Mapping from types/oids to Dumpers/Loaders
"""

# Copyright (C) 2020-2021 The Psycopg Team

from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from typing import cast, TYPE_CHECKING

from . import pq
from . import errors as e
from ._enums import PyFormat as PyFormat
from .proto import AdaptContext, Dumper, Loader
from ._cmodule import _psycopg
from ._typeinfo import TypesRegistry

if TYPE_CHECKING:
    from .connection import BaseConnection

RV = TypeVar("RV")


class AdaptersMap(AdaptContext):
    """
    Map oids to Loaders and types to Dumpers.

    The object can start empty or copy from another object of the same class.
    Copies are copy-on-write: if the maps are updated make a copy. This way
    extending e.g. global map by a connection or a connection map from a cursor
    is cheap: a copy is made only on customisation.
    """

    __module__ = "psycopg.adapt"

    _dumpers: Dict[PyFormat, Dict[Union[type, str], Type[Dumper]]]
    _loaders: List[Dict[int, Type[Loader]]]
    types: TypesRegistry

    # Record if a dumper or loader has an optimised version.
    _optimised: Dict[type, type] = {}

    def __init__(
        self,
        template: Optional["AdaptersMap"] = None,
        types: Optional[TypesRegistry] = None,
    ):
        if template:
            self._dumpers = template._dumpers.copy()
            self._own_dumpers = _dumpers_shared.copy()
            template._own_dumpers = _dumpers_shared.copy()
            self._loaders = template._loaders[:]
            self._own_loaders = [False, False]
            template._own_loaders = [False, False]
            self.types = TypesRegistry(template.types)
        else:
            self._dumpers = {fmt: {} for fmt in PyFormat}
            self._own_dumpers = _dumpers_owned.copy()
            self._loaders = [{}, {}]
            self._own_loaders = [True, True]
            self.types = types or TypesRegistry()

    # implement the AdaptContext protocol too
    @property
    def adapters(self) -> "AdaptersMap":
        return self

    @property
    def connection(self) -> Optional["BaseConnection[Any]"]:
        return None

    def register_dumper(
        self, cls: Union[type, str], dumper: Type[Dumper]
    ) -> None:
        """
        Configure the context to use *dumper* to convert object of type *cls*.
        """
        if not isinstance(cls, (str, type)):
            raise TypeError(
                f"dumpers should be registered on classes, got {cls} instead"
            )

        if _psycopg:
            dumper = self._get_optimised(dumper)

        # Register the dumper both as its format and as auto
        # so that the last dumper registered is used in auto (%s) format
        for fmt in (PyFormat.from_pq(dumper.format), PyFormat.AUTO):
            if not self._own_dumpers[fmt]:
                self._dumpers[fmt] = self._dumpers[fmt].copy()
                self._own_dumpers[fmt] = True

            self._dumpers[fmt][cls] = dumper

    def register_loader(
        self, oid: Union[int, str], loader: Type["Loader"]
    ) -> None:
        """
        Configure the context to use *loader* to convert data of oid *oid*.
        """
        if isinstance(oid, str):
            oid = self.types[oid].oid
        if not isinstance(oid, int):
            raise TypeError(
                f"loaders should be registered on oid, got {oid} instead"
            )

        if _psycopg:
            loader = self._get_optimised(loader)

        fmt = loader.format
        if not self._own_loaders[fmt]:
            self._loaders[fmt] = self._loaders[fmt].copy()
            self._own_loaders[fmt] = True

        self._loaders[fmt][oid] = loader

    def get_dumper(self, cls: type, format: PyFormat) -> Type["Dumper"]:
        """
        Return the dumper class for the given type and format.

        Raise ProgrammingError if a class is not available.
        """
        try:
            dmap = self._dumpers[format]
        except KeyError:
            raise ValueError(f"bad dumper format: {format}")

        # Look for the right class, including looking at superclasses
        for scls in cls.__mro__:
            if scls in dmap:
                return dmap[scls]

            # If the adapter is not found, look for its name as a string
            fqn = scls.__module__ + "." + scls.__qualname__
            if fqn in dmap:
                # Replace the class name with the class itself
                d = dmap[scls] = dmap.pop(fqn)
                return d

        raise e.ProgrammingError(
            f"cannot adapt type {cls.__name__}"
            f" to format {PyFormat(format).name}"
        )

    def get_loader(
        self, oid: int, format: pq.Format
    ) -> Optional[Type["Loader"]]:
        """
        Return the loader class for the given oid and format.

        Return None if not found.
        """
        return self._loaders[format].get(oid)

    @classmethod
    def _get_optimised(self, cls: Type[RV]) -> Type[RV]:
        """Return the optimised version of a Dumper or Loader class.

        Return the input class itself if there is no optimised version.
        """
        try:
            return self._optimised[cls]
        except KeyError:
            pass

        # Check if the class comes from psycopg.types and there is a class
        # with the same name in psycopg_c._psycopg.
        from psycopg import types

        if cls.__module__.startswith(types.__name__):
            new = cast(Type[RV], getattr(_psycopg, cls.__name__, None))
            if new:
                self._optimised[cls] = new
                return new

        self._optimised[cls] = cls
        return cls


# Micro-optimization: copying these objects is faster than creating new dicts
_dumpers_owned = dict.fromkeys(PyFormat, True)
_dumpers_shared = dict.fromkeys(PyFormat, False)
