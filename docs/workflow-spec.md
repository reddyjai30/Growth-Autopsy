# Growth Autopsy workflow specification

This document is the durable product source of truth reconstructed from the
August 5 discovery call and the supplied workflow diagrams.

## End-to-end flow

```text
Founder books discovery call
  → Google Calendar event-description parsing
  → T-60 public evidence collection
  → T-30 Founder Intelligence brief available to Diksha
  → discovery call
  → verified Fathom transcript
  → Founder Intelligence analysis
  → Growth Autopsy draft
  → optional 90-day strategy + Gamma-ready deck brief (only when requested)
  → Diksha approval
  → private Notion package
  → Diksha edits scope, services, commercials and pricing
  → founder factual/consent approval for public material
  → public Notion/LinkedIn/founder email
  → combine two approved founder insights into weekly newsletter
```

Nothing public is sent or published without the applicable approval gate.

## Booking and record resolution

- Google Calendar is the scheduling trigger and source of call time.
- A dedicated title prefix or `Automation: GROWTH_AUTOPSY` marker selects calls.
- The Calendar description supplies required `Company Name` and `Company Website`
  fields plus optional founder email, founder LinkedIn and a multi-line meeting
  agenda. The attendee list can supply founder email, while the title suffix can
  supply the founder name. Legacy labels remain accepted.
- A booking without a company and website enters `NEEDS_INPUT` and
  cannot start research.
- Reschedules update the research start/deadline; cancellations remove the job.

## Pre-call Founder Intelligence (priority slice)

Research starts 60 minutes before the call so the synthesized brief can be
ready 30 minutes before the call. The dashboard tracks the T-30 delivery SLA.

### Website and conversion review

Check positioning, offer, public pricing, likely ICP, messaging, homepage
structure, CTA, landing/product pages, trust signals, testimonials, customer
stories, lead magnets, checkout/cart signals, mobile readiness, performance,
technical issues and conversion hypotheses.

### Traffic and SEO

- Traffic: monthly visits, trend, channel mix, geography, engagement and traffic
  competitors only when licensed Similarweb/analytics evidence is available.
- SEO: on-page hygiene and public search are available in the free collector.
  Branded/non-branded clicks, full rankings, commercial keyword coverage,
  backlinks and Search Console facts require the relevant licensed/private data.
- Missing licensed/private data is `unavailable`, never zero.

### Advertising

- Meta: official Meta Ad Library evidence is required before calling an ad active.
- Google: Ads Transparency Center/public search can inform visibility; spend,
  CPA, ROAS, CTR, CPC, conversion rate, campaign structure, search terms and
  impression share require account access.
- TikTok: Creative Center and public profiles can inform creative direction;
  public evidence does not provide private performance.
- A detected Meta/Google/TikTok pixel is a technology observation, not proof of
  active ads, retargeting, performance or creative fatigue.

### Other channels and technology

Boundedly check Instagram, Facebook, LinkedIn, YouTube, TikTok, Pinterest,
Reddit, X, Amazon, marketplaces, newsletter/email capture, podcasts, affiliates
and influencer/creator programs. Record company-linked profiles separately from
search candidates. `Not observed in this bounded check` never means inactive.

Detect useful technology signals such as commerce/CMS, analytics, ad pixels,
email platforms, checkout and behavior analytics. Detection does not prove the
implementation works.

### Required T-30 report

The validated report uses this exact business-document sequence:

1. Founder & Company Background
2. Executive Marketing Brief
3. Website & Conversion Review
4. SEO & Search Visibility
5. Traffic & Channel Intelligence
6. Paid Media & Creative Signals
7. Social, Email & Technology
8. Competitor Landscape
9. Exactly 10 evidence-backed positives
10. Exactly 10 non-duplicative, testable growth gaps
11. Exactly 5 discovery questions
12. Recommended Call Agenda
13. Data Boundaries & Access Needed
14. Deduplicated Sources

The SEO section separates technical SEO, on-page/content observations, organic
visibility and Semrush estimates. The rendered HTML/PDF uses readable tables,
descriptive links and visual callouts; it does not expose a raw evidence dump.

## Post-call analysis

The Fathom webhook must be signature-verified and replay-protected. Store the
complete speaker-attributed transcript with timestamps, then match it to the
Calendar appointment using call time, title and external attendee email.

Founder Intelligence extracts business model, ICP, offer, pricing statements,
goals, problems, constraints, objections, opportunities, metrics, commitments
and permissions. It separates founder statements from Diksha's suggestions.

Strategy intent is semantic, not keyword-count based:

- `strategy_requested`: the founder asks for recommendations, help, services,
  pricing, a plan or next steps.
- `case_study_only`: the call remains editorial.
- `unsure`: Diksha must decide.

## Content and approval gates

- Growth Autopsy: Problem → Diagnosis → Evidence → Opportunities → Strategy
  hypothesis → Expected impact/measurement plan.
- Do not describe a diagnostic as a results case study unless implemented
  outcomes exist.
- Strategy: prioritized 30/60/90-day plan, channels, quick wins, experiments,
  KPIs, risks, access requirements and decision rules.
- The draft deck brief uses explicit placeholders for service scope, commercial
  terms, and pricing. Diksha supplies those inputs before a final Gamma deck is
  exported or sent.
- Diksha approval and founder public-consent approval are separate gates.
- Rejection creates a revision loop; it never silently publishes.

## Weekly newsletter

After two founder packages are approved, create one newsletter containing the
first founder insight, second founder insight, pattern connecting them, Marketing
Mosaics point of view and CTA. Future YouTube derivatives remain a later slice.

## Implementation status

| Capability | Status |
| --- | --- |
| Calendar ingestion, reschedule/cancel handling | Implemented |
| T-60 collection and T-30 SLA tracking | Implemented |
| Playwright website rendering, bounded crawl, SEO, PageSpeed/Lighthouse, technology | Implemented |
| Social/marketplace/ad-transparency discovery evidence | Implemented with public-data caveats |
| Licensed Semrush SEO/traffic data | Optional official API adapter; paid key required |
| Similarweb/Ahrefs datasets | Not configured; never fabricated |
| Fathom verification, transcript capture and matching | Implemented |
| Founder Intelligence handoff | Implemented |
| Growth Autopsy, 90-day strategy and pitch-deck brief | Implemented with semantic routing |
| Diksha approval/revision controls | Implemented for generated artifacts |
| Private approval-gated Notion package | Implemented |
| Final Gamma export, founder email/consent, public Notion, LinkedIn | Pending |
| Two-founder newsletter batching | Pending |
