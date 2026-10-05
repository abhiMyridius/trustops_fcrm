"""Change detection, state persistence, and activity logging."""

from evoq_context.state.activity import append_build_activity, append_raw_activity
from evoq_context.state.reconciler import (
    compute_build_version_key,
    compute_current_state,
    compute_state_version_key,
    load_previous_state,
    reconcile,
    save_state,
    write_current_pointer,
)

__all__ = [
    "append_build_activity",
    "append_raw_activity",
    "compute_build_version_key",
    "compute_current_state",
    "compute_state_version_key",
    "load_previous_state",
    "reconcile",
    "save_state",
    "write_current_pointer",
]
