from fastapi import APIRouter, HTTPException
from app.schemas.auth import LoginRequest, TokenResponse
from app.security.auth import create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    demo_users = {
        "ceo": {"password": "exec123", "role": "executive"},
        "regional1": {"password": "region123", "role": "regional_manager"},
        "sarah": {"password": "store123", "role": "store_manager"},
        "admin": {"password": "admin123", "role": "system_admin"},
    }
    user = demo_users.get(payload.username)
    if not user or user["password"] != payload.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(payload.username, user["role"])
    return TokenResponse(access_token=token)
