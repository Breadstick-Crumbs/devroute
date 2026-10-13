"""Evaluation and Learning Engine: local records, summaries, and routing evaluation.

Names are imported on first use, so hooks that only need the record store stay fast.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "OUTCOME_STATUSES": "report",
    "Insight": "report",
    "ProfileSummary": "report",
    "Report": "report",
    "summarize": "report",
    "RECORD_KINDS": "store",
    "RecordError": "store",
    "RecordStore": "store",
    "new_record_id": "store",
    "CaseResult": "suite",
    "SuiteResult": "suite",
    "load_suite": "suite",
    "run_suite": "suite",
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
