## 1. Problem

We need a lightweight solution to for integrating data from public and private sources into our platform. I want to put something minimal but extensible in place to solve for what we need today. I don’t want us to boil the ocean, but also don’t want to wander down some path that’s a dead end.

## 2. Goals

1. Engineers write a transform in Python with minimal boilerplate
2. We have a way to author Sources in Python
3. The DAG is derived from declared inputs and outputs of transforms and sources
4. Batch processing of the DAG
5. Some way to test code changes on production data

## 3. Non-goals for v1

- Incremental sources or transforms
- Streaming data
- Per-dataset permissions or row-level security
- Custom container images per transform

## 4. Users

| User | Needs |
| --- | --- |
| **Software engineer** | Author, test, and publish transforms |
| **On-call engineer** | Troubleshoot failing builds |

## 5. Core concepts

| Term | Meaning |
| --- | --- |
| **Source** | A step that brings external data in. Triggered only by a clock for now. |
| **Raw** | A source's landed data. |
| **Transform** | A step whose outputs depend only on its declared inputs and its code. |
| **Dataset** | A named output. Tabular (with a schema) or a set of files. |
| **Dataset version** | An immutable, all-or-nothing result of one build of a dataset. |
| **Build** | One run of a source or transform that produces new dataset versions. In v1 every transform build is a **snapshot**: it reads all of its inputs and replaces its outputs. |
| **Lineage** | For each dataset version, the exact input versions, code revision and runtime environment that produced it. |

## 6. Principles

The requirements below follow from these. A design that breaks one should be rejected even if it meets every numbered requirement.

1. **Reproducibility.** The same code with the same input can be rebuilt multiple times to produce the same result.
2. **All-or-nothing.** A dataset version is published completely or not at all.
3. **Datasets are the unit.** Dependencies, triggers, preview, checks and lineage are all defined in terms of datasets.
4. **Avoid lock-in.** The authoring contract, storage format and publishing contract must not lock us to a specific engine, scheduler or store.

## 7. Requirements

### 7.1 Sources

| ID | Requirement | Priority |
| --- | --- | --- |
| S1 | A source can be triggered by a clock (cron expression with time zone). | P0 |
| S2 | We can ingest data from an `smb://` or `https://` source | P0 |

### 7.2 Authoring transforms

| ID | Requirement | Priority |
| --- | --- | --- |
| A1 | Transforms are a single Python function with a declaration of its inputs. Assume a single output. | P0 |
| A2 | A dataset can be tabular data or a set of files (e.g., PDFs) | P0 |
| A3 | **Scratch runs for development:** run a transform against real (production) inputs and write to a temporary output that nothing downstream reads. This gives most of the value of branching before branching exists. | P0 |

### 7.3 Pipeline graph and triggers

| ID | Requirement | Priority |
| --- | --- | --- |
| G1 | The pipeline graph is worked out from the declared inputs and outputs. | P0 |
| G2 | CI rejects cycles, missing inputs and datasets with more than one writer before deploy. | P0 |
| G3 | Schedules dictate when datasets are rebuilt | P0 |
| G4 | Only one build of a dataset runs at a time. Triggers that arrive during a build are **merged into one** follow-up build. A burst of N changes produces at most one queued rebuild, not N. | P0 |
| G5 | When an upstream build fails, downstream datasets are **not** rebuilt. | P0 |
| G6 | A user can manually rebuild one dataset, one dataset plus everything upstream of it, or one dataset plus everything downstream of it. | P0 |
| G7 | Builds are safe to repeat. Rerunning a build with the same inputs and code produces an equivalent output. | P0 |

### 7.4 Datasets, versions and publishing

| ID | Requirement | Priority |
| --- | --- | --- |
| D1 | Every build produces a new immutable dataset version, published all-or-nothing. A failed build leaves the previous version in place. | P0 |
| D2 | Every dataset version records its lineage: the exact input versions, code revision, runtime image or environment, start and end time, and who or what triggered it. | P0 |

### 7.5 Observability and troubleshooting

| ID | Requirement | Priority |
| --- | --- | --- |
| O1 | A dashboard shows the pipeline graph with each dataset's state: fresh, building, failed or stale. The last build's duration and time are visible on each. | P1 |
| O2 | From a failed build you can reach its logs, error, inputs and lineage in two clicks or fewer. | P1 |
| O3 | **Preview** for any dataset version, including intermediate ones: schema, row count, basic column statistics and the first N rows. For file datasets: a file list with sizes, and file preview where practical. | P1 |
| O4 | Each dataset has a named owner. The system emits telemetry for each build (dataset, outcome, duration, completion time) sufficient to define freshness and failure alerts in SigNoz later, declared as Pulumi resources in `infra/signoz.ts`. The system itself sends no alerts in v1. | P1 |
| O5 | Traces, metrics and logs go to the platform's OpenTelemetry Collector. No vendor endpoints or credentials in pipeline code. Telemetry failures never fail a build. | P1 |
| O6 | Dataset and pipeline identifiers are telemetry attributes, never span names or metric labels. | P1 |
| O7 | Compute time and cost are visible per transform and per build. | P1 |

### 7.6 Runtime and dependencies

| ID | Requirement | Priority |
| --- | --- | --- |
| R1 | One shared runtime image for all transforms. We automatically publish updates from CI | P0 |

## 8. Non-functional requirements

- **Scale:** Transforms can support processing up to 10 TBs of data
- **Operability:** Adding a source or transform requires no infrastructure [or IAM] change
- **Portability:** The authoring contract, storage format and publishing contract survive a change of scheduler, engine or vendor.

## 9. Extensibility

Things to keep in mind as potential future enhancements

- Incremental processing
- Streaming
- Custom docker images
- Compute defined per transform
- Data health checks

## Appendix: Lessons from Foundry

From the current Foundry documentation. Kept as reference for the evaluation, not as a specification.

- **One storage model under everything.** Datasets are sequences of all-or-nothing transactions. Streams keep a fast buffer and are archived to ordinary datasets every few minutes, so batch tools and rebuilds read stream data without special handling.
- **Polling, not watching.** File-share ingestion (SMB included) is scheduled polling. Bringing in modified files commits an `UPDATE` transaction, which turns off incremental processing downstream. Edits are better recorded as new rows (key, ordering column, deletion flag) and resolved to the latest state.
- **Incremental with fallback to full.** One function serves both modes, with automatic fallback to a full rebuild. `semantic_version` forces reprocessing; a plain code change makes the output stale but runs incrementally, so history keeps the old logic. **Snapshot-only v1 avoids this trap entirely.**
- **Incremental slows down over time.** Many small appends degrade reads until you run periodic full rebuilds or maintain compacted copies.
- **Streaming is for low latency only.** Foundry's own guidance: workloads that tolerate more than about ten minutes of latency suit batch or incremental. Streaming transforms don't support Python.
- **Scheduling.** Builds lock their outputs, triggers that arrive during a build are merged, and staleness depends on inputs plus logic.
- **Lightweight engines are there now.** Polars, DuckDB and pandas transforms are documented alongside Spark.
- **Inbound webhooks.** Listeners write into a stream; the limits are about 100 requests per second and 1 MB per event. Recommended only when the sender can't be customized.
