# Persephone skills

A **skill** is a Markdown file that Persephone can inject into the system
prompt when it's likely to help. Every request runs a tiny "skill selector"
model that reads the user's turn plus a menu of skill descriptions and
picks up to three that apply. If nothing fits, no skill is injected and no
latency is added.

## How they load

- Files in `server/skills/` ship with Persephone.
- Files in `~/.persephone/skills/` load automatically at startup. User
  files with the same `name:` as a bundled skill override the bundled one.
- Skills are re-read on file change — save the .md and the next request
  picks it up.

## Anatomy of a skill

```markdown
---
name: my-skill
description: One clear sentence — the selector reads this to decide relevance.
category: general           # optional; matches delegate categories
enabled: true               # optional; default true. User can toggle in Settings.
triggers:
  keywords:
    - keyword one
    - phrase two words
---

The body is the actual guidance for the assistant. Write it as if you
were talking to the model directly — imperative voice, no meta-commentary.

Give clear structure (headings, bullets), concrete rules, and short
examples. Skills should be **focused**: one skill, one job. If you find
yourself writing "and also…", split it into two skills.
```

## Selection rules

1. **Heuristic pre-filter.** A skill is only a candidate if at least one
   of its `keywords` appears (case-insensitive substring) in the user's
   turn. This means: **write keywords generously**. Include synonyms,
   common phrasings, and the domain vocabulary.
2. **Judge pass.** The small selector model (uses the `judge_model`
   config, same as the auto-router judge) picks up to `MAX_ACTIVE_SKILLS`
   (default 3) from the candidate pool. It's told: "most turns need
   zero — only pick a skill if the description clearly applies."
3. **Fallback.** If the judge model isn't installed or times out, the
   top heuristic hits are used directly.

## Writing tips

- **Descriptions matter more than titles.** The selector sees the
  description, not the body. If it says "structured meeting notes" it
  might beat "clarity-focused note taker" for a "recap our meeting"
  turn — even if the second skill is technically the better fit.
- **Keywords do the pre-filtering.** Miss the keywords and the judge
  never sees the skill. Err on the side of more keywords.
- **Skill bodies are additive, not exclusive.** The primary model still
  sees its regular system prompt (character, memory, MCP, thinking) —
  the skill layers on top. So skills should *specialise* behaviour, not
  redefine it.
- **Test with a boring model.** A skill body that only Qwen 3.6 can
  follow isn't useful. Aim for guidance a 3B model can execute.

## Toggling skills

Settings → Skills lists every discovered skill with an on/off toggle,
description, source (builtin vs. user), and provenance path. Toggles are
persisted in the `skills_disabled` config key.
