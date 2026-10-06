import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from mealcart import instacart, planner
from mealcart.app import app
from mealcart.models import InstacartItem, MealPlan, PlanRequest

SAMPLE = MealPlan.model_validate_json(planner.SAMPLE_PLAN.read_text())


class FakeStream:
    def __init__(self, message, calls):
        self.message, self.calls = message, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.message


def fake_client(stop_reason="end_turn", parsed=SAMPLE):
    calls = []
    message = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)

    def stream(**kwargs):
        calls.append(kwargs)
        return FakeStream(message, calls)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    return client, calls


# --- planner -------------------------------------------------------------


def test_user_prompt_includes_preferences():
    req = PlanRequest(people=4, days=5, meals=["lunch", "dinner"], allergies="peanuts", max_cook_minutes=30)
    prompt = planner.build_user_prompt(req)
    assert "5 day(s) of lunch, dinner for 4 people" in prompt
    assert "peanuts" in prompt
    assert "30 minutes" in prompt
    assert "Dislikes" not in prompt  # empty fields are left out


def test_generate_plan_returns_parsed_output():
    client, calls = fake_client()
    plan = asyncio.run(planner.generate_plan(PlanRequest(), client=client))
    assert plan == SAMPLE
    assert calls[0]["output_format"] is MealPlan
    assert calls[0]["model"] == planner.MODEL


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_generate_plan_surfaces_bad_stop_reasons(stop_reason):
    client, _ = fake_client(stop_reason=stop_reason)
    with pytest.raises(planner.PlannerError):
        asyncio.run(planner.generate_plan(PlanRequest(), client=client))


def test_generate_plan_rejects_dangling_recipe_ids():
    broken = SAMPLE.model_copy(deep=True)
    broken.schedule[0].recipe_id = "nope"
    client, _ = fake_client(parsed=broken)
    with pytest.raises(planner.PlannerError):
        asyncio.run(planner.generate_plan(PlanRequest(), client=client))


# --- instacart -----------------------------------------------------------

ITEMS = [InstacartItem(name="lemon", display_text="3 lemons", quantity=3, unit="each")]


def test_instacart_payload_shape():
    payload = instacart.build_payload("Week", ITEMS, "https://example.com")
    assert payload["link_type"] == "shopping_list"
    assert payload["line_items"] == [
        {"name": "lemon", "display_text": "3 lemons", "line_item_measurements": [{"quantity": 3, "unit": "each"}]}
    ]
    assert payload["landing_page_configuration"]["partner_linkback_url"] == "https://example.com"


def test_instacart_creates_link(monkeypatch):
    monkeypatch.setenv("INSTACART_API_KEY", "key-123")
    monkeypatch.setenv("INSTACART_ENV", "development")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"products_link_url": "https://instacart.com/store/list/abc"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await instacart.create_shopping_list_link("Week", ITEMS, http=http)

    assert asyncio.run(run()) == "https://instacart.com/store/list/abc"
    assert seen["url"] == "https://connect.dev.instacart.tools/idp/v1/products/products_link"
    assert seen["auth"] == "Bearer key-123"
    assert seen["body"]["title"] == "Week"


def test_instacart_requires_key(monkeypatch):
    monkeypatch.delenv("INSTACART_API_KEY", raising=False)
    with pytest.raises(instacart.InstacartError):
        asyncio.run(instacart.create_shopping_list_link("Week", ITEMS))


# --- HTTP API ------------------------------------------------------------

client = TestClient(app)


def test_index_and_static_served():
    assert "MealCart" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_plan_endpoint_demo_mode(monkeypatch):
    monkeypatch.setenv("MEALCART_DEMO", "1")
    resp = client.post("/api/plan", json={"people": 2, "days": 3, "meals": ["dinner"]})
    assert resp.status_code == 200
    assert resp.json()["title"] == SAMPLE.title


def test_plan_endpoint_validates_input():
    assert client.post("/api/plan", json={"people": 0}).status_code == 422
    assert client.post("/api/plan", json={"meals": []}).status_code == 422


def test_plan_endpoint_maps_errors(monkeypatch):
    async def boom(req):
        raise planner.PlannerError("nope")

    monkeypatch.setattr(planner, "generate_plan", boom)
    resp = client.post("/api/plan", json={})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "nope"


def test_config_reports_instacart(monkeypatch):
    monkeypatch.delenv("INSTACART_API_KEY", raising=False)
    assert client.get("/api/config").json() == {"instacart_enabled": False}


# --- favorites & swap ----------------------------------------------------


def test_favorites_are_offered_to_claude():
    req = PlanRequest(favorites=[SAMPLE.recipes[0]])
    prompt = planner.build_user_prompt(req)
    assert "saved as favorites" in prompt
    assert SAMPLE.recipes[0].name in prompt


def swap_result(name="Mushroom Risotto"):
    from mealcart.models import SwapResult

    return SwapResult(
        recipe=SAMPLE.recipes[2].model_copy(update={"id": "whatever", "name": name}),
        summary="New summary",
        grocery_list=SAMPLE.grocery_list[:3],
        pantry_staples_assumed=["salt"],
    )


def test_swap_replaces_recipe_and_grocery_list():
    from mealcart.models import SwapRequest, SwapResult

    client, calls = fake_client(parsed=swap_result())
    req = SwapRequest(plan=SAMPLE, recipe_id="r3", reason="something cozier")
    plan = asyncio.run(planner.swap_recipe(req, client=client))

    swapped = next(r for r in plan.recipes if r.id == "r3")  # id kept so the schedule still works
    assert swapped.name == "Mushroom Risotto"
    assert [r.name for r in plan.recipes[:2]] == [r.name for r in SAMPLE.recipes[:2]]
    assert plan.grocery_list == SAMPLE.grocery_list[:3]
    assert plan.summary == "New summary"
    assert calls[0]["output_format"] is SwapResult
    prompt = calls[0]["messages"][0]["content"]
    assert "something cozier" in prompt and "Creamy Tomato Spinach Pasta" in prompt


def test_swap_unknown_recipe():
    from mealcart.models import SwapRequest

    client, _ = fake_client(parsed=swap_result())
    with pytest.raises(planner.PlannerError):
        asyncio.run(planner.swap_recipe(SwapRequest(plan=SAMPLE, recipe_id="zzz"), client=client))


def test_swap_endpoint_demo_mode(monkeypatch):
    monkeypatch.setenv("MEALCART_DEMO", "1")
    resp = client.post("/api/swap", json={"plan": SAMPLE.model_dump(), "recipe_id": "r1"})
    assert resp.status_code == 200
    recipe = next(r for r in resp.json()["recipes"] if r["id"] == "r1")
    assert recipe["name"] == planner.DEMO_SWAP.name


# --- password gate -------------------------------------------------------


def test_password_gate(monkeypatch):
    monkeypatch.setenv("MEALCART_PASSWORD", "hunter2")
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/config").status_code == 401
    assert c.get("/static/styles.css").status_code == 200
    assert c.get("/healthz").status_code == 200

    assert c.post("/login", data={"password": "nope"}, follow_redirects=False).status_code == 401
    assert c.post("/login", data={"password": "hunter2"}, follow_redirects=False).status_code == 303
    assert c.get("/api/config").status_code == 200
    assert "MealCart" in c.get("/").text


def test_no_password_means_open(monkeypatch):
    monkeypatch.delenv("MEALCART_PASSWORD", raising=False)
    assert client.get("/login", follow_redirects=False).status_code == 303
