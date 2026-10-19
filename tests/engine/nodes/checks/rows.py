from __future__ import annotations

from ingest.contract import Issue


def validate(record, params):
    issues = [Issue("warning", "toy_warning", "every doc gets one")]
    if record.num_rows and record.column("size")[0].as_py() > params.get("max_size", 1 << 60):
        issues.append(Issue("error", "too_big", "size over max_size"))
    return issues
