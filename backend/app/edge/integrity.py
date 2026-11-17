"""Storage isolation invariants.

Two enforceable rules, implemented close to the ORM so they cannot be bypassed
by route code:

1. Append-only tables (event store, audit ledger) are INSERT-only. Any ORM
   UPDATE or DELETE against them raises AppendOnlyViolation. The single
   mutable exception by design is `edge_outbox` (delivery cursor) — it is the
   synchronization control table, not the audit trail.

2. Operational projections carry `version` columns and are written through
   compare-and-swap (WHERE id = ? AND version = ?), giving optimistic
   concurrency on the mutable rows.
"""

from sqlalchemy import event

from app.planes.models import (
    InventoryLedgerEntry,
    WasteRecord,
    VendorMessage,
)
from app.edge.models import ContinuityRecord, EdgeEvent


class AppendOnlyViolation(RuntimeError):
    """Raised when code attempts to UPDATE or DELETE an append-only row."""


_APPEND_ONLY = (EdgeEvent, InventoryLedgerEntry, ContinuityRecord, WasteRecord, VendorMessage)


def _before_update(mapper, connection, target):
    raise AppendOnlyViolation(
        f"{target.__class__.__name__} is an append-only table; update in place is forbidden. "
        "Record a correcting event instead."
    )


def _before_delete(mapper, connection, target):
    raise AppendOnlyViolation(
        f"{target.__class__.__name__} is an append-only table; delete is forbidden."
    )


def register() -> None:
    for cls in _APPEND_ONLY:
        event.listen(cls, "before_update", _before_update)
        event.listen(cls, "before_delete", _before_delete)


__all__ = ["AppendOnlyViolation", "register"]
