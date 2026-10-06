"""FastAPI server: JSON API plus the static single-page frontend."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import instacart, planner
from .models import InstacartRequest, InstacartResponse, MealPlan, PlanRequest

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MealCart")


@app.get("/api/config")
async def config() -> dict:
    return {"instacart_enabled": instacart.is_configured()}


@app.post("/api/plan", response_model=MealPlan)
async def create_plan(req: PlanRequest) -> MealPlan:
    try:
        return await planner.generate_plan(req)
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
