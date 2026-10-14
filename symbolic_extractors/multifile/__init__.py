"""Multifile SGR: local extraction → symbol index → cross-file resolution."""

from symbolic_extractors.multifile.pipeline import (
    extract_local_sgr,
    process_project,
    process_project_from_sources,
)
from symbolic_extractors.multifile.compress import compress_for_gnn
from symbolic_extractors.multifile.resolver import resolve_project_graph
from symbolic_extractors.multifile.symbol_index import build_symbol_index

__all__ = [
    "extract_local_sgr",
    "process_project",
    "process_project_from_sources",
    "build_symbol_index",
    "resolve_project_graph",
    "compress_for_gnn",
    "process_subgraph",
    "process_dpd_project",
    "process_dpd_project_subgraphs",
    "process_dpd_corpus",
]


def __getattr__(name: str):
    # Lazy-load DPD helpers so ``python -m ...subgraph_dataset`` stays clean.
    if name in {
        "process_subgraph",
        "process_dpd_project",
        "process_dpd_project_subgraphs",
        "process_dpd_corpus",
    }:
        from symbolic_extractors.multifile import subgraph_dataset as _sd
        return getattr(_sd, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
