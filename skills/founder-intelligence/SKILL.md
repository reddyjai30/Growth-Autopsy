---
name: founder-intelligence
description: Extract grounded intelligence from founder calls.
license: MIT
metadata:
  version: 0.1.0
  author: Jai and Hermes Agent
  hermes:
    tags: [transcript, founder, discovery-call, intelligence]
    category: research
---

# Founder Intelligence Skill

Turn a speaker-attributed Fathom transcript into structured founder intelligence.
Preserve what was actually said, distinguish founder statements from interviewer
ideas, and classify whether a strategy deliverable was requested.

## When to Use

Use after a matched Fathom recording is stored locally. This skill produces an
internal intelligence document; it does not produce public copy or send a pitch.

## Prerequisites

- Full speaker-attributed transcript with timestamps
- Matched calendar event and company identity
- Pre-call report when available
- `read_file` access to the transcript path

Read `references/output-contract.md` before analyzing the transcript.

## How to Run

Use `read_file` to read the complete transcript. If it is too large for one
operation, read every part in order and maintain speaker/timestamp continuity.
Return the internal document in the final response without publishing it.

## Quick Reference

Extract:

- business model, ICP, offer, pricing, sales motion, and channels;
- founder goals, problems, constraints, objections, priorities, and urgency;
- every number with speaker and timestamp;
- attempted solutions and why they worked or failed;
- explicit asks, commitments, next steps, and permissions;
- conflicts between pre-call public evidence and founder statements.

## Procedure

1. Identify speakers using Fathom names and matched attendee emails.
2. Separate founder statements, Diksha's suggestions, and uncertain attribution.
3. Build a chronological fact ledger with timestamps.
4. Extract problems and root-cause hypotheses without converting hypotheses to facts.
5. Extract goals, constraints, objections, resources, timing, and decision process.
6. Copy every mentioned number into the metrics ledger with its exact context.
7. Compare call statements with the pre-call report when supplied.
8. Classify strategy intent semantically:
   - `strategy_requested`: founder asks for recommendations, plan, proposal,
     services, pricing, help, or clear next steps;
   - `case_study_only`: conversation remains editorial and no strategic help is requested;
   - `unsure`: mixed or ambiguous intent requiring Diksha's decision.
9. Add a short rationale and timestamped evidence for the classification.
10. Produce the Founder Intelligence document using the reference contract.

## Pitfalls

- Never treat Diksha's idea as the founder's commitment.
- Never remove qualifiers from a number or prediction.
- Never infer budget, authority, urgency, or willingness to buy without evidence.
- Do not use keyword counts as the strategy classifier.
- Do not expose the transcript in public-facing outputs.
- Do not generate pricing; reserve it for Diksha.

## Verification

Verify that each fact can be traced to a speaker and timestamp, every number has
context, uncertain attribution is marked, the strategy classification includes
evidence, and no public publishing action occurred.
