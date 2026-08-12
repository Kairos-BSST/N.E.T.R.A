"""Session authentication and role-based authorization for NETRA."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import database
from config import Config

router = APIRouter(prefix="/auth", tags=["authentication"])
COOKIE = Config.SESSION_COOKIE_NAME


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


def current_user(request: Request):
    user = database.get_user_by_session(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_admin(user=Depends(current_user)):
    if user.get("role") != "administrator":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user

# Backwards-compatible dependency name used by the existing routers.
administrator = require_admin


def require_operator(user=Depends(current_user)):
    if user.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Operator access required.")
    return user


def can_access_job(user: dict, job: dict) -> bool:
    return user.get("role") == "administrator" or job.get("user_id") == user.get("id")


@router.post("/login")
def login(req: LoginRequest, response: Response):
    user = database.get_user_by_username(req.username.strip())
    if not user or not database.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = database.create_session(user["id"], Config.SESSION_TTL_SECONDS)
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", secure=Config.SESSION_COOKIE_SECURE,
        max_age=Config.SESSION_TTL_SECONDS,
    )
    database.record_audit(user["id"], "LOGIN")
    return {"authenticated": True, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


@router.post("/logout")
def logout(request: Request, response: Response):
    user = database.get_user_by_session(request.cookies.get(COOKIE))
    if user:
        database.record_audit(user["id"], "LOGOUT")
    database.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"authenticated": False}


@router.get("/me")
def me(user=Depends(current_user)):
    return {"authenticated": True, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}
