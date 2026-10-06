# MealCart

An AI meal planner for the week. Tell it who you're feeding and what you like,
and it plans the meals, writes the recipes, and builds one combined grocery
list. Send that list to Instacart, pick any participating store near you
(Kroger, Costco, Safeway, Publix, Aldi, Wegmans and many more), and check out
for pickup or delivery.

## Features

- **Weekly plan:** breakfast, lunch and/or dinner for 1–7 days, with leftovers
  planned on purpose so less food goes to waste.
- **Recipes:** ingredients scaled to your household, with step-by-step instructions.
- **Smart grocery list:** ingredients combined across recipes, rounded up to
  real package sizes, and grouped by store aisle. Uncheck what you already have.
- **Respects your constraints:** diet, allergies, dislikes, favorite cuisines,
  budget, max cooking time, and what's already in your pantry.
- **Order with Instacart:** one tap turns the list into an Instacart shopping
  page where you choose a store and check out.
- **Copy / Share / Print:** for stores without Instacart, or for shopping in person.
- Works on phones (add it to your home screen) and desktop. Your last plan is
  saved on the device.

## How it works

| Piece | File |
|---|---|
| Form, week view, recipes, grocery list | `mealcart/static/` (plain HTML/CSS/JS) |
| API server | `mealcart/app.py` (FastAPI) |
| Meal planning with Claude (structured output) | `mealcart/planner.py` |
| Instacart shopping-list link | `mealcart/instacart.py` |
| Data shapes | `mealcart/models.py` |

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in the keys
```

- `ANTHROPIC_API_KEY` (required): get one at https://console.anthropic.com.
- `INSTACART_API_KEY` (optional): apply for the Instacart Developer Platform at
  https://www.instacart.com/company/business/developers. Start with
  `INSTACART_ENV=development` (sandbox), then switch to `production` once
  Instacart approves your app. Without a key, the "Order with Instacart" button
  is hidden and Copy/Share still work.

## Run

```bash
uvicorn mealcart.app:app --env-file .env --reload
```

Open http://localhost:8000. To try the UI without any API keys, set
`MEALCART_DEMO=1` and every plan returns a built-in sample.

## Tests

```bash
pytest
```
