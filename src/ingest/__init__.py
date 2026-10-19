"""Document ingestion pipeline."""

__version__ = "0.1.0"


def __getattr__(name: str):
    if name == "load_table":
        from ingest.publish import load_table

        return load_table
    raise AttributeError(name)
