# AI-Driven Project Cost Controls Tool

A prototype cost review tool for a $500,000,000 data centre project. It ingests the
supplied cost-control datasets, performs the financial analysis in deterministic
Python, uses Gemini to classify and explain what it finds, keeps every finding
traceable to a source row, and holds every finding in **Draft** until a human
reviews it.

The organising principle is a hard line between two kinds of work:

| | Deterministic code | Gemini |
| --- | --- | --- |
| Decides | what is wrong, by how much, how severe | how to describe it, what to do next |
| Sees | the source files | one finished finding at a time |
| Can produce a number | yes | no — enforced by a guardrail, not a prompt |

Remove the AI layer and every figure in the output is unchanged. That is the point.

---

## Setup

Requires Python 3.11 or later.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run the tests

```bash
pytest
```

107 tests, all passing, no network calls. They assert hand-derived values from
the source reports, so a regression in the engine cannot quietly rewrite its own
baseline.

### Run the interface

```bash
streamlit run app.py
```

Opens on a populated review sheet. The sidebar switches the interpretation layer
between `none`, `gemini` and `deterministic`, and adjusts the BR-04 thresholds.

### Run headless

```bash
python -m costctl.cli analyse --data data --out out            # figures only
python -m costctl.cli analyse --ai gemini                      # with interpretation
```

Writes `out/findings.json`, `out/executive_summary.md` and appends to
`out/audit_log.jsonl`.

### Gemini configuration

```bash
export GEMINI_API_KEY="..."          # or GOOGLE_API_KEY
export GEMINI_MODEL="gemini-2.5-flash"   # optional
```

Without a key the tool falls back to a template interpreter that fills the same
schema and is labelled `deterministic-fallback` in the output and the UI. Nothing
pretends to be model output. The test suite never makes a network call.

---

## What the tool found

Reproduced by `pytest` and by `python -m costctl.cli analyse`.

**30 findings: 7 confirmed errors, 23 requiring explanation, 9 Critical.**

### Confirmed errors

| Rule | Finding | Figures |
| --- | --- | --- |
| BR-02 | Cost code 7000 reported EAC does not equal Actual plus FTC | reported $19,250,000, calculated $18,500,000, overstated $750,000 |
| BR-03 | Cost code 7000 VAC, consequential on the above | reported $750,000, calculated $1,500,000 |
| BR-10 | Project reported EAC does not reconcile to the definition | $481,750,000 against $481,000,000; traced entirely to 7000 |
| BR-05 | Cost code 6000 committed beyond authorized budget | commitments $47,000,000 against budget $45,000,000, unfunded $2,000,000 |
| BR-07 | CO-005 duplicated in the change register | overstates exposure by $650,000 if aggregated raw |
| BR-08 | CT-005 approved with no approval reference | $750,000 |
| BR-13 | C-005 coded `GEN-5000`, normalized to 5000 | $67,000,000 contract would otherwise not join |

### Requiring explanation, most material first

- **Cost code 3000, +$13,500,000 forecast movement.** Approved change CO-001
  accounts for $4,500,000; **$9,000,000 is unattributed** against commentary that
  says only "accelerated procurement and cable escalation".
- **Cost code 4000, +$8,000,000 forecast movement** against commentary that reads
  "No material change this period". The movement is exactly pending change CO-002
  ($6,000,000) plus pending contingency draw CT-004 ($2,000,000).
- **$9,000,000 of unapproved change already inside the forecast** — CO-002
  (Pending, $6,000,000) and CO-004 (Potential, $3,000,000) are both marked
  Included. This is the mirror image of BR-06 and the assessment's rules do not
  name it, so it is raised as derived rule DR-01.
- **$7,550,000 of change marked "Not Included"** and therefore additional
  exposure: 5000 $3,500,000, 10000 $2,500,000, 7000 $900,000, 8000 $650,000.
- **Approved changes never reached Current Budget** (DR-02). CO-001 and CO-006
  were approved and drawn from contingency, yet package budgets are unchanged
  month on month. Cost code 3000's VAC reads -$7,500,000 but becomes -$3,000,000
  once the transfer is posted.
- **Contingency balance mixes approved and pending usage.** The register reports
  $11,550,000 remaining; the definition (opening less approved usage) gives
  $13,550,000. The $2,000,000 difference is pending draw CT-004.
- **Cost code 11000 has a negative Forecast to Complete** of -$200,000 (BR-09).
- **Two packages forecast below contract value** (DR-03): 10000 by $1,000,000 and
  11000 by $500,000.

### Executive position

```
Reported VAC                                    $18,250,000
Correction to definitional EAC (BR-02 / BR-03)     +750,000
Calculated VAC                                  $19,000,000
Less excluded change exposure (BR-06)            -7,550,000
Adjusted VAC                                    $11,450,000   2.29% of budget
```

Separately, contingency has **$13,550,000** remaining on an approved basis. After
the $7,550,000 of excluded exposure and the $2,000,000 pending draw, **net
headroom is $4,000,000** — 0.8% of budget, on a project 96% committed with
$34,000,000 of EAC growth in a single month.

**Contingency draws are deliberately not deducted from VAC.** The costs they fund
already sit inside package forecasts, so deducting them again would count the same
money twice. This is the easiest error to make on this dataset and there is a test
named after it.

---

## Assumptions

1. **BR-04 threshold semantics.** "Above $2,000,000 or 3% of budget" is read as
   OR, so the effective trigger is the lower of the two. On small packages the 3%
   limb dominates: cost code 8000 trips at $300,000. Both limbs are configurable
   in the sidebar and in `Thresholds`.
2. **Forecast movement is measured on calculated EAC in both periods**, not
   reported EAC. Measuring on reported figures would let a reporting error mask a
   real movement.
3. **BR-06 exposure** covers change records that are Pending or Potential *and*
   marked "Not Included", after BR-07 deduplication.
4. **Change marked "Included" is assumed to be in the forecast** as stated. The
   tool cannot verify this from the data, so DR-01 raises it for confirmation
   rather than adjusting anything.
5. **Contingency remaining is opening balance less approved usage**, per the
   definitions sheet. Pending draws are disclosed separately.
6. **BR-08 requires an approval reference only when status is Approved**, exactly
   as written, so pending CT-004 is not flagged for a missing reference. It is
   flagged by DR-04 for being untraceable to a cost code.
7. **Duplicate key** is change ID plus cost code plus amount plus status plus
   forecast treatment. Two genuinely different changes sharing an ID would be
   caught as a conflict rather than silently merged.
8. **Severity** is the rule's stated priority, escalated to Critical when the
   materiality basis reaches 1% of project budget ($5,000,000). The basis is
   declared explicitly per rule because it is not always the exposure — a
   $6,000,000 unapproved change inside the forecast adds no exposure but is still
   a critical control issue.
9. **Confidence** is 100% for confirmed arithmetic errors and 80% for items
   requiring explanation. Gemini's proposed confidence is stored alongside, never
   over the top.
10. Single currency, no FX, no time-phasing, no escalation indices.
11. Period labels come from the two supplied reports; the tool compares exactly
    two periods.

## Limitations

- **Review state is in-process.** Status changes are validated and written to the
  append-only audit log, but findings are recomputed from source on restart, so
  they return to Draft. This is a deliberate prototype boundary, not an oversight:
  see next steps.
- **Two periods only.** No trend, no run rate, no S-curve.
- **No authentication.** The reviewer is a free-text field recorded in the audit
  log. There is no segregation between preparer and reviewer.
- **CSV ingestion only.** The supplied data was transcribed to CSV; direct Excel
  ingestion with sheet-level provenance is not implemented.
- **No schedule data**, so no time-cost integration and no assessment of whether
  the $2,500,000 potential schedule extension on 10000 is realistic.
- **The guardrail checks monetary tokens.** It will catch a fabricated dollar
  figure. It will not catch a fabricated qualitative claim, such as an invented
  vendor name. Ungrouped bare integers are treated as non-monetary so that cost
  codes, row numbers, percentages and years pass, which means a model writing
  "18500000" without separators would not be checked.
- **Gemini wording varies run to run** even at temperature 0.2. Figures do not.
- Cost code 12000 carries a $20,000,000 budget with zero EAC, so contingency
  appears as favourable variance. The summary treats it separately, but any
  package-level VAC comparison including 12000 will look misleadingly healthy.

## Next steps

1. **Persist findings and review state** in SQLite keyed on a stable finding
   fingerprint, so a finding accepted this month reappears next month carrying its
   review history and a reviewer sees "unchanged since last period" rather than a
   fresh Draft.
2. **Period-over-period diffing** of findings, so the register shows new,
   resolved and persisting items.
3. **Movement decomposition by driver** — approved change, unapproved change,
   contingency draw, quantity, rate, escalation — rather than one unattributed
   residual.
4. **Direct Excel ingestion** with sheet and cell provenance, so `source_reference`
   points at `Current Cost Report!F8`.
5. **Preparer / reviewer segregation** with role-based transitions and a
   materiality-based approval threshold.
6. **Extend the guardrail to qualitative claims** by requiring the model to tag
   each sentence with the evidence key it draws on, and rejecting untagged
   assertions.
7. **Rule coverage report** in CI, failing the build if a rule in `ALL_RULES` has
   no corresponding test.

---

## Repository layout

```
cost-controls-tool/
├── README.md                     setup, assumptions, limitations, next steps
├── ARCHITECTURE.md               component and data-flow diagrams, design rationale
├── requirements.txt
├── pytest.ini
├── app.py                        Streamlit review interface
├── data/                         source datasets as CSV
│   ├── cost_code_mapping.csv
│   ├── cost_report_2026_06.csv   prior period
│   ├── cost_report_2026_07.csv   current period
│   ├── commitment_register.csv
│   ├── change_register.csv
│   └── contingency_register.csv
├── src/costctl/
│   ├── money.py                  Decimal parsing and formatting
│   ├── ingest.py                 loading, SHA-256, row-level provenance
│   ├── normalize.py              BR-13 cost-code alias mapping
│   ├── model.py                  CostModel and derived aggregates
│   ├── models.py                 Finding schema, severity, status transitions
│   ├── rules.py                  BR-02..BR-13 and derived DR-01..DR-04
│   ├── engine.py                 orchestration, stable IDs, BR-11/BR-12 validation
│   ├── summary.py                executive summary and variance bridge
│   ├── ai.py                     Gemini interpretation and numeric guardrail
│   ├── review.py                 human review workflow
│   ├── audit.py                  append-only audit log
│   └── cli.py
├── tests/
│   ├── test_money.py                        parser edge cases
│   ├── test_deterministic_calculations.py   golden values per package
│   ├── test_rules.py                        one test per business rule
│   ├── test_traceability_and_review.py      BR-11, BR-12, audit, JSON schema
│   └── test_ai_layer.py                     BR-01 guardrail enforcement
└── out/                          findings.json, executive_summary.md, audit_log.jsonl
```

## Business rule coverage

| Rule | Where implemented | Test |
| --- | --- | --- |
| BR-01 financial logic is deterministic | `ai.py` guardrail, `rules.py` contains all arithmetic | `test_ai_layer.py` |
| BR-02 EAC equals Actual plus FTC | `rules.br02_eac_integrity` | `test_br02_flags_the_single_eac_error` |
| BR-03 VAC equals Budget minus EAC | `rules.br03_vac_integrity` | `test_br03_flags_the_consequential_vac_error` |
| BR-04 movement threshold needs explanation | `rules.br04_forecast_movement` | `test_br04_*` (4 tests) |
| BR-05 commitments above budget | `rules.br05_unfunded_commitment` | `test_br05_flags_the_unfunded_ups_commitment` |
| BR-06 excluded change is exposure | `rules.br06_excluded_change` | `test_br06_reports_excluded_change_as_exposure` |
| BR-07 duplicates before aggregation | `model.build_model`, `rules.br07_duplicate_changes` | `test_br07_identifies_the_duplicate_before_aggregation` |
| BR-08 contingency approval references | `rules.br08_contingency_control` | `test_br08_*` (2 tests) |
| BR-09 negative FTC | `rules.br09_negative_ftc` | `test_br09_flags_negative_forecast_to_complete` |
| BR-10 totals reconcile | `rules.br10_reconciliation` | `test_br10_*` (2 tests) |
| BR-11 source file and reference | `ingest.Row`, `engine.validate` | `test_br11_*` (3 tests) |
| BR-12 findings remain Draft | `models.Finding`, `engine.validate`, `review.py` | `test_br12_*` (3 tests) |
| BR-13 alias normalization | `normalize.CostCodeMap` | `test_br13_*` (2 tests) |
| BR-14 confirmed error vs explanation | `models.FindingType`, every rule | `test_br14_every_finding_is_classified...` |
