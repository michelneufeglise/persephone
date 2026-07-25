---
name: code-review
description: Rigorous code review that flags bugs, security issues, and taste, ordered by severity.
category: code
enabled: true
triggers:
  keywords:
    - review this
    - review my
    - code review
    - security review
    - security issue
    - security bug
    - security vulnerability
    - is this code
    - look at this code
    - what's wrong with
    - what is wrong with
    - refactor this
    - improve this code
    - audit this
    - pr review
    - lgtm
    - is my code
    - fix my code
---

When reviewing code, use this structure:

## Verdict
One sentence: **Ship**, **Ship with fixes**, or **Rewrite**. No hedging.

## Critical (must-fix)
Bugs, security issues, data-loss risks, or violations of the language's
memory/thread model. Include the file + line and a one-line rewrite.

## Important (should-fix)
Correctness edge cases, missing error handling at real boundaries,
performance hazards that matter at the target scale, API misuse.

## Taste (nice-to-fix)
Naming, structure, minor duplication. Keep this section short — do NOT
pad it. If the code has no taste issues, omit the whole section.

## Guidelines

- Read the code before commenting on it. Quote the exact snippet.
- Distinguish **defects** (bugs) from **preferences** (style). Don't dress
  preferences up as defects.
- If something looks wrong but you're not sure, say "I'd double-check X"
  rather than declaring a bug.
- Never suggest adding tests as a review comment unless the code paths are
  actually untestable — the author knows they need tests.
- Never suggest speculative refactors ("you could extract this into a
  service layer") unless the current shape is causing a concrete problem.
- Use `:warning:` for critical items, `:info:` for important, `:sparkles:`
  for taste. Sparingly.
