"""Request and response shapes shared by the API, the planner and the frontend."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

MealType = Literal["breakfast", "lunch", "dinner"]

# Units Claude may use on the grocery list. Kept to purchasable units so the
# list maps cleanly onto store products (and onto Instacart measurements).
GroceryUnit = Literal[
    "each",
    "pound",
    "ounce",
    "fl oz",
    "gallon",
    "quart",
    "pint",
    "dozen",
    "bunch",
    "head",
    "clove",
    "package",
    "can",
    "jar",
    "bottle",
    "bag",
    "box",
    "container",
]

Category = Literal[
    "Produce",
    "Meat & Seafood",
    "Dairy & Eggs",
    "Bakery",
    "Pantry",
    "Spices & Seasonings",
    "Frozen",
    "Beverages",
    "Other",
]


class PlanRequest(BaseModel):
    """What the user fills in on the planning form."""

    people: int = Field(2, ge=1, le=12)
    days: int = Field(7, ge=1, le=7)
    meals: list[MealType] = Field(default_factory=lambda: ["dinner"], min_length=1)
    diet: str = Field("", max_length=300, description="e.g. vegetarian, low-carb")
    allergies: str = Field("", max_length=300)
    dislikes: str = Field("", max_length=300)
    cuisines: str = Field("", max_length=300)
    max_cook_minutes: Optional[int] = Field(None, ge=10, le=240)
    budget: Literal["budget", "moderate", "premium"] = "moderate"
    pantry: str = Field("", max_length=1000, description="Things already at home")
    notes: str = Field("", max_length=1000)


# --- Claude's structured output -------------------------------------------


class Ingredient(BaseModel):
    name: str
    quantity: float
    unit: str


class Recipe(BaseModel):
    id: str
    name: str
    description: str
    servings: int
    prep_minutes: int
    cook_minutes: int
    ingredients: list[Ingredient]
    steps: list[str]


class ScheduledMeal(BaseModel):
    day: str
    meal_type: MealType
    recipe_id: str
    is_leftovers: bool


class GroceryItem(BaseModel):
    name: str
    display_text: str
    quantity: float
    unit: GroceryUnit
    category: Category
    recipe_ids: list[str]


class MealPlan(BaseModel):
    title: str
    summary: str
    recipes: list[Recipe]
    schedule: list[ScheduledMeal]
    grocery_list: list[GroceryItem]
    pantry_staples_assumed: list[str]


# --- Instacart handoff ----------------------------------------------------


class InstacartItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    display_text: str = Field("", max_length=200)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)


class InstacartRequest(BaseModel):
    title: str = Field("My weekly groceries", max_length=200)
    items: list[InstacartItem] = Field(min_length=1, max_length=300)


class InstacartResponse(BaseModel):
    url: str
