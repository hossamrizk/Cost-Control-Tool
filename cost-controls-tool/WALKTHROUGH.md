# How the solution works

## The organising principle

There's one idea the whole project is built around: **a hard line between arithmetic and language**. The engine decides *what* is wrong and *by how much*. The LLM decides *how to describe it* and *what to do next*. Delete the AI layer entirely and every number in the output stays identical. That's what makes BR-01 ("financial calculations must be deterministic, not by the LLM") an enforced property rather than a promise.

## Pipeline (data flow, top to bottom)

```
CSV files ──► ingest ──► normalize ──► model ──► rules ──► engine ──► summary ──► outputs
                                                              │
                                                              └──► ai (optional, adds words only)
```

Every stage is deterministic except the last. If you run the pipeline twice with the same inputs, you get byte-identical numbers.

## Module-by-module

### `data/` — six CSVs
The two monthly cost reports (June, July 2026), the commitment register, change register, contingency register, and the cost-code mapping table. These are the source of truth. Nothing else is authoritative.

### `ingest.py` — load + fingerprint
Reads each CSV, attaches a **row-level source reference** to every record (e.g. `change_register.csv row 6`), and computes a **SHA-256** of each file. That hash goes into the run's provenance block, so a later reviewer can prove *which version* of the source data produced a given finding. This is what makes BR-11 ("each AI finding must include source file and source reference") structural: a `Row` object physically carries its source, so a downstream `Finding` can't be built without one.

### `normalize.py` — BR-13 alias mapping
The mapping table has one alias — `GEN-5000` → `5000`. Without this normalization the $67M turbine contract wouldn't join to cost code 5000 and would silently drop out of reconciliation. Normalizations are *recorded*, not silent, so the BR-13 rule can emit a finding pointing at the row that used the alias.

### `model.py` — the `CostModel`
Everything the rules need, in one clean shape:
- `PackageLine` for each cost code (budget, commitments, actual, FTC, reported EAC/VAC, commentary, source ref)
- `PackageLine.calculated_eac` = actual + FTC (BR-02)
- `PackageLine.calculated_vac` = budget − calculated EAC (BR-03)
- `ChangeRecord`, `Commitment`, `ContingencyTxn` — one row each with source refs
- **BR-07 deduplication happens here, before aggregation.** `dedup_key = (change_id, cost_code, amount, status, treatment)`. The duplicate `CO-005` is separated into `duplicate_changes` and never counted in totals. If it were aggregated raw it would overstate exposure by $650,000.
- Helper aggregates: `approved_included_changes()`, `unapproved_in_forecast()`, `excluded_exposure()`, contingency roll-ups.

Every dollar is `Decimal`, not `float`. Floats would let `18500000.000000002` happen; `Decimal` gives exact equality so `BR-02` can compare cleanly and tests can assert exact values.

### `rules.py` — the deterministic rule engine
This is the assessment's business-rule table turned into code. Each rule is a plain function `(CostModel, Thresholds) → list[Finding]`.

**Fourteen rules total:**
- **BR-02..BR-10, BR-13** from the assessment
- **DR-01..DR-04** are *derived* rules — problems the stated rules imply but don't name:
  - `DR-01` — the mirror of BR-06: change marked "Included" but not Approved. Inflates the forecast with unauthorized scope.
  - `DR-02` — approved change drawn from contingency but Current Budget never updated → forecast carries the cost, contingency carries the funding, VAC looks worse than it is.
  - `DR-03` — calculated EAC below executed commitments (implies unbacked underrun).
  - `DR-04` — contingency draw with no traceable cost code.

Each rule produces a `Finding` with the reported value, calculated value, difference, potential exposure, evidence dict, and recommended review/action. `severity_basis` is stated *per rule* because materiality isn't always exposure — a $6M unapproved change already inside the forecast adds no additional exposure but is still critical control-wise.

Severity is: rule's stated priority, escalated to Critical when materiality basis reaches 1% of project budget ($5M).

### `models.py` — the `Finding` schema
The 15 fields from the assessment's "Required Findings Output Structure" table, plus two extras:
- `finding_type` — `Confirmed error` vs `Requires explanation` (BR-14)
- `ai` — the `AIInterpretation` block kept *structurally separate* so a reviewer can always see which numbers were computed and which words were generated

Enums: `Severity`, `Status`, `FindingType`, `Category`. `VALID_TRANSITIONS` defines the review state machine.

### `engine.py` — orchestrator
```python
tables    = load_dataset(data_dir)       # ingest
model     = build_model(tables)          # normalize + shape
findings  = _assign_ids(run_all(model))  # rules
validate(findings)                       # BR-11/BR-12 enforcement
summary   = build_summary(model, findings)
# optional
for finding in findings:
    finding.ai = interpreter.interpret(finding)
```

Two things worth calling out:

**Stable IDs** — `_assign_ids()` sorts findings by `(rule_order, cost_code, source_reference)`, then labels `F-001, F-002…`. Same inputs → same IDs every run. That makes `findings.json` diffable across runs.

**Structural validation** — `validate()` raises if any finding has no source file/reference (BR-11) or wasn't born as `Draft` (BR-12). A new rule *cannot* ship a finding without these; the engine refuses to complete.

### `ai.py` — the interpretation layer + the guardrail

This is the clever part. The model receives a `_facts()` payload with the *already-computed* figures, category, source refs, and evidence. It never sees raw source files, and the system prompt forbids arithmetic. It returns JSON with `explanation`, `recommended_review`, `recommended_action`, `proposed_severity`, `proposed_confidence`, `severity_rationale`.

But prompts alone are unverifiable, so there's a **numeric guardrail**:

```python
def check_numeric_guardrail(finding, text):
    # extract every $-token from the model's text
    # allow only figures within 0.5% of an engine-computed value
    # (reported/calculated/difference/exposure/evidence)
    # if anything else appears → block
```

If the model fabricates a dollar figure — say, hallucinates a "$45,000,000" that wasn't in the FACTS — the guardrail catches it, and the AI block is replaced with `"AI explanation withheld: the generated text contained monetary figures that do not trace to a deterministic calculation."` The deterministic description remains visible. This is what makes BR-01 enforceable rather than aspirational.

Two interpreters:
- `GeminiInterpreter` — real API call (`google-genai` SDK, temperature 0.2, JSON response mode)
- `DeterministicInterpreter` — offline template fallback labelled `deterministic-fallback` in the output so it never pretends to be model output. Lets the tests and the demo work with no network.

### `summary.py` — the variance bridge and exec summary
Builds `ExecutiveSummary` **from the model, not from the findings**. This matters: if you summed findings you'd double-count the $750,000 EAC error (once at package level BR-02, once at project level BR-10). The bridge walks:

```
Reported VAC → Calculated VAC (BR-02/03 correction) → Adjusted VAC (less BR-06 exposure)
```

Contingency draws are deliberately **not** deducted from VAC — the costs they fund already sit inside package forecasts. There's a test named after this so it can't regress.

### `review.py` + `audit.py` — human review workflow
`set_status()` validates transitions against `VALID_TRANSITIONS` and writes an entry to `audit_log.jsonl` (append-only, one JSON object per line, includes reviewer name, note, run ID, timestamp). Every analysis run is also logged. This gives you the "who knew what when" trail.

### `app.py` — the Streamlit UI
Five tabs:
1. **Findings register** — filter by severity / classification / status / rule; each finding is a card with severity-coloured stripe, badges, deterministic numbers, AI block (visually separated), source trace, and a review expander to move the status
2. **Variance bridge** — the signature visual walk from reported to adjusted VAC
3. **Packages** — package-level table + a watchlist of packages needing attention
4. **Executive summary** — the markdown summary + download buttons for `executive_summary.md` and `findings.json`
5. **Provenance** — run ID, engine version, ruleset version, prompt version, input file hashes, thresholds, audit log tail

### `cli.py` — headless invocation
`python -m costctl.cli analyse --data data --out out` for CI or scripted use.

## Tests (107 total)

Each business rule has at least one test that asserts **exact figures** against the fixture data — not tolerances, exact `Decimal` equality. So a regression can't quietly rewrite the baseline.

- `test_money.py` — parser edge cases
- `test_deterministic_calculations.py` — hand-derived golden values per package
- `test_rules.py` — one or more tests per BR/DR
- `test_traceability_and_review.py` — BR-11 (source must exist), BR-12 (born Draft), audit log shape, JSON schema
- `test_ai_layer.py` — BR-01 guardrail: feeds fabricated `$` tokens through and asserts they're blocked

No test makes a network call.

## How it maps to the 8 required deliverables

| # | Deliverable | Where |
|---|---|---|
| 1 | Working prototype | `app.py` + `cli.py` |
| 2 | Source-code repository | `src/costctl/` |
| 3 | Simple UI | `app.py` (Streamlit) |
| 4 | Architecture / data-flow diagram | `ARCHITECTURE.md` (two Mermaid diagrams) |
| 5 | Structured JSON findings | `out/findings.json` — provenance + summary + full finding schema |
| 6 | Executive cost summary | `out/executive_summary.md` (also rendered in-app) |
| 7 | Automated tests for critical calcs | `tests/` — 107 tests, exact-value assertions |
| 8 | Setup / assumptions / limitations / next steps | `README.md` |

## Why *not* certain things (this is the part reviewers will ask about)

- **No RAG, no vector DB.** 12 packages, 5 tables, known schema. Retrieval is a dict lookup on cost code. A vector store would add an index to keep in sync and introduce silent retrieval failures — for zero gain.
- **No multi-agent system.** One constrained LLM call per finding is enough. Multiple agents negotiating would introduce nondeterminism into a tool whose value proposition is that the same inputs always produce the same numbers.
- **No LLM function calling over the data.** Letting the model query the tables would let it compute, which BR-01 forbids.
- **No database.** Findings recompute from source every run. That's the correct default for a reconciliation tool — state that can drift from its source is a liability. Persistence is called out as a next step.

## The headline result

Reproduced by `pytest` and by the CLI:

- **30 findings** — 7 confirmed errors, 23 requiring explanation, 9 Critical
- Cost code 7000 reports EAC as $19.25M when Actual + FTC is $18.5M (BR-02) → cascades to VAC (BR-03) and project-level reconciliation (BR-10)
- Cost code 6000 committed $2M above its budget (BR-05)
- CO-005 duplicated in the change register (BR-07)
- CT-005 approved contingency draw with no approval reference (BR-08)
- $9M of unapproved change already inside the forecast (DR-01)
- $7.55M of "Not Included" change as additional exposure (BR-06)
- **Adjusted VAC = $11.45M** (2.29% of budget); **net headroom = $4M** (0.8% of budget) on a project 96% committed
