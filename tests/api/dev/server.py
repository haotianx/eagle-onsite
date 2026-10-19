"""Dev server: seed a data dir when empty, then serve the app on it.

    uv run python tests/api/dev/server.py [--data-dir .ingest/dev] [--seed real|synthetic|none]
                                          [--port 8765] [--host 127.0.0.1]

The config is built here (ingest.toml is not read): repo_root is this worktree and the
`proposals` source is tests/fixtures/proposals. Runs started from the UI execute in the engine's
own subprocess, which reads the repo's ingest.toml sources.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed  # noqa: E402

from ingest.api.app import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from ingest.config import Config, SourceConfig  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def dev_config(data_dir: Path) -> Config:
    return Config(
        repo_root=REPO,
        data_dir=(REPO / data_dir).resolve(),
        sources=MappingProxyType({"proposals": SourceConfig("proposals", root=seed.FIXTURES)}),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path(".ingest/dev"))
    parser.add_argument("--seed", choices=("real", "synthetic", "none"), default="real")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("dev")

    config = dev_config(args.data_dir)
    if args.seed != "none" and not config.catalog_path.exists():
        log.info("seeding %s (%s)", config.data_dir, args.seed)
        log.info("seeded: %s", seed.seed(config, real=args.seed == "real"))

    import uvicorn

    from ingest.api.app import create_app

    uvicorn.run(create_app(config), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
