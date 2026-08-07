from __future__ import annotations

from functools import lru_cache

from src.composition.receipt_dependencies import (
    ReceiptProcessingRuntime,
    build_receipt_processing_runtime,
)


@lru_cache
def get_cached_processing_runtime() -> ReceiptProcessingRuntime:
    return build_receipt_processing_runtime()


def handler(event, context):
    runtime = get_cached_processing_runtime()
    return runtime.handler.handle(event, context)
