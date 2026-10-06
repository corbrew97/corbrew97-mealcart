"""Instacart Developer Platform: turn a grocery list into a shoppable link.

The link opens an Instacart page where the shopper picks any participating
store near them, adjusts the matched products, and checks out for pickup or
delivery. See https://docs.instacart.com/developer_platform_api/
"""

import os

import httpx

from .models import InstacartItem

BASE_URLS = {
    "development": "https://connect.dev.instacart.tools",
    "production": "https://connect.instacart.com",
}


class InstacartError(Exception):
    """The link could not be created; the message is safe to show the user."""


def is_configured() -> bool:
    return bool(os.environ.get("INSTACART_API_KEY"))


def build_payload(title: str, items: list[InstacartItem], linkback_url: str | None = None) -> dict:
    payload: dict = {
        "title": title,
        "link_type": "shopping_list",
        "expires_in": 30,
        "line_items": [
            {
                "name": item.name,
                "display_text": item.display_text or item.name,
                "line_item_measurements": [{"quantity": item.quantity, "unit": item.unit}],
            }
            for item in items
        ],
        "landing_page_configuration": {"enable_pantry_items": True},
    }
    if linkback_url:
        payload["landing_page_configuration"]["partner_linkback_url"] = linkback_url
    return payload


async def create_shopping_list_link(
    title: str,
    items: list[InstacartItem],
    http: httpx.AsyncClient | None = None,
) -> str:
    api_key = os.environ.get("INSTACART_API_KEY")
    if not api_key:
        raise InstacartError("Instacart isn't set up on this server yet (INSTACART_API_KEY is missing).")
    env = os.environ.get("INSTACART_ENV", "development")
    base_url = BASE_URLS.get(env)
    if base_url is None:
        raise InstacartError(f"INSTACART_ENV must be one of {', '.join(BASE_URLS)}.")

    payload = build_payload(
        title, items, os.environ.get("MEALCART_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    )
    owns_client = http is None
    http = http or httpx.AsyncClient(timeout=30)
    try:
        resp = await http.post(
            f"{base_url}/idp/v1/products/products_link",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError as e:
        raise InstacartError("Couldn't reach Instacart. Please try again.") from e
    finally:
        if owns_client:
            await http.aclose()

    if resp.status_code in (401, 403):
        raise InstacartError("Instacart rejected the server's API key.")
    if resp.status_code >= 400:
        raise InstacartError(f"Instacart returned an error ({resp.status_code}).")
    url = resp.json().get("products_link_url")
    if not url:
        raise InstacartError("Instacart didn't return a shopping link.")
    return url
