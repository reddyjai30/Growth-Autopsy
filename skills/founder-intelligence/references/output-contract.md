# Founder Intelligence output contract

Use this order:

1. Meeting metadata
2. Executive summary
3. Business and founder evidence
4. Founder story evidence
5. The one problem commercial brief — exact founder quote, speaker, timestamp,
   symptom, consequences, desired outcome and unknowns
6. Goals, constraints and objections
7. Metrics and calculation inputs — founder numbers only; missing = Not provided
8. Growth Operating System evidence — Traffic, Conversion, Retention, Expansion
9. Six-lens evidence — Psychology, Behaviour, Economics, Attention, Trust, Distribution
10. Opportunities discussed
11. Commitments and next steps
12. Public-safety ledger
13. Evidence ledger
14. Open questions for Diksha
15. Strategy and service-lane classification

For evidence entries use:

```text
Evidence type: Founder Fact | Observation | MMS Interpretation
Speaker/source:
Timestamp:
Statement or concise paraphrase:
Allowed wording:
Confidence: high | medium | low
Sensitivity: public-safe | internal-only | needs-confirmation | exclude
```

Never include a verbatim excerpt longer than needed to prove the point.

The final two lines are required for safe orchestration:

```text
<!-- strategy_intent: strategy_requested -->
<!-- service_lane: lead_intelligence -->
```

Intent may instead be `case_study_only` or `unsure`. Service lane must be one of
`meta_acquisition`, `paid_media_rebuild`, `google_intent_capture`, `paid_scaling`,
`amazon_ads`, `attribution_ads`, `native_meta`, `lead_intelligence`,
`outbound_appointment_setting`, `linkedin_authority`, `sales_playbook`,
`shopify_cro_aeo`, or `unsure`. Put no content after the second marker.
