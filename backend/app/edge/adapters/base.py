"""Shared adapter interface.

Each adapter translates hardware- or vendor-specific payloads into Vendarix
event manifests (see app/edge/contracts.py). The core never inspects
proprietary device payloads — it only sees manifests.

Adapters are total functions: any payload they do not recognise becomes an
`edge.unrecognized` event with the raw payload preserved under
`payload.raw_event` and a hash; nothing is dropped silently and nothing is
guessed.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from app.edge.contracts import build_manifest, new_event_id


class AdapterContext:
    def __init__(
        self,
        *,
        tenant_id: str,
        store_id: str,
        source_system: str,
        device_id: str,
        source_seq: int = 1,
        occurred_at: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ):
        self.tenant_id = tenant_id
        self.store_id = store_id
        self.source_system = source_system
        self.device_id = device_id
        self.source_seq = int(source_seq)
        self.occurred_at = occurred_at
        self.correlation_id = correlation_id
        self.causation_id = causation_id

    def manifest(self, event_type: str, payload: dict, *, event_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict:
        return build_manifest(
            event_type=event_type,
            tenant_id=self.tenant_id,
            store_id=self.store_id,
            source_system=self.source_system,
            device_id=self.device_id,
            payload=payload,
            source_seq=self.source_seq,
            occurred_at=self.occurred_at or datetime.now(timezone.utc),
            event_id=event_id or new_event_id(),
            idempotency_key=idempotency_key,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
        )


class VendarixAdapter(ABC):
    vendor: str = ""
    interface: str = ""

    @abstractmethod
    def translate(self, raw: dict[str, Any], ctx: AdapterContext) -> list[dict]:
        """Translate one vendor payload into zero or more manifests."""

    def unrecognized(self, raw: dict[str, Any], ctx: AdapterContext, reason: str) -> list[dict]:
        payload = {
            "raw_event": raw,
            "vendor": self.vendor,
            "interface": self.interface,
            "reason": reason,
        }
        return [ctx.manifest("edge.unrecognized", payload)]
