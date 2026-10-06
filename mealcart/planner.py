"""Turns a PlanRequest into a MealPlan using Claude with structured output."""

import os
from pathlib import Path

import anthropic

from .models import MealPlan, PlanRequest

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


def build_user_prompt(req: PlanRequest) -> str:
    lines = [
        f"Plan {req.days} day(s) of {', '.join(req.meals)} for {req.people} people.",
        f"Budget level: {req.budget}.",
    ]
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
    return "\n".join(lines)


def _check_consistency(plan: MealPlan) -> None:
    recipe_ids = {r.id for r in plan.recipes}
    missing = {m.recipe_id for m in plan.schedule} - recipe_ids
    if missing:
        raise PlannerError("The generated plan referenced recipes that weren't included. Please try again.")
    if not plan.grocery_list:
        raise PlannerError("The generated plan had an empty grocery list. Please try again.")


SAMPLE_PLAN = Path(__file__).parent / "sample_plan.json"


async def generate_plan(req: PlanRequest, client: anthropic.AsyncAnthropic | None = None) -> MealPlan:
    if os.environ.get("MEALCART_DEMO") and client is None:
        return MealPlan.model_validate_json(SAMPLE_PLAN.read_text())
    client = client or anthropic.AsyncAnthropic()
    try:
        async with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(req)}],
            output_format=MealPlan,
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
        raise PlannerError("Claude declined to create this plan. Try rewording your notes.")
    if message.stop_reason == "max_tokens":
        raise PlannerError("The plan was too long to finish. Try fewer days or meals.")

    plan = message.parsed_output
    if plan is None:
        raise PlannerError("Claude returned a plan in an unexpected format. Please try again.")
    _check_consistency(plan)
    return plan
