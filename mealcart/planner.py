"""Turns a PlanRequest into a MealPlan using Claude with structured output."""

import json
import os
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from .models import Ingredient, MealPlan, PlanRequest, Recipe, SwapRequest, SwapResult

T = TypeVar("T", bound=BaseModel)

MODEL = os.environ.get("MEALCART_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """\
You are a home-cooking meal planner. You plan a week of meals for a household \
and produce the grocery list they will order for pickup or delivery.

Plan for the household the way a thoughtful cook would: reuse ingredients \
across recipes so little goes to waste, make deliberate use of leftovers \
(e.g. a big dinner that becomes the next day's lunch), keep variety across \
the week, and respect every dietary restriction and allergy without exception.

Recipes: give each a short unique id (r1, r2, ...), realistic times, \
ingredient amounts scaled to the stated servings, and clear numbered-style \
steps a confident beginner can follow.

Schedule: one entry per requested meal per day, labelled "Day 1", "Day 2", \
and so on. Mark an entry as leftovers when it reuses an earlier recipe; scale \
that recipe's servings to cover both meals.

Grocery list: combine every ingredient across all recipes into one entry per \
product, expressed in the amount someone actually buys at a US grocery store \
(e.g. "1 bunch cilantro", "2 pound chicken thighs", "1 can coconut milk"), \
rounded up to sensible package sizes. `name` is a generic product name a store \
search would match ("yellow onion", "boneless skinless chicken thighs"), with no \
quantity in it. `display_text` is the friendly line shown to the shopper. Leave \
off anything the user says they already have, and list common staples you \
assumed they have (salt, pepper, cooking oil, etc.) in pantry_staples_assumed \
instead of the grocery list.\
"""


class PlannerError(Exception):
    """A plan could not be produced; the message is safe to show the user."""


def _preferences(req: PlanRequest) -> list[str]:
    lines = [f"Household: {req.people} people. Budget level: {req.budget}."]
    if req.max_cook_minutes:
        lines.append(f"Keep active + cook time under {req.max_cook_minutes} minutes per meal.")
    optional = [
        ("Diet", req.diet),
        ("Allergies (must avoid completely)", req.allergies),
        ("Dislikes", req.dislikes),
        ("Cuisines they enjoy", req.cuisines),
        ("Already in the pantry/fridge (don't buy)", req.pantry),
        ("Other notes", req.notes),
    ]
    lines += [f"{label}: {value.strip()}" for label, value in optional if value.strip()]
    return lines


def build_user_prompt(req: PlanRequest) -> str:
    lines = [f"Plan {req.days} day(s) of {', '.join(req.meals)} for {req.people} people."]
    lines += _preferences(req)
    if req.favorites:
        favorites = json.dumps([r.model_dump(exclude={"id"}) for r in req.favorites])
        lines.append(
            "These are recipes the household saved as favorites. Work some of them into the "
            "plan where they fit the constraints and the week (rescaled as needed); you don't "
            f"have to use them all:\n{favorites}"
        )
    return "\n".join(lines)


def build_swap_prompt(req: SwapRequest) -> str:
    old = next(r for r in req.plan.recipes if r.id == req.recipe_id)
    others = [r.name for r in req.plan.recipes if r.id != req.recipe_id]
    lines = _preferences(req.preferences)
    lines += [
        f"Here is the household's current meal plan:\n{req.plan.model_dump_json()}",
        f'Replace the recipe "{old.name}" (id {old.id}) with a different dish. Keep id '
        f'"{old.id}" and {old.servings} servings so its scheduled meals (including any '
        "leftovers) still work. Don't repeat a dish already in the plan "
        f"({', '.join(others) or 'none'}), and favor ingredients the plan already uses.",
    ]
    if req.reason.strip():
        lines.append(f"What they'd like instead: {req.reason.strip()}")
    lines.append(
        "Return the new recipe, an updated one-paragraph plan summary, and the complete "
        "grocery list for the whole plan with the new recipe in place of the old one."
    )
    return "\n".join(lines)


def _check_consistency(plan: MealPlan) -> None:
    recipe_ids = {r.id for r in plan.recipes}
    missing = {m.recipe_id for m in plan.schedule} - recipe_ids
    if missing:
        raise PlannerError("The generated plan referenced recipes that weren't included. Please try again.")
    if not plan.grocery_list:
        raise PlannerError("The generated plan had an empty grocery list. Please try again.")


SAMPLE_PLAN = Path(__file__).parent / "sample_plan.json"

DEMO_SWAP = Recipe(
    id="",
    name="Black Bean & Sweet Potato Tacos",
    description="Smoky roasted sweet potatoes and black beans in warm tortillas.",
    servings=2,
    prep_minutes=10,
    cook_minutes=25,
    ingredients=[
        Ingredient(name="sweet potato", quantity=2, unit="each"),
        Ingredient(name="black beans", quantity=1, unit="can"),
        Ingredient(name="corn tortillas", quantity=8, unit="each"),
        Ingredient(name="lime", quantity=1, unit="each"),
    ],
    steps=[
        "Heat oven to 425°F. Dice sweet potatoes, toss with oil, salt and chili powder, and roast 25 minutes.",
        "Warm the drained beans with a squeeze of lime.",
        "Fill warm tortillas with sweet potatoes and beans.",
    ],
)


async def _ask_claude(client: anthropic.AsyncAnthropic, prompt: str, output_format: type[T]) -> T:
    try:
        async with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
            output_config={"effort": "medium"},
            # If a safety classifier declines, re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise PlannerError("The server's Anthropic API key is missing or invalid.") from e
    except anthropic.RateLimitError as e:
        raise PlannerError("Too many requests right now. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise PlannerError(f"Claude API error ({e.status_code}). Please try again.") from e
    except anthropic.APIConnectionError as e:
        raise PlannerError("Couldn't reach the Claude API. Check the server's network connection.") from e

    if message.stop_reason == "refusal":
        raise PlannerError("Claude declined this request. Try rewording your notes.")
    if message.stop_reason == "max_tokens":
        raise PlannerError("The plan was too long to finish. Try fewer days or meals.")
    if message.parsed_output is None:
        raise PlannerError("Claude returned a plan in an unexpected format. Please try again.")
    return message.parsed_output


def _demo_mode(client) -> bool:
    return bool(os.environ.get("MEALCART_DEMO")) and client is None


async def generate_plan(req: PlanRequest, client: anthropic.AsyncAnthropic | None = None) -> MealPlan:
    if _demo_mode(client):
        return MealPlan.model_validate_json(SAMPLE_PLAN.read_text())
    plan = await _ask_claude(client or anthropic.AsyncAnthropic(), build_user_prompt(req), MealPlan)
    _check_consistency(plan)
    return plan


async def swap_recipe(req: SwapRequest, client: anthropic.AsyncAnthropic | None = None) -> MealPlan:
    old = next((r for r in req.plan.recipes if r.id == req.recipe_id), None)
    if old is None:
        raise PlannerError("That meal isn't in the plan anymore. Refresh and try again.")

    if _demo_mode(client):
        result = SwapResult(
            recipe=DEMO_SWAP,
            summary=req.plan.summary,
            grocery_list=req.plan.grocery_list,
            pantry_staples_assumed=req.plan.pantry_staples_assumed,
        )
    else:
        result = await _ask_claude(client or anthropic.AsyncAnthropic(), build_swap_prompt(req), SwapResult)

    new_recipe = result.recipe.model_copy(update={"id": old.id})
    plan = req.plan.model_copy(
        update={
            "recipes": [new_recipe if r.id == old.id else r for r in req.plan.recipes],
            "summary": result.summary,
            "grocery_list": result.grocery_list,
            "pantry_staples_assumed": result.pantry_staples_assumed,
        }
    )
    _check_consistency(plan)
    return plan
