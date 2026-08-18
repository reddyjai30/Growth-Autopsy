from __future__ import annotations

import re
from collections.abc import Iterable


class FrameworkValidationError(ValueError):
    """Raised when a generated founder-facing document breaks its contract."""


SERVICE_LANES = (
    "meta_acquisition",
    "paid_media_rebuild",
    "google_intent_capture",
    "paid_scaling",
    "amazon_ads",
    "attribution_ads",
    "native_meta",
    "lead_intelligence",
    "outbound_appointment_setting",
    "linkedin_authority",
    "sales_playbook",
    "shopify_cro_aeo",
    "unsure",
)

PAID_MEDIA_LANES = frozenset(
    {
        "meta_acquisition",
        "paid_media_rebuild",
        "google_intent_capture",
        "paid_scaling",
        "amazon_ads",
        "attribution_ads",
        "native_meta",
    }
)

SERVICE_LANE_GUIDE = """
- meta_acquisition — not enough customers or flat sales; Meta acquisition engine.
- paid_media_rebuild — ads were tried but did not work; diagnose and rebuild structure.
- google_intent_capture — buyers search but find competitors; Google intent capture.
- paid_scaling — ads work but cannot scale or return drops; scale architecture.
- amazon_ads — marketplace or Amazon visibility is weak; marketplace share capture.
- attribution_ads — they cannot tell whether marketing works; measurement first.
- native_meta — a cold or niche audience has little search demand; demand creation.
- lead_intelligence — not enough B2B leads or uncertainty about whom to contact.
- outbound_appointment_setting — outreach is not working or consumes founder time.
- linkedin_authority — low awareness, no inbound, or a founder-authority problem.
- sales_playbook — sales depend entirely on the founder and are not delegatable.
- shopify_cro_aeo — traffic exists but the store/funnel under-converts.
- unsure — no single founder-stated problem is sufficiently supported.
""".strip()

SERVICE_LANE_ROUTES = """
meta_acquisition: predictable acquisition (audience research → offer-first creative →
structured testing → scale winners) → Meta Ads Management → conservative spend/AOV
breakeven and return arithmetic.
paid_media_rebuild: diagnose the informative tests (offer/funnel/tracking) → Paid Media
Audit then rebuilt Meta/Google structure → recovered-waste and same-budget arithmetic.
google_intent_capture: high-intent search, shopping and brand protection → Google Ads
Management → search volume × CTR × close rate × AOV when founder inputs exist.
paid_scaling: creative-volume system, audience expansion and margin-based bidding →
Scaling Retainer → incremental profit above the evidenced current ceiling.
amazon_ads: ranking, defensive and competitor-conquest campaigns → Amazon Ads Management
→ category demand × share shift × margin when founder inputs exist.
attribution_ads: tracking infrastructure, attribution clarity, then optimisation →
Tracking / Attribution Setup + Ads Management → cost of unattributable spend.
native_meta: native + Meta prospecting to educate, then retarget → Native + Meta
Full-Funnel → acquired-customer cost versus evidenced current economics.
lead_intelligence: signal-based identification, qualification and prioritisation → Lead
Intelligence System (one-time) → prospects × close rate × AOV.
outbound_appointment_setting: systematic multi-touch outreach, response handling and
booked calls → Outbound Appointment Setting → meetings × close rate × AOV, annualised.
linkedin_authority: founder-voice authority and consistent content → LinkedIn Authority
Content Management → evidenced inbound value and reduced outbound dependence.
sales_playbook: document, codify and delegate the working sales motion → Sales Playbook
(one-time) → founder hours reclaimed and de-risked hiring.
shopify_cro_aeo: funnel diagnosis, friction removal and AI-search visibility → Shopify
CRO + AEO Optimisation → traffic × conservative conversion lift × AOV.
unsure: do not select or name a service. Use [DIKSHA INPUT REQUIRED: SELECT SERVICE LANE]
and leave lane-dependent commercial logic unfilled.
""".strip()


GROWTH_REPORT_SECTIONS = (
    "Brand Snapshot",
    "Founder Story",
    "Growth Timeline",
    "Business Model Breakdown",
    "Growth Operating System",
    "Market Context",
    "What {brand} Did Really Well",
    "Key Challenges / Bottlenecks",
    "Opportunities They're Missing",
    "Strategic Observations",
    "What Marketing Mosaic Suggests",
    "Biggest Growth Lever",
    "What Other Founders Can Learn",
    "Expert Summary",
)

STRATEGY_BASE_SECTIONS = (
    "The Problem, In Their Exact Words",
    "The Problem Beneath the Problem",
    "Why This Problem Exists",
    "What We've Seen",
    "The Destination",
    "The Strategy",
    "The Execution Gap",
    "The Vehicle",
    "Why Us, Why Now",
    "The Maths",
    "The Investment",
)

FOUNDER_INTELLIGENCE_SECTIONS = (
    "Meeting Metadata",
    "Executive Summary",
    "Business and Founder Evidence",
    "Founder Story Evidence",
    "The One Problem Commercial Brief",
    "Goals, Constraints and Objections",
    "Metrics and Calculation Inputs",
    "Growth Operating System Evidence",
    "Six-Lens Evidence",
    "Opportunities Discussed",
    "Commitments and Next Steps",
    "Public-Safety Ledger",
    "Evidence Ledger",
    "Open Questions for Diksha",
    "Strategy and Service Lane Classification",
)


FOUNDER_INTELLIGENCE_CONTRACT = f"""
Return private, evidence-led Markdown without an H1. Use these exact H2 headings in
this exact order:

## Meeting Metadata
## Executive Summary
## Business and Founder Evidence
## Founder Story Evidence
## The One Problem Commercial Brief
## Goals, Constraints and Objections
## Metrics and Calculation Inputs
## Growth Operating System Evidence
## Six-Lens Evidence
## Opportunities Discussed
## Commitments and Next Steps
## Public-Safety Ledger
## Evidence Ledger
## Open Questions for Diksha
## Strategy and Service Lane Classification

The One Problem Commercial Brief must isolate the founder's answer to “What's one
problem I can solve for you?” Quote it verbatim with speaker and timestamp. Separate
the symptom, founder-stated consequences, founder-provided numbers, desired outcome,
decision authority, urgency, and anything still unknown. Never select a problem
suggested only by the interviewer.

Metrics and Calculation Inputs is a ledger, not a place to estimate. Record founder-
provided AOV, close rate, capacity, revenue, spend, pricing, time cost and other inputs
with timestamps. Mark every absent input “Not provided”; never substitute a benchmark.

Growth Operating System Evidence covers Traffic, Conversion, Retention and Expansion.
For B2B, explicitly check (a) natural repurchase/renewal/calendar cycles and (b) whether
the buyer differs from the end user. Six-Lens Evidence covers Psychology, Behaviour,
Economics, Attention, Trust and Distribution without turning interpretations into fact.

Public-Safety Ledger must mark details Public-safe, Internal-only, Needs confirmation,
or Exclude. Exclude exact spend, declining metrics, NDA relationships, passing client
names, supplier terms, personal finance, health/family matters, legal/rebrand issues,
off-record comments, hesitation-marked details and any metric the founder declined to
share.

In the Evidence Ledger, each compact entry contains Evidence type (Founder Fact,
Observation, or MMS Interpretation), Speaker/source, Timestamp/URL where available,
Statement or concise paraphrase, Allowed wording, Confidence, and Sensitivity. Founder
Facts may be stated as facts; Observations use “appears to” or “publicly visible”; MMS
Interpretations use “our reading is”, “this suggests”, or “may indicate”.

Classify intent as strategy_requested only when the founder requests recommendations,
a plan, proposal, service, pricing, help, or clear strategic next steps; case_study_only
when the conversation remains editorial; unsure when attribution or intent is mixed.
Select exactly one service lane from this guide, based only on the one problem the
founder actually named:
{SERVICE_LANE_GUIDE}

End with exactly these two machine-readable markers, with no text after them:
<!-- strategy_intent: strategy_requested|case_study_only|unsure -->
<!-- service_lane: {'|'.join(SERVICE_LANES)} -->
Replace each pipe-separated set with one permitted value.
""".strip()


COMMON_FOUNDER_VISIBLE_RULES = """
- Make the founder feel deeply understood, never audited, embarrassed, naive or behind.
- Use warm, respectful consultant language. No jargon dumping or generic flattery.
- Never mention transcripts, recordings, AI, prompt mechanics or the production process.
- Founder Fact: write as fact. Public Observation: “appears to” or “publicly visible”.
  MMS Interpretation: “our reading is”, “this suggests”, or “may indicate”.
- Do not invent numbers, dates, metrics, intent, consent, performance or outcomes.
- Omit confidential/internal-only details and anything the founder declined to share.
- Never bash competitors, agencies, freelancers or partners. Prior ad activity is an
  informative test that produced learning, never a failure.
- In regulated industries, describe positioning and operations, not client outcomes.
- Do not use the phrase “case study” anywhere in founder-visible copy.
""".strip()


GROWTH_REPORT_CONTRACT = f"""
Create a founder-facing Growth Intelligence Report / Growth Autopsy. It must feel like
“we understood your business better than most people”, not “we analysed you”.

{COMMON_FOUNDER_VISIBLE_RULES}

Use Markdown without an H1 and these H2 headings in this exact order. Market Context is
included only when external research exists; otherwise omit that H2 completely.

## 1 · Brand Snapshot
Four to six concise lines: brand, niche, offer and buyer. Facts only.
## 2 · Founder Story
One coherent origin-to-breakthrough narrative. Keep story material near 20% of the
report. If one exceptional human story exists, give it one memorable H3 title.
## 3 · Growth Timeline
Founder facts only: stages, jumps, milestones and turning points. Omit unsupported dates
or numbers.
## 4 · Business Model Breakdown
Products, pricing structure, AOV, revenue streams and margin logic only when evidenced.
## 5 · Growth Operating System
Use H3 headings Traffic, Conversion, Retention and Expansion. State facts only and mark
each item Active, Gap, or Not established. Never diagnose or recommend here. For B2B,
include repurchase/renewal calendar and buyer-versus-end-user evidence.
## 6 · Market Context
Optional. Only cited public research: market, category and neutral competitive context.
## 7 · What [Brand] Did Really Well
Pure, specific, earned credit with reasons. No critique, “but”, “however”, “although” or
hidden setup for a gap. This must be safe and appealing for the founder to share.
## 8 · Key Challenges / Bottlenecks
Diagnosis only: structural friction, constraints and ceilings, never founder failure.
## 9 · Opportunities They're Missing
Gaps only, not recommendations. Use language such as “there is an untapped…”.
## 10 · Strategic Observations
Use relevant H3 lenses from Psychology, Behaviour, Economics, Attention, Trust and
Distribution. Give one short pattern-level MMS interpretation per relevant lens.
## 11 · What Marketing Mosaic Suggests
This starts on a fresh page in rendered documents. Every recommendation is a separate
H3 block with all four bold labels in order: Observation, Evidence, Impact, Unlock.
Every recommendation must trace to evidence established in Parts A–C. Quantify only
when defensible; never say “you should hire us”.
## 12 · Biggest Growth Lever
Exactly one lever. Start: “If we owned this company for the next 90 days, we would focus
on…” Make it the sharpest, most specific section.
## 13 · What Other Founders Can Learn
Exactly 3–5 numbered, transferable, LinkedIn-ready lessons.
## 14 · Expert Summary
Include a four-row table labelled Brand Maturity, Biggest Growth Lever, Biggest Risk if
Unchanged, and Next Phase Recommendation. Follow with one final verdict paragraph that
ends with genuine optimism and includes the sentence “The ingredients are there.”

Never add Questions Worth Investigating. Keep praise, diagnosis, gaps and advice in
their assigned sections. Aim for this emphasis: story 20%, business model 20%, growth
system 30%, bottlenecks 15%, expert analysis 15%.
""".strip()


STRATEGY_CONTRACT = f"""
Create a private one-problem Strategy Doc designed to earn agreement on the thinking
before introducing the service. Use only the one problem explicitly stated by the
founder. The psychology sequence is Agitate → Mirror → Diagnose → Authority → Vision →
Strategy → Gap → Inoculate when applicable → Vehicle → De-risk → ROI → Price.

{COMMON_FOUNDER_VISIBLE_RULES}

Use Markdown without an H1. Use these H2 headings in this exact order; include 6D only
for paid-media lanes and place it between 6B and 6C:

## 1 · The Problem, In Their Exact Words
Open with one verbatim founder quote in a Markdown blockquote. Give timestamp/source.
Do not paraphrase it here.
## 2 · The Problem Beneath the Problem
Use H3 Surface Cost, Compounding Cost, Invisible Cost, and Gut-Punch Question. Quantify
only through founder-provided arithmetic; label missing inputs. End with exactly one
question and do not answer it.
## 3 · Why This Problem Exists
Mandatory absolution: explain the structural/system reason and explicitly remove blame.
## 4 · What We've Seen
Two or three MMS pattern observations. Use only approved MMS claims or anonymised stories
present in the source. Never invent track record; the $500K+ claim is allowed only if an
approved MMS commercial input explicitly supplies it.
## 5 · The Destination
Paint day 90 using “What Your Week Looks Like After This”, then a Today / Day 90 table
with matching rows.
## 6A · The Strategy
Three or four plain-English phases describing what must become true. No MMS service or
deliverable names. It must be genuinely useful standalone.
## 6B · The Execution Gap
Begin “You could build this yourself.” Respectfully show tools, setup, test cycles and
founder opportunity cost; technically possible, economically irrational, never belittling.
## 6D · The Inoculation
Paid media only. Name the concern first, then weak offer / broken funnel / missing
tracking, prior campaigns as informative tests, and the offer-first, tracking-first,
structured-testing difference. Never blame a past agency or founder.
## 6C · The Vehicle
Name the one MMS service selected by the founder's problem. Include a Strategy Phase /
MMS Deliverable table with complete one-to-one mapping, then a System Does / You Do table
where founder effort is limited to approve, review and take calls where relevant.
## 7 · Why Us, Why Now
Use prior research as proof of work. Include only evidenced deliverables, timeline,
cadence, risk reversal, capacity, guarantee or pilot. True scarcity only, matter-of-fact.
## 8 · The Maths
Use only founder-provided AOV, close rate, capacity and other inputs; show conservative
arithmetic, monthly value, annual value and return multiple when calculable. Otherwise
show clearly labelled input fields for Diksha. Paid-media lanes must transparently show
both the management-fee field and recommended-ad-spend field and state month 1 learning,
months 2–3 optimisation, then scale. Do not reveal a final price here.
## 9 · The Investment
Final section. One vivid closing question, then exactly Option A and Option B within the
same lane, price/input shown once, inclusions, transparent ad spend where applicable,
and a true locked start-date choice. Never add Option C, discounts, flexibility language,
or “let me know”. If commercial data is absent, use conspicuous [DIKSHA INPUT REQUIRED]
fields rather than invented terms.

The lane is supplied separately. Broken-funnel exception: sequence leak repair before
traffic and keep both options within that lane. Compound problems still get one lane and
one ROI story; put other opportunities in the Growth Intelligence Report, not this doc.

Use this fixed route for the supplied lane; do not substitute another service:
{SERVICE_LANE_ROUTES}
""".strip()


PITCH_DECK_CONTRACT = f"""
Create a Gamma-ready Markdown pitch deck directly from the APPROVED Strategy Doc. Do not
re-diagnose the transcript, add a new service, alter the lane, or add unsupported claims.

{COMMON_FOUNDER_VISIBLE_RULES}

Use no H1. Each slide is one H2 in the form “## Slide N · Title”. Under every slide use
the bold labels Core message, On-slide copy, Suggested visual, Evidence/source, and
Speaker notes. Preserve this mapping:

1 Cover — brand + exact founder quote.
2 Surface, compounding and invisible cost arithmetic.
3 Gut-punch question only: one number, one question, nothing else in On-slide copy.
4 Absolution: it is the missing system, not the founder.
5 Pattern authority and only approved MMS proof.
6 What the founder's week looks like after this.
7 Today / Day 90 contrast.
8 Strategy phases only; no service name.
9 Honest DIY maths/execution gap.
10 Paid-media inoculation only.
10 (secondary lane) or 11 (paid lane) Vehicle reveal with strategy ↔ service mapping and
System Does / You Do.
Next slide Why us, why now: scope, timeline, cadence, evidenced risk reversal/capacity.
Penultimate slide ROI bridge using only founder numbers. For paid media, transparently
include management-fee and ad-spend input fields without inventing values.
Final slide Investment: vivid question, exactly two same-lane options, price last and
once, transparent spend, and locked date.

Paid-media decks contain exactly 14 sequential slides. Secondary-lane decks contain
exactly 13 and omit inoculation. Pricing may appear only on the final slide; the ROI
slide may name commercial input fields but must not fill an unapproved final price.
""".strip()


LINKEDIN_POST_CONTRACT = f"""
Create one founder-approval LinkedIn Growth Autopsy post derived from the APPROVED Growth
Intelligence Report. It is a share layer, not a fresh diagnosis and not a sales pitch.

{COMMON_FOUNDER_VISIBLE_RULES}

Use Markdown without an H1 and exactly these H2 headings:
## Draft Post
## Public Claim Ledger
## Approval Checklist

Under Draft Post, begin with exactly one HTML marker choosing the best evidenced mode:
<!-- linkedin_mode: founder_story|contrarian_growth_engine|memorable_customer_story -->
Use founder_story for an origin or pivotal founder decision; contrarian_growth_engine
for a surprising, evidence-backed channel/strategy insight; memorable_customer_story
for one human customer anecdote that reveals the brand. Never force a mode when its
source story is absent.
Then write 130–350 words in this sequence: pattern-breaking hook; founder/customer story;
tension or contrast; evidence stack; marketing translation; earned founder credit; one
next-stage opportunity; open loop; “Full Growth Autopsy in the comments.” CTA; 3–5
focused hashtags. Use short paragraphs and generous line breaks, with no internal heading
or table inside the post. The founder is the protagonist and MMS is the respectful observer.
Use Section 7, Section 13, the strongest approved story and public-safe metrics. Avoid
clickbait claims such as “they don't know it” and “CAC of 0” unless the approved report
literally and defensibly supports them.

Public Claim Ledger must list every material public claim and its approved report section,
evidence type and safety status. Approval Checklist must explicitly confirm founder
approval is still required and that no post has been published.
""".strip()


def _canonical_heading(value: str) -> str:
    value = re.sub(r"^\s*(?:slide\s+\d+|\d+[A-Z]?)\s*[·.):-]+\s*", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _h2_sections(document: str) -> tuple[list[str], dict[str, str]]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", document))
    headings: list[str] = []
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = _canonical_heading(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        headings.append(key)
        sections[key] = document[match.end() : end].strip()
    return headings, sections


def _list_count(content: str) -> int:
    return len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S", content))


def _require_in_order(
    actual: Iterable[str], expected: Iterable[str], errors: list[str]
) -> None:
    actual_list = list(actual)
    expected_list = [_canonical_heading(item) for item in expected]
    if actual_list != expected_list:
        errors.append(
            "H2 sections must be exactly: " + " → ".join(expected)
        )


def _reject_founder_visible_leaks(document: str, errors: list[str]) -> None:
    visible = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", document)
    visible = re.sub(r"<!--.*?-->", "", visible, flags=re.S)
    if re.search(r"(?m)^#\s+\S", document):
        errors.append("document must not include an H1; the application supplies the cover")
    if re.search(r"\bcase[ -]?study\b", visible, re.I):
        errors.append("founder-visible copy must not use 'case study'")
    if re.search(r"questions worth investigating", visible, re.I):
        errors.append("formal documents must not include Questions Worth Investigating")
    if re.search(r"\b(?:AI[- ]generated|the transcript|this transcript|the recording)\b", visible, re.I):
        errors.append("founder-visible copy exposes the production process")


def validate_founder_intelligence(document: str) -> tuple[str, str]:
    intent_markers = re.findall(
        r"<!--\s*strategy_intent:\s*(strategy_requested|case_study_only|unsure)\s*-->",
        document,
        flags=re.I,
    )
    lane_markers = re.findall(
        rf"<!--\s*service_lane:\s*({'|'.join(SERVICE_LANES)})\s*-->",
        document,
        flags=re.I,
    )
    expected_tail = re.compile(
        rf"<!--\s*strategy_intent:\s*(?:strategy_requested|case_study_only|unsure)\s*-->\s*"
        rf"<!--\s*service_lane:\s*(?:{'|'.join(SERVICE_LANES)})\s*-->\s*$",
        re.I,
    )
    errors: list[str] = []
    if re.search(r"(?m)^#\s+\S", document):
        errors.append("document must not include an H1")
    if len(intent_markers) != 1 or len(lane_markers) != 1 or not expected_tail.search(document):
        errors.append(
            "document must end with exactly one intent marker followed by exactly one "
            "permitted service-lane marker"
        )
    headings, sections = _h2_sections(document)
    _require_in_order(headings, FOUNDER_INTELLIGENCE_SECTIONS, errors)
    growth_os = sections.get(_canonical_heading("Growth Operating System Evidence"), "")
    for pillar in ("Traffic", "Conversion", "Retention", "Expansion"):
        if pillar.casefold() not in growth_os.casefold():
            errors.append(f"Growth OS evidence is missing {pillar}")
    lenses = sections.get(_canonical_heading("Six-Lens Evidence"), "")
    for lens in ("Psychology", "Behaviour", "Economics", "Attention", "Trust", "Distribution"):
        if lens.casefold() not in lenses.casefold():
            errors.append(f"Six-Lens evidence is missing {lens}")
    safety = sections.get(_canonical_heading("Public-Safety Ledger"), "")
    if not re.search(
        r"public-safe|internal-only|needs confirmation|exclude", safety, re.I
    ):
        errors.append("Public-Safety Ledger is missing a permitted safety decision")
    ledger = sections.get(_canonical_heading("Evidence Ledger"), "")
    if not re.search(r"Founder Fact|Observation|MMS Interpretation", ledger, re.I):
        errors.append("Evidence Ledger is missing an evidence type")
    if intent_markers and intent_markers[0].casefold() == "strategy_requested":
        problem = sections.get(
            _canonical_heading("The One Problem Commercial Brief"), ""
        )
        if not re.search(r"(?m)^>\s+\S", problem):
            errors.append("strategy-requested calls require the founder's verbatim problem quote")
    if errors:
        raise FrameworkValidationError(
            "Founder Intelligence contract failed: " + "; ".join(errors)
        )
    return intent_markers[0].casefold(), lane_markers[0].casefold()


def extract_service_lane(document: str) -> str:
    matches = re.findall(
        rf"service_lane\s*:\s*({'|'.join(SERVICE_LANES)})", document, flags=re.I
    )
    return matches[-1].casefold() if matches else "unsure"


def validate_growth_report(
    document: str, *, brand: str, has_external_research: bool
) -> None:
    headings, sections = _h2_sections(document)
    expected = [item.format(brand=brand) for item in GROWTH_REPORT_SECTIONS]
    if not has_external_research:
        expected.remove("Market Context")
    errors: list[str] = []
    _require_in_order(headings, expected, errors)
    _reject_founder_visible_leaks(document, errors)
    brand_snapshot = sections.get(_canonical_heading("Brand Snapshot"), "")
    nonempty_lines = [line for line in brand_snapshot.splitlines() if line.strip()]
    if not 4 <= len(nonempty_lines) <= 6:
        errors.append("Brand Snapshot must contain 4–6 non-empty lines")
    os_section = sections.get(_canonical_heading("Growth Operating System"), "")
    for pillar in ("Traffic", "Conversion", "Retention", "Expansion"):
        if not re.search(rf"(?m)^###\s+{re.escape(pillar)}\s*$", os_section, re.I):
            errors.append(f"Growth Operating System is missing H3 {pillar}")
    if re.search(r"\b(?:should|recommend|we would|opportunit(?:y|ies))\b", os_section, re.I):
        errors.append("Growth Operating System must contain facts, not diagnosis or advice")
    praise = sections.get(_canonical_heading(f"What {brand} Did Really Well"), "")
    if re.search(r"\b(?:but|however|although|yet)\b", praise, re.I):
        errors.append("the pure-credit section contains contrast/critique language")
    missed = sections.get(_canonical_heading("Opportunities They're Missing"), "")
    if re.search(r"\b(?:should|recommend|we would|focus on)\b", missed, re.I):
        errors.append("Section 9 must identify gaps without recommending solutions")
    suggestions = sections.get(_canonical_heading("What Marketing Mosaic Suggests"), "")
    for label in ("Observation", "Evidence", "Impact", "Unlock"):
        if not re.search(rf"\*\*{label}:?\*\*", suggestions, re.I):
            errors.append(f"MMS suggestions are missing the {label} label")
    lever = sections.get(_canonical_heading("Biggest Growth Lever"), "")
    if not re.search(
        r"If we owned this company for the next 90 days, we would focus on", lever, re.I
    ):
        errors.append("Biggest Growth Lever is missing the required 90-day focus statement")
    lessons = sections.get(_canonical_heading("What Other Founders Can Learn"), "")
    lesson_count = _list_count(lessons)
    if not 3 <= lesson_count <= 5:
        errors.append(f"share layer has {lesson_count} lessons; expected 3–5")
    summary = sections.get(_canonical_heading("Expert Summary"), "")
    for label in (
        "Brand Maturity",
        "Biggest Growth Lever",
        "Biggest Risk if Unchanged",
        "Next Phase Recommendation",
    ):
        if label.casefold() not in summary.casefold():
            errors.append(f"Expert Summary is missing {label}")
    if "the ingredients are there" not in summary.casefold():
        errors.append("Expert Summary must end with grounded optimism")
    if errors:
        raise FrameworkValidationError("Growth report contract failed: " + "; ".join(errors))


def validate_strategy_doc(document: str, *, service_lane: str) -> None:
    paid_media = service_lane in PAID_MEDIA_LANES
    expected = list(STRATEGY_BASE_SECTIONS)
    if paid_media:
        expected.insert(7, "The Inoculation")
    headings, sections = _h2_sections(document)
    errors: list[str] = []
    _require_in_order(headings, expected, errors)
    _reject_founder_visible_leaks(document, errors)
    exact_problem = sections.get(_canonical_heading("The Problem, In Their Exact Words"), "")
    if not re.search(r"(?m)^>\s+\S", exact_problem):
        errors.append("Section 1 must contain the founder's verbatim blockquote")
    beneath = sections.get(_canonical_heading("The Problem Beneath the Problem"), "")
    for subsection in ("Surface Cost", "Compounding Cost", "Invisible Cost", "Gut-Punch Question"):
        if not re.search(rf"(?m)^###\s+{re.escape(subsection)}\s*$", beneath, re.I):
            errors.append(f"Section 2 is missing H3 {subsection}")
    if beneath.count("?") != 1 or not beneath.rstrip().endswith("?"):
        errors.append("Section 2 must end with exactly one unanswered gut-punch question")
    strategy = sections.get(_canonical_heading("The Strategy"), "")
    if re.search(r"\bMarketing Mosaic|\bMMS\b|\bretainer\b", strategy, re.I):
        errors.append("6A must remain service-free")
    phase_count = len(re.findall(r"(?m)^###\s+\S", strategy))
    if not 3 <= phase_count <= 4:
        errors.append(f"6A contains {phase_count} phases; expected 3–4")
    execution = sections.get(_canonical_heading("The Execution Gap"), "")
    if not re.search(r"You could build this yourself", execution, re.I):
        errors.append("6B must respectfully acknowledge the DIY path")
    vehicle = sections.get(_canonical_heading("The Vehicle"), "")
    for label in ("Strategy Phase", "MMS Deliverable", "System Does", "You Do"):
        if label.casefold() not in vehicle.casefold():
            errors.append(f"6C is missing {label}")
    investment = sections.get(_canonical_heading("The Investment"), "")
    for label in ("Option A", "Option B"):
        count = len(re.findall(rf"\b{re.escape(label)}\b", investment, re.I))
        if count != 1:
            errors.append(f"Investment must contain {label} exactly once")
    if re.search(r"\bOption C\b", investment, re.I):
        errors.append("Investment must contain exactly two options")
    if re.search(r"\blet me know\b", investment, re.I):
        errors.append("Investment must end with a locked-date mechanism")
    if not re.search(
        r"\b(?:1st|15th)\b|\[DIKSHA INPUT REQUIRED[^]]*(?:DATE|START)",
        investment,
        re.I,
    ):
        errors.append("Investment is missing a locked date or explicit start-date input")
    investment_heading = re.search(
        r"(?m)^##\s+(?:9\s*[·.):-]\s*)?The Investment\s*$", document, re.I
    )
    if investment_heading and re.search(
        r"\b(?:price|fee|retainer|investment|option)\b[^\n]{0,30}(?:£|\$|€)\s*\d",
        document[: investment_heading.start()],
        re.I,
    ):
        errors.append("a numeric price appears before the final Investment section")
    if paid_media:
        maths = sections.get(_canonical_heading("The Maths"), "")
        for label in ("management fee", "ad spend", "month 1", "months 2–3"):
            if label not in maths.casefold():
                errors.append(f"paid-media maths is missing {label}")
    if errors:
        raise FrameworkValidationError("Strategy contract failed: " + "; ".join(errors))


def validate_pitch_deck(document: str, *, service_lane: str) -> None:
    paid_media = service_lane in PAID_MEDIA_LANES
    expected_count = 14 if paid_media else 13
    matches = list(re.finditer(r"(?m)^##\s+Slide\s+(\d+)\s*[·.):-]\s*(.+?)\s*$", document, re.I))
    errors: list[str] = []
    numbers = [int(match.group(1)) for match in matches]
    if numbers != list(range(1, expected_count + 1)):
        errors.append(f"deck must contain exactly {expected_count} sequential slides")
    slide_contents: list[str] = []
    slide_titles: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        content = document[match.end() : end]
        slide_contents.append(content)
        slide_titles.append(match.group(2))
        for label in (
            "Core message",
            "On-slide copy",
            "Suggested visual",
            "Evidence/source",
            "Speaker notes",
        ):
            if not re.search(rf"\*\*{re.escape(label)}:?\*\*", content, re.I):
                errors.append(f"Slide {index + 1} is missing {label}")
    required_topics = [
        r"cover",
        r"cost|problem|pain",
        r"question",
        r"system|why",
        r"pattern|seen|winner",
        r"week|destination",
        r"day\s*90|before|after|today",
        r"strategy",
        r"DIY|execution",
    ]
    if paid_media:
        required_topics.append(r"inoculation|ads|paid media")
    required_topics.extend(
        [r"vehicle|service", r"why us|why now", r"math|ROI|return", r"investment|price|option"]
    )
    for index, topic in enumerate(required_topics):
        if index >= len(slide_contents):
            break
        searchable = f"{slide_titles[index]}\n{slide_contents[index]}"
        if not re.search(topic, searchable, re.I):
            errors.append(f"Slide {index + 1} does not match its required deck role")
    if len(slide_contents) >= 3:
        on_slide = re.search(
            r"\*\*On-slide copy:?\*\*\s*(.*?)(?=\n\*\*Suggested visual:?\*\*)",
            slide_contents[2],
            re.I | re.S,
        )
        slide_three_copy = on_slide.group(1).strip() if on_slide else ""
        if slide_three_copy.count("?") != 1 or not re.search(r"\d", slide_three_copy):
            errors.append("Slide 3 must contain one number and one unanswered question")
    if len(slide_contents) >= 8 and re.search(
        r"\b(?:MMS|Marketing Mosaic|Management|Retainer|Playbook|Appointment Setting)\b",
        slide_contents[7],
        re.I,
    ):
        errors.append("Slide 8 strategy must not reveal the service")
    _reject_founder_visible_leaks(document, errors)
    if matches:
        all_before_final = document[: matches[-1].start()]
        if re.search(r"(?:£|\$|€)\s*\d|\bprice\s*[:=]\s*\d", all_before_final, re.I):
            errors.append("a numeric price appears before the final slide")
        final = document[matches[-1].start() :]
        for label in ("Option A", "Option B"):
            if len(re.findall(rf"\b{label}\b", final, re.I)) != 1:
                errors.append(f"final slide must contain {label} exactly once")
        if re.search(r"\bOption C\b", final, re.I):
            errors.append("final slide must contain exactly two options")
    if errors:
        raise FrameworkValidationError("Pitch deck contract failed: " + "; ".join(errors))


def validate_linkedin_post(document: str) -> None:
    headings, sections = _h2_sections(document)
    errors: list[str] = []
    _require_in_order(
        headings, ("Draft Post", "Public Claim Ledger", "Approval Checklist"), errors
    )
    _reject_founder_visible_leaks(document, errors)
    post = sections.get(_canonical_heading("Draft Post"), "")
    modes = re.findall(
        r"<!--\s*linkedin_mode:\s*(founder_story|contrarian_growth_engine|memorable_customer_story)\s*-->",
        post,
        flags=re.I,
    )
    if len(modes) != 1:
        errors.append("Draft Post must select exactly one approved LinkedIn mode")
    visible_post = re.sub(r"<!--.*?-->", "", post, flags=re.S)
    if re.search(r"(?m)^###\s+|^\s*\|.+\|\s*$", visible_post):
        errors.append("LinkedIn post must use short prose, not internal headings or tables")
    words = re.findall(r"\b[\w’'-]+\b", visible_post)
    if not 130 <= len(words) <= 350:
        errors.append(f"LinkedIn post has {len(words)} words; expected 130–350")
    hashtag_count = len(re.findall(r"(?<!\w)#\w+", visible_post))
    if not 3 <= hashtag_count <= 5:
        errors.append(f"LinkedIn post has {hashtag_count} hashtags; expected 3–5")
    if "full growth autopsy in the comments" not in visible_post.casefold():
        errors.append("LinkedIn CTA must point to the full Growth Autopsy")
    if re.search(r"\b(?:book a call|DM me|hire us|our services?)\b", visible_post, re.I):
        errors.append("LinkedIn share copy must not contain a direct sales pitch")
    claim_ledger = sections.get(_canonical_heading("Public Claim Ledger"), "")
    for pattern, label in (
        (r"approved report|section\s+\d+", "approved report source"),
        (r"Founder Fact|Observation|MMS Interpretation", "evidence type"),
        (r"public-safe|needs confirmation|exclude", "safety decision"),
    ):
        if not re.search(pattern, claim_ledger, re.I):
            errors.append(f"Public Claim Ledger is missing an {label}")
    checklist = sections.get(_canonical_heading("Approval Checklist"), "")
    if "founder approval" not in checklist.casefold() or not re.search(
        r"not (?:been )?published|has not been published", checklist, re.I
    ):
        errors.append("Approval Checklist must state that founder approval is pending")
    if errors:
        raise FrameworkValidationError("LinkedIn post contract failed: " + "; ".join(errors))


def validate_postcall_deliverable(
    kind: str,
    document: str,
    *,
    brand: str,
    has_external_research: bool,
    service_lane: str,
) -> None:
    if kind == "growth_autopsy":
        validate_growth_report(
            document, brand=brand, has_external_research=has_external_research
        )
    elif kind == "strategy_doc":
        validate_strategy_doc(document, service_lane=service_lane)
    elif kind == "pitch_deck_brief":
        validate_pitch_deck(document, service_lane=service_lane)
    elif kind == "linkedin_post":
        validate_linkedin_post(document)
    else:
        raise FrameworkValidationError(f"Unsupported post-call deliverable kind: {kind}")


DELIVERABLE_CONTRACTS = {
    "growth_autopsy": GROWTH_REPORT_CONTRACT,
    "strategy_doc": STRATEGY_CONTRACT,
    "pitch_deck_brief": PITCH_DECK_CONTRACT,
    "linkedin_post": LINKEDIN_POST_CONTRACT,
}
