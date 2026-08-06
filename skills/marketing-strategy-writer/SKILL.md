---
name: marketing-strategy-writer
description: Draft evidence-backed 90-day marketing strategies.
license: MIT
metadata:
  version: 0.1.0
  author: Jai and Hermes Agent
  hermes:
    tags: [marketing, strategy, roadmap, pitch]
    category: productivity
---

# Marketing Strategy Writer Skill

Draft a practical 90-day marketing strategy from public research and founder
intelligence. Leave service selection, commercial positioning, and pricing for
Diksha to decide before any pitch deck is generated.

## When to Use

Use when strategy intent is `strategy_requested`, or when Diksha explicitly
overrides an `unsure` classification. Do not run for `case_study_only` calls.

## Prerequisites

- Founder Intelligence document
- Pre-call report and evidence ledger
- Strategy-intent decision
- Diksha's available services, delivery capacity, and constraints when known

Read `references/strategy-output-contract.md` before drafting.

## How to Run

Use the supplied evidence to draft recommendations and explicitly surface missing
inputs. Return an internal strategy document; do not create a final deck, send a
proposal, or determine a price.

## Quick Reference

Prioritize work using:

```text
Expected impact × evidence strength × speed to learn ÷ effort and dependency risk
```

Separate quick wins from foundational work and experiments.

## Procedure

1. Restate the founder's goal, baseline, constraints, and strategy ask.
2. Identify the three highest-leverage growth problems supported by evidence.
3. Define a strategic thesis and what must be true for it to work.
4. Create a 30/60/90-day roadmap with owners, dependencies, and decision gates.
5. Recommend channels only when they fit the ICP, offer, economics, and capacity.
6. Define quick wins, foundational work, and controlled experiments.
7. Attach KPIs to decisions; never invent current baselines.
8. Add risks, assumptions, unavailable data, and account-access requests.
9. Provide service-package options as placeholders for Diksha's judgment.
10. Add explicit fields for Diksha's edits, selected scope, and price.

## Pitfalls

- Do not recommend every channel.
- Do not present benchmarks as the client's actual performance.
- Do not promise revenue or fixed growth outcomes.
- Do not invent budget, margin, conversion rate, CAC, or team capacity.
- Do not set pricing or package scope without Diksha.
- Do not generate the pitch deck before Diksha approves strategy and commercials.

## Verification

Verify that every priority maps to a founder goal or evidenced constraint, KPIs
contain no invented baseline, the plan fits 90 days, risks are explicit, and the
document ends at the Diksha approval gate.
