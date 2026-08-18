---
name: founder-intelligence
description: Extract evidence, public-safety decisions, the founder's one stated problem, strategy intent, and a service lane from discovery calls.
license: MIT
metadata:
  version: 0.2.0
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
- the verbatim answer to “What's one problem I can solve for you?”;
- public-safe, internal-only, confirmation-needed, and excluded claims;
- Traffic, Conversion, Retention and Expansion evidence;
- Psychology, Behaviour, Economics, Attention, Trust and Distribution evidence.

## Procedure

1. Identify speakers using Fathom names and matched attendee emails.
2. Separate founder statements, Diksha's suggestions, and uncertain attribution.
3. Build a chronological fact ledger with timestamps.
4. Extract problems and root-cause hypotheses without converting hypotheses to facts.
5. Extract goals, constraints, objections, resources, timing, and decision process.
6. Copy every mentioned number into the metrics ledger with its exact context.
7. Compare call statements with the pre-call report when supplied.
8. For B2B, check the natural repurchase/renewal calendar and whether buyer and
   end user differ.
9. Classify strategy intent semantically:
   - `strategy_requested`: founder asks for recommendations, plan, proposal,
     services, pricing, help, or clear next steps;
   - `case_study_only`: conversation remains editorial and no strategic help is requested;
   - `unsure`: mixed or ambiguous intent requiring Diksha's decision.
10. Select one service lane from the founder's stated problem; never route from
    a larger opportunity suggested by Diksha.
11. Add a short rationale and timestamped evidence for both classifications.
12. Produce the Founder Intelligence document using the reference contract.
13. End with exactly two machine-readable markers and no text after them:
    `<!-- strategy_intent: strategy_requested -->`,
    `<!-- strategy_intent: case_study_only -->`, or
    `<!-- strategy_intent: unsure -->`, followed by
    `<!-- service_lane: permitted_lane -->`.

## Pitfalls

- Never treat Diksha's idea as the founder's commitment.
- Never remove qualifiers from a number or prediction.
- Never infer budget, authority, urgency, or willingness to buy without evidence.
- Do not use keyword counts as the strategy classifier.
- Do not expose the transcript in public-facing outputs.
- Do not generate pricing, guarantees, capacity, start dates or MMS claims;
  reserve commercial inputs for Diksha.

## Verification

Verify that each fact can be traced to a speaker and timestamp, every number has
context, sensitivity is explicit, the one problem is founder-stated, both routing
classifications include evidence, and no public publishing action occurred.
