from app.assurance.bengal import ThreatMode
from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health():
    from app.assurance.bengal_eval import PROTOCOL_VERSION

    return {
        "status": "operational",
        "bengal": {
            "enabled": True,
            "method_version": "bengal-baa/v1",
            "modes": [m.value for m in ThreatMode],
            "protocol_version": PROTOCOL_VERSION,
            "observation_only": True,
        },
    }


@router.get("/live")
def live():
    return {"status": "live"}


@router.get("/ready")
def ready():
    return {"status": "ready"}
