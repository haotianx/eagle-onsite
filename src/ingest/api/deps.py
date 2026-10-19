"""Request dependencies: the app's config, catalog, object store and search cache, plus lookups
that turn missing rows into HTTP errors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ingest.config import Config
from ingest.search import IndexCache
from ingest.store.catalog import Catalog, RunRow, SnapshotRow, VersionRow
from ingest.store.objects import ObjectStore


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_catalog(request: Request) -> Catalog:
    return request.app.state.catalog


def get_objects(request: Request) -> ObjectStore:
    return request.app.state.objects


def get_index_cache(request: Request) -> IndexCache:
    return request.app.state.index_cache


ConfigDep = Annotated[Config, Depends(get_config)]
CatalogDep = Annotated[Catalog, Depends(get_catalog)]
ObjectsDep = Annotated[ObjectStore, Depends(get_objects)]
IndexCacheDep = Annotated[IndexCache, Depends(get_index_cache)]


def not_found(message: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, message)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


@contextmanager
def catalog_404() -> Iterator[None]:
    """Turn a catalog get_* KeyError into a 404."""
    try:
        yield
    except KeyError as e:
        raise not_found(str(e.args[0]) if e.args else "Not found") from None


def version_or_404(catalog: Catalog, version_id: int) -> VersionRow:
    with catalog_404():
        return catalog.get_version(version_id)


def run_or_404(catalog: Catalog, run_id: int) -> RunRow:
    with catalog_404():
        return catalog.get_run(run_id)


def snapshot_or_404(catalog: Catalog, snapshot_id: int) -> SnapshotRow:
    with catalog_404():
        return catalog.get_snapshot(snapshot_id)
