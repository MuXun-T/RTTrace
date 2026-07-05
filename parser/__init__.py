"""Trace decoding, rebuild, and query pipeline."""

from .align import align_events
from .codec import TraceDecodeSession, decode_trace, encode_trace
from .index import idx_Build, query_events, query_timeline_buckets
from .pipeline import (
    ParserSession,
    load_dataset,
    load_dataset_with_timings,
    load_dataset_from_chunks,
    prs_Align,
    prs_FeedChunk,
    prs_Finalize,
    prs_Init,
    prs_Load,
    prs_LoadChunks,
    prs_Prescan,
    prs_Verify,
)
from .rebuild import rb_Rebuild
from .result import Result

__all__ = [
    "Result",
    "ParserSession",
    "TraceDecodeSession",
    "align_events",
    "decode_trace",
    "encode_trace",
    "idx_Build",
    "load_dataset",
    "load_dataset_with_timings",
    "load_dataset_from_chunks",
    "prs_FeedChunk",
    "prs_Finalize",
    "prs_Init",
    "prs_Align",
    "prs_Load",
    "prs_LoadChunks",
    "prs_Prescan",
    "prs_Verify",
    "query_events",
    "query_timeline_buckets",
    "rb_Rebuild",
]
