# requirements-miner

Automated Grill Me for requirements. Instead of interviewing you, it mines
requirements through independent expert subagent panels: builds a
dependency-aware question tree, runs parallel clean-context experts per
question, aggregates verdicts with **evidence over majority** and preserved
dissent, then ships a compact requirements brief ready for implementation.

Works as one shared Agent Skill across four harnesses:

| Harness | Discovery | Invoke |
|---------|-----------|--------|
| OpenCode | native (`~/.agents/skills`) | `use requirements-miner on PROJECT_PATH with GOAL ...` |
| Codex CLI | native | `$requirements-miner ...` |
| pi | native | `/skill:requirements-miner ...` |
| Claude Code | via symlink | `/requirements-miner ...` |

## Install

```bash
git clone git@github.com:liqlos/requirements-miner.git ~/.agents/skills/requirements-miner
~/.agents/skills/requirements-miner/scripts/install_adapters.sh
```

The installer only creates `requirements-miner`-named symlinks (Claude Code
skill + one read-only `rm-expert` subagent per harness) and backs up any
conflicting file first. OpenCode, Codex and pi pick up the skill natively.

## Run

```bash
opencode run "Use requirements-miner: PROJECT_PATH=~/src/myapp \
  GOAL='rewrite auth' QUESTION_TARGET=30 PANEL_SIZE=3 \
  OUTPUT_PATH=./req/auth-brief.md"
```

Parameters: `PROJECT_PATH` · `GOAL`/`FOCUS` · `KNOWLEDGE_BASE` (path / URL /
skill / MCP, read-only) · `QUESTION_TARGET` (=100 ceiling, not quota) ·
`PANEL_SIZE` (1–5, default 5) · `MODEL_POLICY` (`inherit`; never silently
switches to a paid model) · `OUTPUT_PATH` · `RESUME` · time/expert budgets.

## How it works

```
seed question tree → per question: PANEL_SIZE clean-context experts in parallel
→ deterministic aggregation → peer review only on flagged forks → grow frontier
→ stop (frontier exhausted / target / budget / diminishing returns)
→ requirements-brief.md + results.json
```

Experts never see each other's answers. Facts are settled by cited evidence,
preferences by weighted majority, dissent survives verbatim. The analyzed
project is fingerprinted before the run and verified byte-for-byte after;
panel adapters enforce read-only at the harness level (tool whitelists,
permission denies, Codex read-only sandbox).

Outputs: one Markdown brief (facts / preferences / assumptions, open
contradictions, dissent, success criteria, recommended first increment) plus
one JSON with questions, votes, evidence and provenance.

## Development

```bash
bash tests/run_tests.sh                                # 28 black-box tests
python3 scripts/rm_validate.py                         # structural validation
python3 scripts/rm_validate.py --state F --results F   # artifact validation
```

Layout: `SKILL.md` (orchestrator protocol) · `references/` (round loop,
aggregation rules, harness mechanics, KB adapters) · `adapters/` (read-only
expert definitions + shared expert contract) · `schemas/` (state/results
contracts) · `scripts/rm_state.py` (deterministic state machine: dedupe,
aggregation, frontier, resume, budgets) · `fixtures/mini-todo` (smoke target).

MIT license.
