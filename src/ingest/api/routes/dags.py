"""DAGs, DAG detail with node states, a node's versions."""

from __future__ import annotations

from fastapi import APIRouter

from ingest.api.models import DagDetail, DagSummary, VersionSummary

router = APIRouter(tags=["dags"])


@router.get("/dags")
def list_dags() -> list[DagSummary]:
    raise NotImplementedError


@router.get("/dags/{dag}")
def get_dag(dag: str) -> DagDetail:
    raise NotImplementedError


@router.get("/dags/{dag}/nodes/{node}/versions")
def node_versions(dag: str, node: str) -> list[VersionSummary]:
    """Complete versions (incl. scratch), newest first."""
    raise NotImplementedError
