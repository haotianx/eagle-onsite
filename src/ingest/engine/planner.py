"""Turn a run request into a plan: which nodes, which docs, what is cached."""

from __future__ import annotations

import ast
import random
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ingest import gitinfo
from ingest.config import Config
from ingest.fingerprint import canonical_json, dag_fingerprints, env_hash, sha256_bytes
from ingest.spec import DAGS_DIR, DagError, DagSpec, dag_paths, parse_dag, validate_dag
from ingest.store.catalog import CacheEntry, Catalog, VersionRow

Selection: TypeAlias = Literal["node", "downstream", "upstream", "all"]
ScriptKind: TypeAlias = Literal["per_doc", "corpus"]

NODES_DIR = "nodes"
SELECTIONS: tuple[str, ...] = ("node", "downstream", "upstream", "all")


@dataclass(frozen=True)
class RunRequest:
    dag: str
    target: str | None = None
    """Node the selection is relative to; required unless selection == "all"."""
    selection: Selection = "all"
    snapshot_id: int | None = None
    """None → latest snapshot of the DAG's source."""
    input_versions: Mapping[str, int] = field(default_factory=dict)
    """Node name → version id to read instead of that node's current version. Applies to nodes
    outside the selection that a selected node reads."""
    recompute: Literal["reuse", "force"] = "reuse"
    docs: Literal["all"] | int | Sequence[str] = "all"
    """"all", a sample size, or explicit doc ids (any subset makes the run scratch)."""
    code: Literal["auto", "committed", "working"] = "auto"
    """auto → "working" if any selected node's files are dirty, else "committed"."""
    sample_seed: int = 0


@dataclass(frozen=True)
class InputRef:
    node: str
    version_id: int | None
    """None when the input is produced earlier in the same run."""
    fingerprint: str


@dataclass(frozen=True)
class NodePlan:
    node: str
    script: str
    fingerprint: str
    artifact_type: str | None
    inputs: Mapping[str, InputRef]
    docs_total: int
    docs_cached: int
    """Docs with a cache entry for (fingerprint, doc) — 0 when recompute == "force"."""
    docs_to_process: int
    docs_blocked: int
    """Docs that failed or are missing in an input version read from outside the run."""


@dataclass(frozen=True)
class RunPlan:
    request: RunRequest
    dag: DagSpec
    code: Literal["committed", "working"]
    commit: str | None
    dirty: bool
    """Whether the selected nodes' files differ from HEAD."""
    snapshot_id: int
    doc_ids: tuple[str, ...]
    scratch: bool
    scratch_reasons: tuple[str, ...]
    """e.g. "doc subset", "working tree"."""
    nodes: tuple[NodePlan, ...]
    """Selected nodes in topo order."""
    env: str


class PlanError(Exception):
    pass


def dirty_nodes(repo_root: Path, dag: DagSpec, names: Sequence[str] | None = None) -> set[str]:
    """Nodes (of `names`, default all) whose definition differs from HEAD: their script or
    checks file has uncommitted changes, or their entry in the DAG YAML (or the DAG's source)
    does. Editing one node's params leaves the other nodes clean."""
    names = list(dag.nodes if names is None else names)
    paths = gitinfo.dirty_paths(repo_root)
    out = {n for n in names if any(f in paths for f in dag.node_files(n) if f != dag.path)}
    if dag.path in paths:
        head = _head_dag(repo_root, dag.path)
        out |= {n for n in names if head is None or head.source != dag.source or head.nodes.get(n) != dag.nodes[n]}
    return out


def _head_dag(repo_root: Path, path: str) -> DagSpec | None:
    with tempfile.TemporaryDirectory() as tmp:
        if gitinfo.materialize_head(repo_root, [path], Path(tmp)):
            return None
        try:
            return parse_dag(Path(tmp) / path, Path(tmp))
        except DagError:
            return None


def selected_nodes(dag: DagSpec, selection: Selection, target: str | None) -> list[str]:
    if selection not in SELECTIONS:
        raise PlanError(f"unknown selection {selection!r} (expected {', '.join(SELECTIONS)})")
    if selection == "all":
        return dag.topo_order()
    if target is None:
        raise PlanError(f"selection {selection!r} needs a target node")
    if target not in dag.nodes:
        raise PlanError(f"{dag.name}: unknown node {target!r}")
    if selection == "node":
        return [target]
    wanted = {target, *(dag.downstream(target) if selection == "downstream" else dag.upstream(target))}
    return [n for n in dag.topo_order() if n in wanted]


def plan_run(catalog: Catalog, config: Config, request: RunRequest, *, code_root: Path | None = None) -> RunPlan:
    """Resolve DAG (from the working tree, or `code_root` holding a materialized HEAD tree for
    committed runs), snapshot, docs, input versions and fingerprints, and count cached docs.
    Raises PlanError for unknown DAG/node/version, a missing snapshot, an input with no current
    version and no override, or code == "committed" with no commits."""
    repo = config.repo_root
    commit = gitinfo.head_commit(repo)
    working_dag = _find_dag(repo, request.dag, config, required=False)
    if working_dag is not None:
        names = selected_nodes(working_dag, request.selection, request.target)
        dirty = bool(dirty_nodes(repo, working_dag, names))
    else:
        dirty = False

    code = request.code
    if code == "auto":
        code = "committed" if commit is not None and not dirty and working_dag is not None else "working"
    if code not in ("committed", "working"):
        raise PlanError(f"unknown code {request.code!r} (expected auto, committed or working)")
    if code == "committed":
        if commit is None:
            raise PlanError("committed run needs a git commit; commit first or run the working tree")
        if code_root is None:
            with head_tree(repo) as head:
                return _plan(catalog, config, request, code, commit, dirty, _committed_dag(repo, head, request.dag, config), head)
        return _plan(catalog, config, request, code, commit, dirty, _committed_dag(repo, code_root, request.dag, config), code_root)
    if working_dag is None:
        working_dag = _find_dag(repo, request.dag, config, required=True)
    return _plan(catalog, config, request, code, commit, dirty, working_dag, repo)


def _plan(
    catalog: Catalog,
    config: Config,
    request: RunRequest,
    code: Literal["committed", "working"],
    commit: str | None,
    dirty: bool,
    dag: DagSpec,
    root: Path,
) -> RunPlan:
    if request.recompute not in ("reuse", "force"):
        raise PlanError(f"unknown recompute {request.recompute!r} (expected reuse or force)")
    names = selected_nodes(dag, request.selection, request.target)
    if code == "committed":
        dirty = dirty or bool(dirty_nodes(config.repo_root, dag, names))

    snapshot = _snapshot(catalog, dag, request.snapshot_id)
    all_ids = [d.doc_id for d in catalog.snapshot_docs(snapshot)]
    doc_ids = _select_docs(all_ids, request)
    reasons: list[str] = []
    if len(doc_ids) < len(all_ids):
        reasons.append("doc subset")
    if code == "working" and (dirty or commit is None):
        reasons.append("working tree")

    selected = set(names)
    external = _external_inputs(catalog, dag, names, request.input_versions)
    for name, v in external.items():
        if v.scratch:
            reasons.append(f"scratch input {name}")
    env = env_hash(config.repo_root)
    fps = dag_fingerprints(dag, root, env, {n: v.node_fp for n, v in external.items()})
    kinds = {n: script_info(root / dag.nodes[n].script) for n in dag.nodes}
    keyed = input_keyed(dag, {n: k for n, (k, _) in kinds.items()})
    force = request.recompute == "force"

    # Per node: docs known to be skipped, and output shas known in advance (cached docs).
    blocked: dict[str, set[str]] = {}
    shas: dict[str, dict[str, str]] = {}
    for name, v in external.items():
        ok = {d.doc_id: d.object_sha for d in catalog.version_docs(v.id) if d.status == "ok" and d.object_sha}
        shas[name] = ok
        blocked[name] = set(doc_ids) - ok.keys()

    plans = []
    for name in names:
        node = dag.nodes[name]
        node_blocked = set().union(*(blocked[i] for i in node.inputs)) if node.inputs else set()
        unblocked = [d for d in doc_ids if d not in node_blocked]
        input_shas = {
            d: s for d in unblocked if (s := _shas_for(d, node.inputs, shas)) is not None
        }
        entries: dict[str, CacheEntry] = {}
        if not force:
            _, entries = cache_lookup(catalog, fps[name], kinds[name][0], name in keyed, unblocked, input_shas)
        blocked[name] = node_blocked
        shas[name] = {d: e.object_sha for d, e in entries.items()}
        plans.append(
            NodePlan(
                node=name,
                script=node.script,
                fingerprint=fps[name],
                artifact_type=kinds[name][1],
                inputs={
                    i: InputRef(i, None if i in selected else external[i].id, fps[i]) for i in node.inputs
                },
                docs_total=len(doc_ids),
                docs_cached=len(entries),
                docs_to_process=len(unblocked) - len(entries),
                docs_blocked=len(node_blocked),
            )
        )

    return RunPlan(
        request=request,
        dag=dag,
        code=code,
        commit=commit,
        dirty=dirty,
        snapshot_id=snapshot,
        doc_ids=tuple(doc_ids),
        scratch=bool(reasons),
        scratch_reasons=tuple(reasons),
        nodes=tuple(plans),
        env=env,
    )


def _find_dag(root: Path, name: str, config: Config, *, required: bool) -> DagSpec | None:
    """The DAG called `name` under `root`, validated."""
    for path in dag_paths(root):
        try:
            dag = parse_dag(path, root)
        except DagError:
            continue
        if dag.name != name:
            continue
        errors = validate_dag(dag, root, config.sources)
        if errors:
            raise PlanError("; ".join(errors))
        return dag
    if required:
        raise PlanError(f"unknown DAG {name!r}")
    return None


def _committed_dag(repo: Path, head: Path, name: str, config: Config) -> DagSpec:
    for path in dag_paths(head):
        try:
            dag = parse_dag(path, head)
        except DagError:
            continue
        if dag.name != name:
            continue
        materialize_dag_files(repo, head, dag)
        errors = validate_dag(dag, head, config.sources)
        if errors:
            raise PlanError("; ".join(f"HEAD: {e}" for e in errors))
        return dag
    raise PlanError(f"DAG {name!r} not found at HEAD; commit it or run the working tree")


def materialize_dag_files(repo_root: Path, head: Path, dag: DagSpec) -> None:
    """Add HEAD's version of any file the DAG references outside dags/ and nodes/."""
    missing = sorted({f for n in dag.nodes for f in dag.node_files(n) if not (head / f).exists()})
    if missing:
        gitinfo.materialize_head(repo_root, missing, head)


@contextmanager
def head_tree(repo_root: Path) -> Iterator[Path | None]:
    """A temporary directory holding HEAD's dags/ and nodes/ (None if there are no commits)."""
    if gitinfo.head_commit(repo_root) is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="ingest-head-") as tmp:
        root = Path(tmp)
        gitinfo.materialize_head(repo_root, [DAGS_DIR, NODES_DIR], root)
        yield root


def _snapshot(catalog: Catalog, dag: DagSpec, snapshot_id: int | None) -> int:
    if snapshot_id is None:
        latest = catalog.latest_snapshot(dag.source)
        if latest is None:
            raise PlanError(f"source {dag.source!r} has no snapshot; sync it first")
        return latest.id
    try:
        snapshot = catalog.get_snapshot(snapshot_id)
    except KeyError:
        raise PlanError(f"snapshot {snapshot_id} not found") from None
    if snapshot.source != dag.source:
        raise PlanError(f"snapshot {snapshot_id} is of source {snapshot.source!r}, not {dag.source!r}")
    return snapshot.id


def _select_docs(all_ids: list[str], request: RunRequest) -> list[str]:
    docs = request.docs
    if docs == "all":
        return all_ids
    if isinstance(docs, int) and not isinstance(docs, bool):
        if docs < 1:
            raise PlanError("sample size must be at least 1")
        if docs >= len(all_ids):
            return all_ids
        chosen = set(random.Random(request.sample_seed).sample(all_ids, docs))
        return [d for d in all_ids if d in chosen]
    if isinstance(docs, str):
        raise PlanError(f"docs must be 'all', a sample size or a list of doc ids, not {docs!r}")
    wanted = set(docs)
    unknown = sorted(wanted - set(all_ids))
    if unknown:
        raise PlanError(f"docs not in the snapshot: {', '.join(unknown)}")
    if not wanted:
        raise PlanError("no docs selected")
    return [d for d in all_ids if d in wanted]


def _external_inputs(
    catalog: Catalog, dag: DagSpec, names: list[str], overrides: Mapping[str, int]
) -> dict[str, VersionRow]:
    """Inputs of selected nodes produced outside the selection → the version to read."""
    selected = set(names)
    needed = [i for i in dict.fromkeys(i for n in names for i in dag.nodes[n].inputs) if i not in selected]
    stray = sorted(set(overrides) - set(needed))
    if stray:
        raise PlanError(f"input_versions for nodes the selection does not read from outside: {', '.join(stray)}")
    out: dict[str, VersionRow] = {}
    for name in needed:
        if name in overrides:
            try:
                v = catalog.get_version(overrides[name])
            except KeyError:
                raise PlanError(f"version {overrides[name]} not found") from None
            if (v.dag, v.node) != (dag.name, name):
                raise PlanError(f"version {v.id} is {v.dag}/{v.node}, not {dag.name}/{name}")
            if v.status != "complete":
                raise PlanError(f"version {v.id} of {dag.name}/{name} is {v.status}")
        else:
            current = catalog.current_version(dag.name, name)
            if current is None:
                raise PlanError(f"input {dag.name}/{name} has no current version; include it in the run or pick a version")
            v = current
        out[name] = v
    return out


def _shas_for(doc_id: str, inputs: Sequence[str], shas: Mapping[str, Mapping[str, str]]) -> dict[str, str] | None:
    out = {}
    for i in inputs:
        sha = shas[i].get(doc_id)
        if sha is None:
            return None
        out[i] = sha
    return out


def script_info(path: Path) -> tuple[ScriptKind, str | None]:
    """(per_doc | corpus, literal ARTIFACT_TYPE) read statically, so the parent never imports
    node scripts."""
    tree = ast.parse(path.read_bytes(), filename=str(path))
    funcs = {s.name for s in tree.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
    artifact_type = None
    for s in tree.body:
        if isinstance(s, ast.Assign):
            targets, value = s.targets, s.value
        elif isinstance(s, ast.AnnAssign) and s.value is not None:
            targets, value = [s.target], s.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "ARTIFACT_TYPE" for t in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                artifact_type = value.value
    return ("corpus" if "run_corpus" in funcs else "per_doc"), artifact_type


def input_keyed(dag: DagSpec, kinds: Mapping[str, ScriptKind]) -> set[str]:
    """Nodes whose per-doc outputs can change without their fingerprint changing: run_corpus
    nodes and everything downstream of one. Their cache keys also cover input object shas."""
    out: set[str] = set()
    for n in dag.topo_order():
        if kinds[n] == "corpus" or any(i in out for i in dag.nodes[n].inputs):
            out.add(n)
    return out


def _hash(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def doc_cache_key(fp: str, input_shas: Mapping[str, str]) -> str:
    return _hash({"node": fp, "inputs": sorted(input_shas.items())})


def corpus_cache_key(fp: str, input_shas: Mapping[str, Mapping[str, str]]) -> str:
    """Covers the doc set and every input object, so a different doc set recomputes."""
    return _hash({"node": fp, "docs": sorted((d, sorted(s.items())) for d, s in input_shas.items())})


def cache_lookup(
    catalog: Catalog,
    fp: str,
    kind: ScriptKind,
    keyed: bool,
    doc_ids: Sequence[str],
    input_shas: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, str], dict[str, CacheEntry]]:
    """Cache key per doc and the entries found. Keyed and corpus nodes need every doc's input
    shas (`input_shas[doc]`); docs without them are not looked up. A corpus node is cached
    only as a whole."""
    if kind == "corpus":
        if any(d not in input_shas for d in doc_ids):
            return {}, {}
        key = corpus_cache_key(fp, {d: input_shas[d] for d in doc_ids})
        entries = catalog.cache_get_many(key, doc_ids)
        return dict.fromkeys(doc_ids, key), entries if len(entries) == len(doc_ids) else {}
    if not keyed:
        return dict.fromkeys(doc_ids, fp), catalog.cache_get_many(fp, doc_ids)
    keys = {d: doc_cache_key(fp, input_shas[d]) for d in doc_ids if d in input_shas}
    return keys, {d: e for d, k in keys.items() if (e := catalog.cache_get(k, d)) is not None}


def request_to_dict(request: RunRequest) -> dict[str, Any]:
    docs = request.docs
    return {
        "dag": request.dag,
        "target": request.target,
        "selection": request.selection,
        "snapshot_id": request.snapshot_id,
        "input_versions": dict(request.input_versions),
        "recompute": request.recompute,
        "docs": docs if docs == "all" or isinstance(docs, int) else list(docs),
        "code": request.code,
        "sample_seed": request.sample_seed,
    }


def request_from_dict(data: Mapping[str, Any]) -> RunRequest:
    docs = data.get("docs", "all")
    return RunRequest(
        dag=data["dag"],
        target=data.get("target"),
        selection=data.get("selection", "all"),
        snapshot_id=data.get("snapshot_id"),
        input_versions={k: int(v) for k, v in (data.get("input_versions") or {}).items()},
        recompute=data.get("recompute", "reuse"),
        docs=docs if docs == "all" or isinstance(docs, int) else tuple(docs),
        code=data.get("code", "auto"),
        sample_seed=data.get("sample_seed", 0),
    )
