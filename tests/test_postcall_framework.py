from __future__ import annotations

import pytest

from growth_autopsy.postcall_framework import (
    FrameworkValidationError,
    extract_service_lane,
    validate_founder_intelligence,
    validate_growth_report,
    validate_linkedin_post,
    validate_pitch_deck,
    validate_strategy_doc,
)


def growth_report(*, praise: str = "The team built trust through specific customer proof.") -> str:
    return f"""## 1 · Brand Snapshot
Acme is a B2B software company.
It sells a recurring workflow product.
Operations leaders buy the product.
Their teams are the end users.
## 2 · Founder Story
Alice built Acme after experiencing the workflow problem herself.
## 3 · Growth Timeline
The founder described moving from direct validation to repeat customers.
## 4 · Business Model Breakdown
The model is recurring; pricing and margin were not provided.
## 5 · Growth Operating System
### Traffic
Active — founder referrals are established.
### Conversion
Active — Alice leads sales conversations.
### Retention
Gap — a renewal calendar was not established.
### Expansion
Gap — an expansion motion was not established.
## 6 · Market Context
Publicly visible search results suggest an established software category.
## 7 · What Acme Did Really Well
{praise}
## 8 · Key Challenges / Bottlenecks
Founder-led conversion creates a structural capacity ceiling.
## 9 · Opportunities They're Missing
There is an untapped opportunity around codifying the sales motion.
## 10 · Strategic Observations
### Psychology
Our reading is that the founder's lived experience strengthens relevance.
### Trust
This suggests trust currently concentrates around the founder.
## 11 · What Marketing Mosaic Suggests
### Codify the sales motion
**Observation:** Alice leads conversion.
**Evidence:** The founder described the current sales motion.
**Impact:** Growth remains linked to founder capacity.
**Unlock:** Capture and test the repeatable path.
## 12 · Biggest Growth Lever
If we owned this company for the next 90 days, we would focus on codifying the sales motion.
## 13 · What Other Founders Can Learn
1. Lived experience can sharpen an offer.
2. Founder-led sales contains playbook evidence.
3. Repeatability should precede delegation.
## 14 · Expert Summary
| Dimension | Reading |
| --- | --- |
| Brand Maturity | Emerging |
| Biggest Growth Lever | Repeatable sales |
| Biggest Risk if Unchanged | Founder capacity |
| Next Phase Recommendation | Codify and test |

Acme has a strong base for its next operating phase. The ingredients are there.
"""


def strategy_doc(*, paid: bool) -> str:
    inoculation = """## 6D · The Inoculation
You may have run ads before. Weak offers, broken funnels or missing tracking usually
explain the result; prior campaigns remain informative tests. This time begins with
the offer, tracking and a structured testing framework.
""" if paid else ""
    maths = (
        "Management fee: [DIKSHA INPUT REQUIRED]. Ad spend: [DIKSHA INPUT REQUIRED]. "
        "Month 1 is learning; months 2–3 are optimisation, then scale."
        if paid
        else "Founder hours and close-rate inputs were not provided. [DIKSHA INPUT REQUIRED]."
    )
    service = "Meta Ads Management" if paid else "Sales Playbook"
    return f"""## 1 · The Problem, In Their Exact Words
> “Sales depends entirely on me.” — Alice, 00:20
## 2 · The Problem Beneath the Problem
### Surface Cost
The founder owns the daily sales motion.
### Compounding Cost
The required hour and deal inputs were not provided.
### Invisible Cost
The founder cannot step back from conversion.
### Gut-Punch Question
How much capacity stays locked inside one calendar?
## 3 · Why This Problem Exists
This is not a discipline problem. It is a missing-system problem.
## 4 · What We've Seen
Across founder-led businesses studied at this stage, the working motion often exists
before the documentation does.
## 5 · The Destination
### What Your Week Looks Like After This
Monday begins with a team-owned pipeline review.
| Today | Day 90 |
| --- | --- |
| Founder holds the process | Team follows defined stages |
## 6A · The Strategy
### Capture the motion
Record the decisions that already work.
### Codify the path
Turn decisions into stages.
### Transfer ownership
Test the process with the team.
## 6B · The Execution Gap
You could build this yourself. It requires tooling, setup, review and testing cycles,
while the founder's time is more valuable in customer conversations.
{inoculation}## 6C · The Vehicle
| Strategy Phase | MMS Deliverable |
| --- | --- |
| Capture | {service} discovery |
| Codify | {service} build |
| Transfer | {service} rollout |

| System Does | You Do |
| --- | --- |
| Builds and tests the system | Approve, review and take calls |
## 7 · Why Us, Why Now
The Growth Autopsy provides the proof of work. Scope, cadence and capacity require
Diksha's confirmation.
## 8 · The Maths
{maths}
## 9 · The Investment
What becomes possible when the founder no longer carries the entire motion?

**Option A:** [DIKSHA INPUT REQUIRED]

**Option B:** [DIKSHA INPUT REQUIRED]

Choose the 1st or 15th start date after Diksha confirms capacity.
"""


def pitch_deck(*, paid: bool) -> str:
    titles = [
        "Cover",
        "Cost of the Problem",
        "The Gut-Punch Question",
        "Why the System Is Missing",
        "What We've Seen",
        "What Your Week Looks Like",
        "Today and Day 90",
        "The Strategy",
        "The Honest DIY Execution",
    ]
    if paid:
        titles.append("Why Paid Ads May Not Have Worked")
    titles.extend(["The Vehicle", "Why Us, Why Now", "The ROI Maths", "The Investment"])
    count = len(titles)
    slides: list[str] = []
    for number in range(1, count + 1):
        on_slide = "A focused evidence-led message."
        if number == 3:
            on_slide = "1 founder. How much capacity stays locked inside one calendar?"
        if number == count:
            on_slide = "Option A: [DIKSHA INPUT REQUIRED]. Option B: [DIKSHA INPUT REQUIRED]. Choose the 1st or 15th."
        slides.append(
            f"""## Slide {number} · {titles[number - 1]}
**Core message:** One message.
**On-slide copy:** {on_slide}
**Suggested visual:** A simple diagram.
**Evidence/source:** Approved Strategy Doc, Section {min(number, 9)}.
**Speaker notes:** Keep the explanation grounded.
"""
        )
    return "\n".join(slides)


def linkedin_post() -> str:
    body = """<!-- linkedin_mode: founder_story -->
Most founders do not need a bigger sales personality. They need to capture the one
they already use.

Alice built Acme after living the workflow problem herself. That experience gave her
a sharp offer, language customers understand, and the trust to lead every important
sales conversation personally.

That strength now creates the next growth question. When the founder carries every
decision, growth remains tied to one calendar. Publicly visible proof supports the
brand, while the approved report shows that the real asset is Alice's repeatable way
of diagnosing a buyer's needs.

The marketing lesson is simple: founder-led selling is not something to replace. It
is source material for the system that comes next. Acme has already done the hard
part by earning customer understanding. The next-stage opportunity is to capture the
decisions, turn them into stages, and test whether the team can carry the same trust.

What would become possible if the founder's best sales conversation became a company
capability?

Full Growth Autopsy in the comments.

#GrowthAutopsy #B2BGrowth #FounderLedSales"""
    return f"""## Draft Post
{body}
## Public Claim Ledger
- Founder origin — approved report Section 2 — Founder Fact — public-safe.
- Founder-led sales — approved report Section 5 — Founder Fact — public-safe.
- System opportunity — approved report Section 11 — MMS Interpretation — needs confirmation.
## Approval Checklist
- Founder approval is still required.
- This post has not been published.
"""


def test_founder_intelligence_requires_intent_and_lane_markers() -> None:
    document = """## Meeting Metadata
Internal call metadata.
## Executive Summary
The founder requested help with founder-dependent sales.
## Business and Founder Evidence
Acme sells B2B software.
## Founder Story Evidence
The founder described the origin.
## The One Problem Commercial Brief
> “Sales depends entirely on me.” — Alice, 00:20
## Goals, Constraints and Objections
The founder wants a delegatable system.
## Metrics and Calculation Inputs
Close rate: Not provided.
## Growth Operating System Evidence
Traffic: founder referrals. Conversion: founder-led. Retention: not provided. Expansion: not provided.
## Six-Lens Evidence
Psychology: not established. Behaviour: not established. Economics: not established.
Attention: founder-led. Trust: founder-led. Distribution: referrals.
## Opportunities Discussed
A repeatable sales motion.
## Commitments and Next Steps
Diksha will prepare a strategy.
## Public-Safety Ledger
Founder problem — needs confirmation.
## Evidence Ledger
Evidence type: Founder Fact. Speaker/source: Alice. Timestamp: 00:20.
## Open Questions for Diksha
Commercial inputs remain open.
## Strategy and Service Lane Classification
Strategy requested; sales playbook lane.
<!-- strategy_intent: strategy_requested -->
<!-- service_lane: sales_playbook -->
"""
    assert validate_founder_intelligence(document) == (
        "strategy_requested",
        "sales_playbook",
    )
    assert extract_service_lane(document) == "sales_playbook"
    with pytest.raises(FrameworkValidationError):
        validate_founder_intelligence(document.replace("sales_playbook", "anything"))


def test_growth_report_enforces_v2_separation_and_share_layer() -> None:
    validate_growth_report(growth_report(), brand="Acme", has_external_research=True)
    with pytest.raises(FrameworkValidationError, match="pure-credit"):
        validate_growth_report(
            growth_report(praise="The offer is clear, but conversion is weak."),
            brand="Acme",
            has_external_research=True,
        )
    with pytest.raises(FrameworkValidationError, match="case study"):
        validate_growth_report(
            growth_report().replace("Growth Timeline", "Case Study Timeline"),
            brand="Acme",
            has_external_research=True,
        )


def test_strategy_contract_switches_inoculation_by_lane() -> None:
    validate_strategy_doc(strategy_doc(paid=False), service_lane="sales_playbook")
    validate_strategy_doc(strategy_doc(paid=True), service_lane="meta_acquisition")
    with pytest.raises(FrameworkValidationError, match="The Inoculation"):
        validate_strategy_doc(strategy_doc(paid=False), service_lane="meta_acquisition")


def test_deck_contract_requires_13_or_14_sequential_slides() -> None:
    validate_pitch_deck(pitch_deck(paid=False), service_lane="sales_playbook")
    validate_pitch_deck(pitch_deck(paid=True), service_lane="meta_acquisition")
    with pytest.raises(FrameworkValidationError, match="14 sequential"):
        validate_pitch_deck(pitch_deck(paid=False), service_lane="meta_acquisition")


def test_linkedin_contract_is_approval_gated_and_public_safe() -> None:
    validate_linkedin_post(linkedin_post())
    with pytest.raises(FrameworkValidationError, match="case study"):
        validate_linkedin_post(linkedin_post().replace("Growth Autopsy", "case study", 1))
