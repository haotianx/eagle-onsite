import type { VersionDetail, VersionSummary } from "@/api/types";

/** Test payloads shaped like the dev backend's. */
export function versionSummary(id: number, extra: Partial<VersionSummary> = {}): VersionSummary {
  const seq = extra.seq === undefined ? id : extra.seq;
  return {
    id,
    label: `default/chunk@v${seq}`,
    dag: "default",
    node: "chunk",
    seq,
    status: "complete",
    scratch: false,
    current: false,
    commit: "8b367a7fc2a0f72dd095b722eb13fe99575da94a",
    dirty: false,
    node_fp: "802f704e8cc4300df7e2bbb7a48ed19dd2b6695fb788fc39fa52355aa43584c6",
    snapshot_id: 2,
    run_id: 3,
    artifact_type: "chunk",
    created_at: "2026-10-18T18:33:38.200000+00:00",
    finalized_at: "2026-10-18T18:33:39.031698+00:00",
    docs: 6,
    ok: 6,
    failed: 0,
    skipped: 0,
    ...extra,
  };
}

export function versionDetail(id: number, extra: Partial<VersionDetail> = {}): VersionDetail {
  return {
    ...versionSummary(id),
    inputs: [
      {
        node: "ocr",
        version: versionSummary(1, { label: "default/ocr@v1", node: "ocr", artifact_type: "parse", current: true }),
        current: true,
      },
    ],
    meta: {
      script: "nodes/hybrid_chunk.py",
      script_hash: "45ce7f50a784c9ed58daadb7cd1884b9aa8354251df83b1b7effe5ab8928d1de",
      params: { max_tokens: 256, tokenizer: "Qwen/Qwen3-Embedding-0.6B" },
      env: "197dc7132d0bc42115dd06f87595019cbe75b8dcf2ab8e0f935498b32807e42f",
      describe: { chunker: "docling.HybridChunker", max_tokens: 256 },
      checks_fp: null,
    },
    stats: { docs: 6, ok: 6, error: 0, skipped: 0, size_bytes: 161358, wall_s: 0.73 },
    summary: {
      docs: 6,
      ok: 6,
      failed: 0,
      skipped: 0,
      size_bytes: 161358,
      records: 148,
      chunks: 148,
      tokens_per_chunk: 171.72297297297297,
    },
    issues: { error: 0, warning: 2 },
    promotable: true,
    not_promotable_reasons: [],
    replaces: versionSummary(2, { current: true }),
    noncurrent_inputs: [],
    checks_changed: false,
    ...extra,
  };
}
