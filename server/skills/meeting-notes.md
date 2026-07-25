---
name: meeting-notes
description: Turn a messy transcript or bullet dump into clean action-oriented meeting notes.
category: general
enabled: true
triggers:
  keywords:
    - meeting notes
    - meeting minutes
    - minutes for
    - minutes of
    - summarise the meeting
    - summarize the meeting
    - notes from
    - action items
    - action points
    - takeaways from the meeting
    - recap of the meeting
    - meeting recap
    - standup notes
    - retro notes
---

Output meeting notes in this exact structure. Skip any section that has no
content — don't fill it with "N/A" or "None discussed".

## Summary
Two or three sentences. What was the meeting for, what got decided, what's
still open. No fluff.

## Decisions
- One bullet per decision. State the decision, not the discussion.

## Action items
Format each as a checklist item: `- [ ] {action} — **{owner}** by {when}`.
If the owner or date wasn't stated, put `?` — don't invent one.

## Open questions
Anything raised but unresolved. One bullet each.

## Notable context
Only if there's a fact from the meeting that isn't a decision or action
but that a future reader would want to know (e.g., "revenue is down 8%
QoQ", "Sarah is leaving in Q3"). Skip this section otherwise.

Guidelines:

- Preserve names exactly as spoken. Don't retitle "Sam" as "Samuel".
- Neutral tone — no "great discussion!" or "excellent points".
- If the transcript is ambiguous about who said what, don't guess.
- Never invent action items just to fill the section.
- Keep it scannable: a good reader should extract everything they need
  in under 30 seconds.
