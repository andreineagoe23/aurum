"""Deterministic, atomic Parquet files.

* **Deterministic:** the same rows and schema always give byte-identical files (fixed writer
  options, no timestamps or random ids in metadata). So re-running ingestion or aggregation can
  be checked by file hash. A different pyarrow version may change the bytes; the hash manifest
  records the version.
* **Atomic:** data goes to a temporary file in the same directory first. A crash never leaves
  a half-written file under the final name.
* **Immutable when asked:** ``overwrite=False`` refuses to replace an existing file (raw data).
"""

import contextlib
import hashlib
import os
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from aurum.data.schemas import polars_schema

PARQUET_OPTIONS = {
    "compression": "zstd",
    "compression_level": 9,
    "use_dictionary": True,
    "write_statistics": True,
    "version": "2.6",
    "row_group_size": 1 << 20,
    "data_page_version": "1.0",
}
TMP_SUFFIX = ".tmp"


def to_table(df: pl.DataFrame, schema: pa.Schema) -> pa.Table:
    """Select ``schema``'s columns in order and cast to its exact types (raises if missing)."""
    table = df.select(schema.names).to_arrow()
    return table.cast(schema)


def write_parquet(df: pl.DataFrame, path: Path, schema: pa.Schema, *, overwrite: bool) -> Path:
    """Write ``df`` to ``path`` atomically. ``overwrite=False`` raises ``FileExistsError`` if the
    file exists (checked atomically: the final step is a hard link, which fails if taken)."""
    table = to_table(df, schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}{TMP_SUFFIX}")
    try:
        pq.write_table(table, tmp, **PARQUET_OPTIONS)
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # FileExistsError if someone else got there first
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return path


def read_parquet(path: Path, schema: pa.Schema | None = None) -> pl.DataFrame:
    df = pl.read_parquet(path)
    if schema is not None and df.columns != schema.names:
        raise ValueError(f"{path} has columns {df.columns}, expected {schema.names}")
    return df


def empty_frame(schema: pa.Schema) -> pl.DataFrame:
    return pl.DataFrame(schema=polars_schema(schema))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def remove_stale_temps(directory: Path) -> int:
    """Delete leftover ``*.tmp`` files under ``directory`` (from a crash). Returns the count."""
    if not directory.exists():
        return 0
    count = 0
    for tmp in directory.rglob(f"*{TMP_SUFFIX}"):
        tmp.unlink(missing_ok=True)
        count += 1
    return count
