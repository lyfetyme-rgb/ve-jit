"""Adapter registry. The edge core resolves an adapter by (vendor, interface)
and falls back to the enterprise normalizer or to `edge.unrecognized`."""

from app.edge.adapters.base import AdapterContext, VendarixAdapter
from app.edge.adapters.ncr_voyix import NCRVoyixAdapter
from app.edge.adapters.toshiba_tcgs import ToshibaTCGSAdapter
from app.edge.adapters.diebold_nixdorf import DieboldNixdorfAdapter
from app.edge.adapters.scanner_scale import ScannerScaleAdapter
from app.edge.adapters.enterprise import EnterpriseAdapter

_ADAPTERS: dict[str, VendarixAdapter] = {
    "ncr-voyix": NCRVoyixAdapter(),
    "toshiba-tcgs": ToshibaTCGSAdapter(),
    "diebold-nixdorf": DieboldNixdorfAdapter(),
    "scanner-scale": ScannerScaleAdapter(),
    "enterprise": EnterpriseAdapter(),
}


def adapter_for(vendor: str) -> VendarixAdapter:
    key = (vendor or "enterprise").lower()
    if key not in _ADAPTERS:
        raise KeyError(f"unsupported vendor/interface {vendor!r}; supported: {sorted(_ADAPTERS)}")
    return _ADAPTERS[key]


__all__ = [
    "AdapterContext",
    "VendarixAdapter",
    "adapter_for",
    "NCRVoyixAdapter",
    "ToshibaTCGSAdapter",
    "DieboldNixdorfAdapter",
    "ScannerScaleAdapter",
    "EnterpriseAdapter",
]
