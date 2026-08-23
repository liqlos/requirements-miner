---
name: requirements-miner
description: Mine requirements for a project, idea, or topic through independent expert subagent panels — builds a dependency-aware question tree Grill-Me-style, runs parallel clean-context expert panels per question, aggregates verdicts with evidence-over-majority and preserved dissent, and emits a compact requirements brief plus machine-readable results. Use when the user asks to mine/extract/gather requirements, run an expert panel analysis, or stress-test an idea into implementable requirements. Not for ordinary Q&A about a codebase.
---

# requirements-miner

Turn a project/topic + goal into settled requirements using independent
expert panels. You are the orchestrator; deterministic bookkeeping lives in
`scripts/rm_state.py` (never hand-edit state JSON).

## Parameters

Accept as user arguments or ask once, compactly:

| Param | Default | Meaning |
|-------|---------|---------|
| `PROJECT_PATH` | cwd | target project/dir to analyze (never modified; outputs must live outside it) |
| `GOAL` / `FOCUS` | required | what the requirements are for |
| `KNOWLEDGE_BASE` | none | optional path / URL / skill / MCP / wiki (read-only) — see `references/knowledge-base.md` |
| `QUESTION_TARGET` | 100 | cap on aggregated questions; stop early if frontier exhausts or returns diminish |
| `PANEL_SIZE` | 5 | independent experts per question, 1..5 (one per lens: user-product, feasibility, critic, domain, scope) |
| `MODEL_POLICY` | `inherit` | `inherit` = current harness model; never switch to a paid model silently |
| `OUTPUT_PATH` | `./requirements-brief.md` | brief path outside PROJECT_PATH; `<base>.results.json` and `<base>.state.json` sit beside it |
| `RESUME` | off | resume interrupted run from state file |
| Time/panel budget | none | e.g. "30 min", "max 20 expert runs" (`--time-budget-minutes`, `--max-panels`; both count individual expert verdicts) |

Small project ⇒ small tree. QUESTION_TARGET is a ceiling, not a quota.

## Workflow

1. **Read** `references/protocol.md` (round loop, task package, stop rules)
   and `references/harnesses.md` (how your harness launches parallel
   clean-context experts; use installed adapter if present).
2. **Init:** fingerprint the project read-only, then
   `scripts/rm_state.py init --state-file <out>.state.json --project-path ... --goal ... [--knowledge-base ...] [--question-target ...] [--panel-size ...] [--model-policy ...] [--time-budget-minutes ...] [--max-panels ...] [--output-path ...]`
3. **Seed** the dependency-aware question tree with `add-question`:
   categories are exactly `product user constraint behavior risk
   contradiction edge-case success-criterion assumption`; each question is
   `--kind fact` (evidence-decidable) or `--kind preference` (judgement call).
   Dedupe rejects paraphrases; respect it.
4. **Rounds** (protocol §Round loop): claim → fan out PANEL_SIZE clean-context
   experts in ONE parallel batch per question → `record-verdict` each reply →
   `aggregate` (rules: `references/aggregation.md`) → peer-review ONLY flagged
   items → add genuinely new dependent questions → `round-done`.
5. **Finish** (protocol §Finishing): `verify-project --save` must pass, then
   decide first increment, then
   `finish --harness <name> --model-observed <from receipts> --first-increment '{"description":...,"rationale":...,"excluded_until_later":[...]}'`
   (flag is required).

## Deliverables

One compact brief (`OUTPUT_PATH`) + one JSON (`*.results.json`): accepted
requirements separated into facts / preferences / assumptions, open
contradictions, preserved dissent, success criteria, recommended first
increment, provenance (harness, models observed from receipts, counters).

## Hard rules

- The analyzed project is read-only end-to-end; verify before shipping.
- Experts never see each other's answers or parent-session history.
- Evidence beats votes on facts; votes decide preferences; dissent survives.
- Peer review only on flagged disagreement/weak-evidence/expensive forks.
- No mandatory per-step reports; batch panels; no equivalent rephrased
  questions; no reviewer bureaucracy beyond the single critic pass.
- Resume never re-runs completed panels.

Validation: `scripts/rm_validate.py [--state F] [--results F]`.
