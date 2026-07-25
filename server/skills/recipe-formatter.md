---
name: recipe-formatter
description: Present recipes with structured ingredients, timing, and inline food icons.
category: general
enabled: true
triggers:
  keywords:
    - recipe
    - how do i cook
    - how do i make
    - how to cook
    - how to bake
    - how to make
    - ingredients for
    - what do i need to bake
    - what do i need to cook
    - dinner idea
    - lunch idea
    - breakfast idea
    - meal prep
    - cooking instructions
    - baking instructions
---

Recipes should render like a good cookbook page — scannable at a glance,
detailed when you look closer.

## Structure

**Title** — the dish name as an H2 with a matching food icon (`:pizza:`
`:cake:` `:coffee:` `:food:` `:apple:`).

**Quick facts** — a single line with icons: `:clock: {total time} · :users:
{servings} · {difficulty}`. Difficulty is one of Easy / Medium / Hard.

**Ingredients** — a checkbox list grouped by component when it makes
sense (dough, filling, glaze). Use metric first with imperial in
parentheses, e.g. `200 g (1½ cups) flour`.

**Method** — numbered steps. Each step gets one clear verb-first sentence.
If a step has a critical technique note, add it as a nested bullet.

**Notes** (optional) — substitutions, make-ahead advice, storage. Skip
this section if there's nothing to add.

## Guidelines

- Assume the reader is competent but not a pro. Don't over-explain "chop
  the onion" but do specify "dice — about 5 mm cubes" when precision
  matters.
- Timings on each step ("stir for 30 seconds", "bake 25 minutes").
- Never invent obscure ingredients to sound authentic. If a Thai recipe
  really needs galangal, say so and mention ginger as a fallback.
- No "prep time / cook time / total time" table — the one-line quick
  facts row covers it.
- No inspirational preamble. Go straight to the recipe.
