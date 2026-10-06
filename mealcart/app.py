"""FastAPI server: JSON API plus the static single-page frontend."""

import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import instacart, planner
from .models import InstacartRequest, InstacartResponse, MealPlan, PlanRequest, SwapRequest

STATIC_DIR = Path(__file__).parent / "static"
AUTH_COOKIE = "mealcart_auth"
PUBLIC_PATHS = ("/login", "/static/", "/healthz")

app = FastAPI(title="MealCart")


# --- Optional password gate -----------------------------------------------
# When MEALCART_PASSWORD is set (e.g. on a public host), every page and API
# call requires signing in once per device so strangers can't spend your
# Claude credits.


def _password() -> str:
    return os.environ.get("MEALCART_PASSWORD", "")


def _auth_token(password: str) -> str:
    return hmac.new(password.encode(), b"mealcart-session", hashlib.sha256).hexdigest()


def _is_signed_in(request: Request) -> bool:
    password = _password()
    if not password:
        return True
    return hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _auth_token(password))


@app.middleware("http")
async def require_password(request: Request, call_next):
    if request.url.path.startswith(PUBLIC_PATHS) or _is_signed_in(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Please sign in again."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MealCart</title><link rel="stylesheet" href="/static/styles.css">
<link rel="icon" href="/static/icon.svg"><link rel="manifest" href="/static/manifest.webmanifest">
</head><body>
<header class="topbar"><h1 class="brand">Meal<span>Cart</span></h1></header><main>
<form method="post" action="/login" class="card">
  <label>Password <input type="password" name="password" autocomplete="current-password" autofocus required></label>
  {error}
  <button type="submit" class="primary">Sign in</button>
</form></main></body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    if not _password():
        return RedirectResponse("/", status_code=303)
    return LOGIN_PAGE.format(error="")


@app.post("/login")
async def login(request: Request):
    password = _password()
    submitted = parse_qs((await request.body()).decode()).get("password", [""])[0]
    if not password or not hmac.compare_digest(submitted.encode(), password.encode()):
        return HTMLResponse(
            LOGIN_PAGE.format(error='<p class="error" role="alert">Wrong password.</p>'), status_code=401
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        AUTH_COOKIE,
        _auth_token(password),
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
    )
    return resp


# --- API -------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/config")
async def config() -> dict:
    return {"instacart_enabled": instacart.is_configured()}


@app.post("/api/plan", response_model=MealPlan)
async def create_plan(req: PlanRequest) -> MealPlan:
    try:
        return await planner.generate_plan(req)
    except planner.PlannerError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/swap", response_model=MealPlan)
async def swap_meal(req: SwapRequest) -> MealPlan:
    try:
        return await planner.swap_recipe(req)
    except planner.PlannerError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/instacart", response_model=InstacartResponse)
async def send_to_instacart(req: InstacartRequest) -> InstacartResponse:
    try:
        url = await instacart.create_shopping_list_link(req.title, req.items)
    except instacart.InstacartError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return InstacartResponse(url=url)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
