from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from app.auth import verify_password, login_required
from app.admin_auth import effective_hash, set_hash, verify_and_hash

router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginBody, request: Request):
    if not verify_password(body.password, await effective_hash()):
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


class ChangePwBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password", dependencies=[Depends(login_required)])
async def change_password(body: ChangePwBody):
    h = verify_and_hash(body.old_password, body.new_password, await effective_hash())
    await set_hash(h)
    return {"ok": True}
