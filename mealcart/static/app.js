"use strict";

const STORAGE_KEY = "mealcart.v1";
const MEAL_ORDER = ["breakfast", "lunch", "dinner"];
const CATEGORY_ORDER = [
  "Produce", "Meat & Seafood", "Dairy & Eggs", "Bakery", "Pantry",
  "Spices & Seasonings", "Frozen", "Beverages", "Other",
];
const LOADING_TIPS = [
  "Picking recipes that share ingredients",
  "Planning leftovers so nothing goes to waste",
  "Scaling recipes for your household",
  "Combining everything into one list",
  "Rounding up to real package sizes",
];

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};

// state = { plan, checked: {itemName: bool}, tab, lastRequest, favorites: [recipe] }
let state = loadState();
state.favorites ||= [];
let instacartEnabled = false;
let currentRecipe = null; // { recipe, fromPlan }

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable (private mode); the app still works for this visit */
  }
}

function showView(name) {
  for (const v of ["form", "loading", "plan"]) $(`#view-${v}`).hidden = v !== name;
  if (name === "form") updateFavoritesRow();
  $("#new-plan").hidden = name !== "plan";
  $("#actions").hidden = !(name === "plan" && state.tab === "groceries");
  window.scrollTo(0, 0);
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 2200);
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    window.location = "/login";
    throw new Error("Please sign in again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? "Please check the form: " + data.detail.map((d) => d.loc.at(-1)).join(", ")
      : data.detail;
    throw new Error(detail || `Something went wrong (${res.status}).`);
  }
  return data;
}

// ---------- Form ----------

function readForm(form) {
  const fd = new FormData(form);
  const minutes = fd.get("max_cook_minutes");
  return {
    people: Number(fd.get("people")),
    days: Number(fd.get("days")),
    meals: fd.getAll("meals"),
    budget: fd.get("budget"),
    diet: fd.get("diet"),
    allergies: fd.get("allergies"),
    dislikes: fd.get("dislikes"),
    cuisines: fd.get("cuisines"),
    max_cook_minutes: minutes ? Number(minutes) : null,
    pantry: fd.get("pantry"),
    notes: fd.get("notes"),
  };
}

function updateFavoritesRow() {
  $("#use-favorites-row").hidden = !state.favorites.length;
  $("#favorites-count").textContent = state.favorites.length;
}

async function onSubmit(e) {
  e.preventDefault();
  const err = $("#form-error");
  err.hidden = true;
  const body = readForm(e.target);
  if (!body.meals.length) {
    err.textContent = "Pick at least one meal to plan.";
    err.hidden = false;
    return;
  }
  state.lastRequest = body;
  const useFavorites = e.target.elements.use_favorites.checked && state.favorites.length;
  const request = { ...body, favorites: useFavorites ? state.favorites.slice(0, 10) : [] };
  showView("loading");
  let i = 0;
  const tipTimer = setInterval(() => {
    $("#loading-tip").textContent = LOADING_TIPS[++i % LOADING_TIPS.length];
  }, 4000);
  try {
    const plan = await apiPost("/api/plan", request);
    state = { ...state, plan, checked: {}, tab: "week" };
    saveState();
    renderPlan();
    showView("plan");
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    showView("form");
  } finally {
    clearInterval(tipTimer);
  }
}

function restoreForm() {
  const req = state.lastRequest;
  if (!req) return;
  const form = $("#plan-form");
  for (const [k, v] of Object.entries(req)) {
    if (k === "meals") {
      form.querySelectorAll('[name="meals"]').forEach((c) => (c.checked = v.includes(c.value)));
    } else if (k === "budget") {
      form.querySelectorAll('[name="budget"]').forEach((r) => (r.checked = r.value === v));
    } else if (form.elements[k]) {
      form.elements[k].value = v ?? "";
    }
  }
}

// ---------- Plan ----------

function isChecked(item) {
  return state.checked?.[item.name] !== false;
}

function renderPlan() {
  const { plan } = state;
  $("#plan-title").textContent = plan.title;
  $("#plan-summary").textContent = plan.summary;
  renderWeek(plan);
  renderGroceries(plan);
  renderSaved();
  selectTab(state.tab || "week");
}

function renderWeek(plan) {
  const recipes = Object.fromEntries(plan.recipes.map((r) => [r.id, r]));
  const days = new Map();
  for (const m of plan.schedule) {
    if (!days.has(m.day)) days.set(m.day, []);
    days.get(m.day).push(m);
  }
  const panel = $("#tab-week");
  panel.replaceChildren();
  for (const [day, meals] of days) {
    meals.sort((a, b) => MEAL_ORDER.indexOf(a.meal_type) - MEAL_ORDER.indexOf(b.meal_type));
    const card = el("div", { className: "card day" }, el("h3", { textContent: day }));
    for (const m of meals) {
      const r = recipes[m.recipe_id];
      const btn = el(
        "button",
        { className: "meal", type: "button" },
        el("span", { className: "meal-type", textContent: m.meal_type }),
        el("span", { className: "meal-name", textContent: r.name },
          m.is_leftovers ? el("span", { className: "meal-tag", textContent: " · leftovers" }) : ""),
        el("span", { className: "chev", textContent: "›" }),
      );
      btn.addEventListener("click", () => openRecipe(r, true));
      card.append(btn);
    }
    panel.append(card);
  }
}

function formatQty(q) {
  return Number.isInteger(q) ? String(q) : String(Math.round(q * 100) / 100);
}

function isFavorite(r) {
  return state.favorites.some((f) => f.name === r.name);
}

function renderFavoriteButton() {
  const fav = isFavorite(currentRecipe.recipe);
  const btn = $("#toggle-favorite");
  btn.setAttribute("aria-pressed", String(fav));
  btn.textContent = fav ? "♥ Saved" : "♡ Save";
}

function toggleFavorite() {
  const r = currentRecipe.recipe;
  if (isFavorite(r)) {
    state.favorites = state.favorites.filter((f) => f.name !== r.name);
    toast("Removed from saved");
  } else {
    state.favorites = [r, ...state.favorites];
    toast("Saved");
  }
  saveState();
  renderFavoriteButton();
  renderSaved();
}

function renderSaved() {
  const panel = $("#tab-saved");
  panel.replaceChildren();
  if (!state.favorites.length) {
    panel.append(el("div", { className: "card empty muted" },
      "No saved recipes yet. Open any recipe and tap ♡ Save to keep it for future weeks."));
    return;
  }
  const card = el("div", { className: "card day" });
  for (const r of state.favorites) {
    const btn = el("button", { className: "meal", type: "button" },
      el("span", { className: "meal-name", textContent: r.name }),
      el("span", { className: "meal-tag", textContent: `${r.prep_minutes + r.cook_minutes} min` }),
      el("span", { className: "chev", textContent: "›" }));
    btn.addEventListener("click", () => openRecipe(r, false));
    card.append(btn);
  }
  panel.append(card,
    el("p", { className: "muted small", textContent: "Saved recipes can be worked into your next plan from the planning form." }));
}

function closeSwap() {
  $("#swap-form").hidden = true;
  $("#swap-form").reset();
  $("#swap-error").hidden = true;
  $("#start-swap").hidden = !currentRecipe?.fromPlan;
}

async function onSwap(e) {
  e.preventDefault();
  const btn = $("#confirm-swap");
  const err = $("#swap-error");
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = "Finding a new meal…";
  try {
    const { lastRequest = {} } = state;
    const plan = await apiPost("/api/swap", {
      plan: state.plan,
      recipe_id: currentRecipe.recipe.id,
      reason: e.target.elements.reason.value,
      preferences: { ...lastRequest, favorites: [] },
    });
    state.plan = plan;
    saveState();
    renderPlan();
    const fresh = plan.recipes.find((r) => r.id === currentRecipe.recipe.id);
    openRecipe(fresh, true);
    toast("Meal swapped. Grocery list updated.");
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Swap it";
  }
}

function openRecipe(r, fromPlan) {
  currentRecipe = { recipe: r, fromPlan };
  renderFavoriteButton();
  closeSwap();
  $("#recipe-name").textContent = r.name;
  $("#recipe-meta").textContent =
    `Serves ${r.servings} · Prep ${r.prep_minutes} min · Cook ${r.cook_minutes} min`;
  $("#recipe-desc").textContent = r.description;
  $("#recipe-ingredients").replaceChildren(
    ...r.ingredients.map((i) => el("li", { textContent: [formatQty(i.quantity), i.unit === "each" ? "" : i.unit, i.name].filter(Boolean).join(" ") })),
  );
  $("#recipe-steps").replaceChildren(...r.steps.map((s) => el("li", { textContent: s })));
  const dialog = $("#recipe-dialog");
  if (!dialog.open) dialog.showModal();
}

function renderGroceries(plan) {
  const recipeNames = Object.fromEntries(plan.recipes.map((r) => [r.id, r.name]));
  const groups = new Map(CATEGORY_ORDER.map((c) => [c, []]));
  for (const item of plan.grocery_list) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  const list = $("#grocery-list");
  list.replaceChildren();
  for (const [category, items] of groups) {
    if (!items.length) continue;
    const card = el("div", { className: "card category" }, el("h3", { textContent: category }));
    for (const item of items) {
      const box = el("input", { type: "checkbox", checked: isChecked(item) });
      box.addEventListener("change", () => {
        state.checked = { ...state.checked, [item.name]: box.checked };
        saveState();
        updateCount();
      });
      const usedIn = item.recipe_ids.map((id) => recipeNames[id]).filter(Boolean).join(", ");
      card.append(el("label", { className: "item" }, box,
        el("span", { className: "item-text", textContent: item.display_text },
          usedIn ? el("small", { textContent: usedIn }) : "")));
    }
    list.append(card);
  }
  $("#staples").textContent = plan.pantry_staples_assumed.length
    ? `Assumed you have: ${plan.pantry_staples_assumed.join(", ")}.`
    : "";
  updateCount();
}

function selectedItems() {
  return state.plan.grocery_list.filter(isChecked);
}

function updateCount() {
  $("#grocery-count").textContent = selectedItems().length;
}

function selectTab(tab) {
  state.tab = tab;
  saveState();
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
  $("#tab-week").hidden = tab !== "week";
  $("#tab-groceries").hidden = tab !== "groceries";
  $("#tab-saved").hidden = tab !== "saved";
  $("#actions").hidden = tab !== "groceries";
}

function listAsText() {
  const lines = [state.plan.title, ""];
  const groups = new Map();
  for (const item of selectedItems()) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item.display_text);
  }
  for (const c of CATEGORY_ORDER.filter((c) => groups.has(c)).concat([...groups.keys()].filter((c) => !CATEGORY_ORDER.includes(c)))) {
    lines.push(c.toUpperCase(), ...groups.get(c).map((t) => `☐ ${t}`), "");
  }
  return lines.join("\n").trim();
}

async function sendToInstacart() {
  const btn = $("#send-instacart");
  const err = $("#action-error");
  err.hidden = true;
  const items = selectedItems();
  if (!items.length) {
    err.textContent = "Check at least one item first.";
    err.hidden = false;
    return;
  }
  // Open the tab synchronously so mobile popup blockers allow it.
  const win = window.open("", "_blank");
  btn.disabled = true;
  btn.textContent = "Building your cart…";
  try {
    const { url } = await apiPost("/api/instacart", {
      title: state.plan.title,
      items: items.map(({ name, display_text, quantity, unit }) => ({ name, display_text, quantity, unit })),
    });
    if (win) win.location = url;
    else window.location = url;
  } catch (ex) {
    win?.close();
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Order with Instacart";
  }
}

// ---------- Wiring ----------

$("#plan-form").addEventListener("submit", onSubmit);
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));
$("#close-recipe").addEventListener("click", () => $("#recipe-dialog").close());
$("#recipe-dialog").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$("#new-plan").addEventListener("click", () => {
  restoreForm();
  showView("form");
});
$("#send-instacart").addEventListener("click", sendToInstacart);
$("#toggle-favorite").addEventListener("click", toggleFavorite);
$("#start-swap").addEventListener("click", () => {
  $("#swap-form").hidden = false;
  $("#start-swap").hidden = true;
  $("#swap-form").elements.reason.focus();
});
$("#cancel-swap").addEventListener("click", closeSwap);
$("#swap-form").addEventListener("submit", onSwap);
$("#copy-list").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(listAsText());
    toast("List copied");
  } catch {
    toast("Couldn't copy. Try Share instead.");
  }
});
$("#share-list").addEventListener("click", async () => {
  const text = listAsText();
  if (navigator.share) {
    try {
      await navigator.share({ title: state.plan.title, text });
    } catch {
      /* user cancelled */
    }
  } else {
    window.print();
  }
});

fetch("/api/config")
  .then((r) => r.json())
  .then((cfg) => {
    instacartEnabled = cfg.instacart_enabled;
    $("#send-instacart").hidden = !instacartEnabled;
  })
  .catch(() => {});

restoreForm();
if (state.plan) {
  renderPlan();
  showView("plan");
} else {
  showView("form");
}
