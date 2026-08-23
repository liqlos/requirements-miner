# Aggregation rules

Implemented deterministically in `scripts/rm_state.py aggregate`. The
orchestrator never aggregates by vibes; it reads the script's output.

## Fact vs preference is decided at question time

`add-question --kind fact|preference` sets the epistemic contract:

- **fact** — decidable from project code, knowledge base, or documentation.
  Panels vote, but evidence outranks votes (below).
- **preference** — a judgement call between defensible options. Decided by
  weighted majority; dissent preserved.

## Answer grouping

Answers are grouped by normalized text (lowercased, whitespace-collapsed), so
experts must phrase answers option-style (EXPERT_CORE output contract: ≤8
words). Nuance belongs in `rationale`; it never changes the vote. Aggregation
proceeds once ≥2 verdicts are recorded for a question.

## Evidence over majority (facts)

A fact-answer wins on evidence when the top group has at least one panel
citing concrete `evidence[].source` entries AND either ≥50% of all panels
cite sources or ≥2 distinct sources are cited overall.

If two or more answer groups cite real evidence → contradiction: the question
goes to `needs_review` with dissent already attached, and if still unsettled
ships as an open contradiction in the brief. A "fact" resolved without any
evidential support is downgraded to an *assumption* in the final brief.
Peer review preserves epistemics: if the critic's verdict echoes an
answer that panels backed with citations, `basis=evidence` carries over and
the item stays a fact; a verdict matching nothing cited stays
`basis=peer-review` and ships as an assumption — adjudication never turns
uncited claims into facts.

## Weighted majority (preferences)

Groups are compared by (member count, summed confidence). Winner needs share
> 0.6 of the panel; a share ≤ 0.6 (e.g. 3–2 at PANEL_SIZE=5) or unanimous
confidence < 0.5 sends the question to `needs_review`. Decision confidence =
mean confidence of winning group × (0.6 + 0.4 × share).

## Dissent preservation

Every expert not in the winning group is recorded verbatim in
`decision.dissent[]` (`lens`, `position`, strongest counterargument) — also on
the conflict path — and flows into the brief's "Preserved dissent" section
and `results.dissent_log`. A 4–1 split is reported as 4–1 with the dissenting
argument intact — never laundered into unanimity.

## Peer review triggers (the ONLY ones)

Exactly one extra read-only critic pass runs when:

1. top answer share ≤ 0.6 (no clear majority),
2. all confidences < 0.5 (nobody believes their own answer),
3. two or more groups cite conflicting evidence on a fact question.

No reviewers on cleanly-aggregated questions, no standing reviewer agents,
no review-of-review. If the critic pass cannot settle it, the item ships as
an explicit open contradiction instead of being forced.
