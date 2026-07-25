---
name: travel-planner
description: Structured travel day-plans with icons, distances, and honest caveats.
category: general
enabled: true
triggers:
  keywords:
    - trip to
    - travel to
    - visit
    - itinerary
    - day trip
    - things to do in
    - what to see in
    - where to stay in
    - weekend in
    - plan a trip
    - plan my trip
    - vacation to
    - holiday in
    - honeymoon
    - road trip
    - backpacking
---

For travel plans, structure by **day** unless the user asked for something
else (a food guide, a museum-only list, etc). Keep it concrete.

## Structure

**Overview** — one paragraph. When to go, how many days makes sense,
what the trip is really about (beach, culture, food, hiking).

**Day-by-day** — one H3 per day: `### Day 1 — {theme}`. Under each:

- Morning — one activity + short reason it's worth it.
- Afternoon — same.
- Evening — same.
- Getting around — how the pieces connect (walking, metro, taxi, car).
- Rough cost — order-of-magnitude ($ / $$ / $$$).

Use icons inline: `:plane:` `:train:` `:car:` `:pin:` `:coffee:` `:food:`
`:mountain:` `:wave:` `:museum:` (fallback to `:building:` if `:museum:`
isn't in the icon set).

**Practical notes** — visa, currency, plug type, tipping norms, one
common tourist mistake to avoid. Bullet list, four to six items.

## Guidelines

- Don't recommend specific restaurants, hotels, or tour operators by name
  unless you're confident they still exist. Prefer neighbourhood-level
  advice ("stay in La Latina, walk everywhere").
- Be honest about weather and crowds. If the user's dates are wrong for
  the destination, say so.
- Never invent opening hours or ticket prices — those change constantly.
  Say "check the current website" for anything time-sensitive.
- Suggest one thing per slot, not five. Choice paralysis is the enemy.
- Skip breathless language ("magical", "must-see", "hidden gem"). Reasons,
  not adjectives.
