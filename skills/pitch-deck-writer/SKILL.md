---
name: pitch-deck-writer
description: Draft grounded Gamma-ready pitch deck briefs.
license: MIT
metadata:
  version: 0.1.0
  author: Jai and Hermes Agent
  hermes:
    tags: [pitch, deck, gamma, strategy]
    category: productivity
---

# Pitch Deck Writer Skill

Create an evidence-grounded, Gamma-ready pitch deck brief for Diksha's review.
The result is a Markdown brief—not a sent proposal or an exported deck.

## When to Use

Use only when strategy intent is `strategy_requested` or Diksha explicitly
overrides an `unsure` decision. Never run it for a case-study-only call.

## Prerequisites

- Founder Intelligence document
- Pre-call evidence/report when available
- Confirmed strategy-intent decision

Read `references/deck-output-contract.md` before drafting.

## Procedure

1. State the founder's goal and current situation without exaggeration.
2. Turn evidence into one coherent diagnosis and strategic thesis.
3. Structure a concise slide sequence with one message per slide.
4. Include a practical 30/60/90 roadmap and measurement plan.
5. Use `Diksha input required` for scope, package, price, terms, or missing facts.
6. Add concise speaker notes and evidence references for each slide.
7. End with risks, assumptions, next steps, and an approval checklist.
8. Mark the document `DRAFT — NOT APPROVED OR SENT`.

## Pitfalls

- Never invent results, baselines, budgets, pricing, testimonials, or urgency.
- Never turn estimates into client analytics.
- Never promise revenue or fixed growth outcomes.
- Never claim Gamma export or delivery occurred.

## Verification

Verify every factual slide against the supplied evidence, keep pricing as a
placeholder, ensure the roadmap matches the strategy, and leave the deck at the
Diksha approval gate.
