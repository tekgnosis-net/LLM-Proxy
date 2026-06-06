from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.auth import verify_password
from app.settings import get_settings

router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request):
    s = get_settings()
    if not verify_password(body.password, s.admin_password_hash):
        raise HTTPException(status_code=401, detail="invalid password")
    request.session["authed"] = True
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"authed": bool(request.session.get("authed"))}
