from fastapi import APIRouter, HTTPException

from app.planes import PLANES, PLANE_IDS, plane

router = APIRouter(prefix="/api/v1/planes", tags=["planes"])


@router.get("")
def list_planes():
    return {
        "planes": [PLANES[pid] for pid in PLANE_IDS],
        "governance_rule": (
            "Intelligence produces recommendations. "
            "Operations executes authorized business actions. "
            "Commerce remains responsible for the customer-facing transaction."
        ),
    }


@router.get("/{plane_id}")
def get_plane(plane_id: str):
    info = plane(plane_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown plane {plane_id!r}; expected one of {PLANE_IDS}")
    return info
