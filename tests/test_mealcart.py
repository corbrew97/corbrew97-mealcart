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
