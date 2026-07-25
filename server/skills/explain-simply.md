---
name: explain-simply
description: Rewrite jargon-heavy explanations as if talking to a smart friend, no dumbing-down.
category: general
enabled: true
triggers:
  keywords:
    - explain like i'm
    - eli5
    - explain simply
    - in plain english
    - in simple terms
    - for a beginner
    - i don't understand
    - i dont understand
    - what does this mean
    - what does that mean
    - break this down
    - break it down
    - dumb it down
    - like a five
    - like a 5
---

The user wants clarity, not condescension. Rewrite the explanation as if
you were talking to a smart friend who happens not to be an expert in this
specific field.

Structure:

- **Anchor first**. Open with a familiar analogy or a concrete everyday
  example that maps onto the concept. Not a metaphor smorgasbord — one
  strong image.
- **Then the mechanism**. Two or three sentences explaining what's actually
  happening under the analogy. Real vocabulary, but define each unfamiliar
  term the first time you use it.
- **Then the "aha"**. Why does this matter? What does it let people do
  that they otherwise couldn't?

Guidelines:

- Never say "imagine", "picture this", or "think of it like" three times in
  a row. Vary the framing.
- Prefer short sentences to long ones — but not so short it sounds like a
  children's book.
- Don't strip out the interesting technical bits. A smart friend can
  handle "the algorithm's O(n log n)" if you give them one line explaining
  what O(n log n) means.
- Don't use `:idea:` or `:brain:` icons unless the user seems to enjoy
  them — this skill is about words doing the work.
- Skip the "in conclusion" wrap-up. Stop when you've made the point.
