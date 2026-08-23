# Panel protocol

How a requirements-miner run mines requirements with independent expert panels.
The orchestrating agent (the session running SKILL.md) executes this; all
bookkeeping goes through `scripts/rm_state.py` — never hand-edit state.

## Lenses

Five canonical lenses — one expert each, so `PANEL_SIZE` is 1..5.

| Lens | Question it owns |
|------|------------------|
| `user-product` | Who uses this and what must be true for the product to work for them |
| `feasibility` | Can this be built as described; hidden engineering constraints |
| `critic` | Which assumptions would falsify this answer; failure modes |
| `domain` | Craft/domain-specific correctness a generalist misses |
| `scope` | Value, cut-lines, sequencing, what NOT to build |

With PANEL_SIZE < 5 use the first N lenses unless a question clearly favors others.

## Round loop

1. **Frontier.** `rm_state.py frontier --limit <B>` returns open questions
   whose dependencies are aggregated or parked (`B` = batch cap, default 10;
   grilling would ask the whole frontier — the cap exists only to keep one
   round's fan-out sane). Empty frontier → run is done.
2. **Claim & fan out.** For each selected question, one at a time:
   `claim`, then launch its `PANEL_SIZE` experts **in one parallel batch**
   using the harness mechanism (see `harnesses.md`). Each gets only
   the neutral task package — never other experts' answers, never parent
   session history. The next question's batch starts after this one is recorded.
3. **Collect.** Pipe each expert's reply into
   `record-verdict --question-id Qxxx --verdict -`. The parser accepts raw
   replies containing one fenced JSON block; on a parse failure re-run that
   expert once with a reminder of the output contract. Aggregation proceeds
   once at least 2 verdicts are recorded.
4. **Aggregate.** `aggregate` applies deterministic rules
   (rules and formulas: `references/aggregation.md`). Outcomes:
   `aggregated` or `needs_review`.
5. **Peer review — only when flagged.** For each `needs_review` question run
   ONE additional read-only critic pass: a fresh subagent seeing the question,
   both positions, and all evidence. It must end with exactly one fenced JSON
   block:
   ```json
   {"verdict": "<winning resolution>", "settling_evidence": ["<source refs>"],
    "confidence": 0.0}
   ```
   Feed it to `resolve-review --resolution <verdict> --confidence <n>`.
   If the critic cannot settle it, either resolve it yourself from the cited
   evidence or `park` the question — parked items ship as open contradictions
   and unblock their dependents as "decided by parking".
6. **Grow the tree.** Answers may unblock genuinely NEW dependent questions.
   Add them with `add-question` (dedupe rejects paraphrases). Count how many
   you added, then close the round: `round-done --new-children <n>`.
7. Repeat until stop rule fires.

## Neutral task package per expert

```
Lens:            <lens name> — <one-line lens description from the table>
Question Qxxx:   <title> — <body>
Category/Kind:   <category> / fact|preference
Project (READ-ONLY): <absolute path to PROJECT_PATH>
Knowledge base:  <path | URL | "none">
Goal:            <GOAL/FOCUS text>

Answer ONLY this question under your lens. Read the project slice you need.
End with exactly one JSON block matching adapters/EXPERT_CORE.md.
```

## Stop rules (evaluated by `round-done`)

- `frontier_exhausted` — nothing left to ask that isn't already asked.
- `target_reached` — QUESTION_TARGET questions aggregated. Do not pad: if the
  frontier dies at 12 questions on a small project, 12 correct answers beat
  100 padded ones.
- `budget_time` / `budget_panels` — explicit budget hit.
- `diminishing_return` — 3 consecutive rounds added zero new dependent
  questions.

## Finishing

1. `verify-project --save` — compares a sha256 manifest of the project
   against run start (excludes VCS/metadata/build dirs and binary-suffix
   files; unreadable or symlinked files are skipped and counted in the
   digest). If it reports CHANGED, STOP and tell the user; do not ship
   results.
2. Decide the first implementable increment from accepted requirements.
3. `finish --harness <name> --model-observed <model-from-receipts>
   --first-increment '{"description":...,"rationale":...,"excluded_until_later":[...]}'`
   renders `<base>.md` + `<base>.results.json` beside the state file
   (`<base>` = OUTPUT_PATH minus extension). Outputs must live outside
   PROJECT_PATH — `init` enforces this.
4. Report to user: brief location, headline requirements, contradictions,
   low-confidence preferences worth a human sign-off, suggested increment.
   Nothing else.

## Resume

State lives in `<base>.state.json` next to the outputs. To resume after an
interruption: `status` shows completed panels, in-flight questions
(`queued`) and pending ones. Never re-run panels for verdicts already in
state; continue from `frontier`. If OUTPUT_PATH/state file is missing, start
a fresh run. A stopped run is final: once `stop_reason` is set, mutating
commands refuse to run — start a new run instead of extending an old one.
