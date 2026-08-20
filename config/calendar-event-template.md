# Growth Autopsy Calendar template

Use this format for every founder discovery call.

## Event title

```text
Any client-safe meeting title
```

The title can remain natural because the automation is selected privately from
the description marker below. The legacy `[GROWTH AUTOPSY]` title prefix remains
supported but is no longer required.

## Event description

```text
Automation: growth_autopsy
Company Name: Company Name
Company Website: https://example.com
Founder Name: Founder Name
Founder LinkedIn: https://www.linkedin.com/in/example
Industry: Category or industry
Strategy Mode: auto
```

`Automation: growth_autopsy`, **Company Name**, and **Company Website** are the
core description contract. The system cannot run pre-call research without the
company and website.

**Founder Email** and **Founder LinkedIn** are optional. When supplied, they are
used only as additional founder context for the private analysis. Founder email
can also be inferred from an external Calendar attendee, and founder name can be
inferred from the event title.

**Meeting Agenda is optional.** If it is omitted because the founder can see the
invite, the report derives a neutral call plan and discovery questions from the
supplied company, founder and industry context plus public evidence. It does not
present those research hypotheses as founder priorities.

The parser also accepts the shorter legacy names `Company`, `Website`, and
`Agenda`, so existing Calendar events continue to work.
