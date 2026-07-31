# Architecture and data flow

## Component architecture

```mermaid
flowchart TB
    subgraph SRC["Source data (read-only)"]
        A1["cost_report_2026_07.csv<br/>current period"]
        A2["cost_report_2026_06.csv<br/>prior period"]
        A3["commitment_register.csv"]
        A4["change_register.csv"]
        A5["contingency_register.csv"]
        A6["cost_code_mapping.csv"]
    end

    subgraph DET["Deterministic core - no LLM, Decimal arithmetic"]
        B["ingest.py<br/>SHA-256 per file<br/>row-level provenance"]
        C["normalize.py<br/>BR-13 alias mapping<br/>money parsing"]
        D["model.py<br/>CostModel<br/>BR-07 dedup before aggregation"]
        E["rules.py<br/>BR-02 to BR-13 plus derived DR-01 to DR-04"]
        F["engine.py<br/>stable finding IDs<br/>BR-11 / BR-12 validation"]
        G["summary.py<br/>variance bridge<br/>computed from model, not findings"]
    end

    subgraph AI["Interpretation - LLM, words only"]
        H["ai.py<br/>Gemini via google-genai"]
        I["numeric guardrail<br/>rejects any figure the<br/>engine did not compute"]
        N["blocked: deterministic text retained,<br/>AI text discarded"]
    end

    subgraph OUT["Outputs"]
        J["findings.json<br/>schema plus provenance"]
        K["executive_summary.md"]
        L["app.py<br/>Streamlit review sheet"]
        M["audit_log.jsonl<br/>append-only"]
    end

    A1 --> B
    A2 --> B
    A3 --> B
    A4 --> B
    A5 --> B
    A6 --> B
    B --> C --> D --> E --> F
    F --> G
    F -->|"findings with figures already computed"| H --> I
    I -->|passed| L
    I -->|blocked| N --> L
    F --> J
    G --> J
    G --> K
    F --> M
    L --> M

    classDef det fill:#EAF0F8,stroke:#2A5DB0,color:#10203A
    classDef ai fill:#F1F4F9,stroke:#4A5A72,color:#10203A,stroke-dasharray: 4 3
    classDef out fill:#FFFFFF,stroke:#C8D0DC,color:#10203A
    class B,C,D,E,F,G det
    class H,I,N ai
    class J,K,L,M out
```

The dashed boundary is the important one: **every figure crosses it fully formed.**
Delete the AI subgraph entirely and `findings.json` still contains the same
numbers, the same severities and the same source references.

## Data flow for one finding

```mermaid
sequenceDiagram
    autonumber
    participant F as CSV row
    participant N as Normalizer
    participant R as Rule BR-02
    participant V as Validator
    participant G as Gemini
    participant Q as Guardrail
    participant H as Reviewer

    F->>N: cost_code 7000, actual $8,000,000, FTC $10,500,000, reported EAC $19,250,000
    N->>R: Decimal values plus source_file and row 7
    R->>R: calculated EAC = 8,000,000 + 10,500,000 = 18,500,000
    R->>R: 19,250,000 does not equal 18,500,000, difference 750,000
    R->>V: Finding F-001, Confirmed error, Critical, status Draft
    V->>V: BR-11 source present? BR-12 status Draft?
    V->>G: facts only, no raw files, no other packages
    G-->>Q: prose plus proposed severity
    Q->>Q: does every monetary token trace to an engine-computed value?
    Q->>H: pass shows AI text, fail withholds it and keeps deterministic text
    H->>H: Draft to Reviewed to Accepted, logged with reviewer and note
```

## Why each component exists

| Component | Why it is required | Why not something larger |
| --- | --- | --- |
| `Decimal` arithmetic | Financial equality must be exact for BR-02 and BR-03, and for tests to assert values rather than tolerances | Floats make `18500000.000000002` possible, which is indefensible in a cost report |
| Row-level provenance in `ingest` | BR-11 becomes structural: a finding cannot exist without a source | Asking the LLM to cite sources produces citations that look right and cannot be checked |
| `normalize` with an explicit mapping table | BR-13. `GEN-5000` must join to `5000` or the $67,000,000 turbine contract silently drops out of reconciliation | Fuzzy matching or embedding similarity would guess, and a wrong guess is invisible |
| Dedup inside `model`, before aggregation | BR-07. Exposure is $7,550,000 deduplicated and $8,200,000 raw | — |
| `rules` as plain functions | Each rule is independently testable and readable next to the business-rule table | A rules DSL adds indirection with no new capability at this size |
| `engine` validation step | BR-11 and BR-12 are enforced in code, so a new rule cannot ship a finding without a source or one that starts life Accepted | — |
| `summary` computed from the model | Prevents a finding being double counted into the headline number | Summing findings would count the $750,000 EAC error twice, once at package level and once at project level |
| `ai` layer | Classification, explanation and recommendation are genuine language tasks. Judging whether "No material change this period" is consistent with an $8,000,000 movement is not arithmetic | — |
| Numeric guardrail | Makes BR-01 enforceable rather than aspirational | Prompt instructions alone are unverifiable |
| `audit_log.jsonl` | Append-only history of runs and status changes with input hashes | A mutable table loses the question of what was known when |

## Components deliberately not built

**No vector database and no RAG.** The dataset is twelve packages across five
tables with a known, fixed schema. Retrieval is a dictionary lookup on cost code.
Traceability requires an exact row reference such as `change_register.csv row 6`,
and embedding similarity degrades exactly that property. A vector store would add
an index to keep in sync, a chunking strategy to tune and a new class of silent
retrieval failure, in exchange for nothing.

**No multi-agent system.** There is no task decomposition here that one
constrained call per finding does not handle. Multiple agents negotiating over
financial findings would introduce nondeterminism into a tool whose entire value
proposition is that the same inputs always produce the same numbers.

**No LLM function calling over the data.** Letting the model query the tables
would let it compute, which BR-01 forbids. The model receives a finished finding.

**No ORM or database in the prototype.** Findings are recomputed from source on
every run, which is the correct default for a reconciliation tool: state that can
drift from its source is a liability. Persistence is a next step, and the shape it
should take is described in the README.

## Extending the ruleset

A new rule is a function with the signature `(CostModel, Thresholds) -> list[Finding]`,
registered in `ALL_RULES`. Because `engine.validate()` runs after every rule, a new
rule physically cannot emit a finding without a source reference or with a status
other than Draft. The convention is that each new rule arrives with a test that
asserts its exact expected figures against the fixture data.
