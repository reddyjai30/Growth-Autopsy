---
name: pitch-deck-writer
description: Convert an approved one-problem Strategy Doc into the exact 13- or 14-slide Gamma-ready pitch deck without changing its lane, evidence, maths, or commercial decisions.
license: MIT
metadata:
  version: 0.2.0
  author: Jai and Hermes Agent
  hermes:
    tags: [pitch, deck, gamma, strategy]
    category: productivity
---

# Pitch Deck Writer Skill

Create an evidence-grounded, Gamma-ready pitch deck for Diksha's review.
The result is deck-ready Markdown—not a sent proposal or an exported file.

## When to Use

Use only when strategy intent is `strategy_requested` or Diksha explicitly
overrides an `unsure` decision. Never run it for a case-study-only call.

## Prerequisites

- Approved Strategy Doc
- Confirmed service lane
- Approved commercial inputs or explicit Diksha placeholders

Read `references/deck-output-contract.md` before drafting.

## Procedure

1. Read the approved Strategy Doc completely; treat it as authoritative.
2. Preserve its exact founder quote, one problem, selected lane and maths.
3. Map its sections to the slide sequence in the reference contract.
4. Keep strategy and service reveal on separate slides.
5. Give the gut-punch question its own slide.
6. Include inoculation only for paid-media lanes.
7. Add concise speaker notes and evidence/source to every slide.
8. Keep price on the final slide and use placeholders where unapproved.

## Pitfalls

- Never invent results, baselines, budgets, pricing, testimonials, or urgency.
- Never turn estimates into client analytics.
- Never promise revenue or fixed growth outcomes.
- Never re-diagnose sources or introduce a new service.
- Never claim Gamma export or delivery occurred.

## Verification

Verify 14 sequential slides for paid media or 13 for secondary lanes, all five
slide fields, strategy before vehicle, pricing on the final slide only, exactly two
options, and the locked-date close.
