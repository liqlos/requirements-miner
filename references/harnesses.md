# Harness adapters — how panels actually run

The skill core is harness-agnostic. Panels run on whatever native parallel
subagent facility the current harness has. Preference order per harness:
(1) installed `rm-expert` adapter (structural read-only enforcement),
(2) built-in read-only subagent type,
(3) generic subagent + prompt-level prohibition (weakest — always verify the
project fingerprint after the run).

After any run: `rm_state.py verify-project --save` proves byte-for-byte
non-mutation regardless of mechanism.

## OpenCode

- Skill discovery: scans `~/.agents/skills` natively. No symlink needed.
- Expert adapter: `~/.config/opencode/agents/rm-expert.md`
  (`permission: edit deny, bash deny`).
- Launch: Task tool with `subagent_type="rm-expert"`, one call per expert in a
  single message → parallel clean-context sessions.
- Fallback: built-in `explore` subagent (read-only) with the same task package.

## Claude Code

- Skill discovery: does NOT scan `~/.agents/skills`; needs
  `~/.claude/skills/requirements-miner` symlink (`scripts/install_adapters.sh`).
- Expert adapter: `~/.claude/agents/rm-expert.md`
  (`tools: Read, Glob, Grep` — whitelist ⇒ no write path, no network).
- Launch: Task tool, `subagent_type="rm-expert"`; batch calls in one message.
- Fallback: built-in `Explore` agent type; prompt-level no-write rule.

## Codex

- Skill discovery: scans `$HOME/.agents/skills` natively (USER scope).
  No symlink needed (would double-register).
- Expert adapter: `$CODEX_HOME/agents/rm_expert.toml`
  (`sandbox_mode = "read-only"`). Multi-agent must be enabled
  (`codex features list` shows `multi_agent`; stable and on by default in
  current releases) — verify before relying on fan-out; per-role sandbox
  enforcement is not separately verified, so still run
  `verify-project --save` after every Codex run.
- Launch: ask for parallel delegation explicitly ("spawn N rm_expert agents,
  one per question, wait for all") — spawn_agent fans out; each child is a
  fresh context. Pass the neutral task package verbatim as each task message.
- Fallback: built-in `explorer` role (read-only).

## pi

- Skill discovery: scans `~/.agents/skills` natively (global scope).
- Expert adapter: requires the `pi-subagents` extension
  (`pi install npm:pi-subagents`); custom agent installed at
  `~/.pi/agent/agents/rm-expert.md` with `tools:` read-only allowlist,
  `inheritProjectContext: false`, `inheritSkills: false`, `defaultContext: fresh`.
- Launch: `subagent({action:"run", agent:"rm-expert", ...})` in parallel mode
  or plain-language "run rm-expert in parallel for these questions".
- Fallback without pi-subagents: headless child processes —
  `pi -p --no-session --tools read,grep,find,ls "<task package>"` launched as
  background bash jobs; collect outputs. Clean contexts by construction.

## Model policy

`MODEL_POLICY=inherit` (default): experts use whatever model the harness
session uses — never switch models silently. `MODEL_POLICY=cheap`: use the
harness's cheapest capable model ONLY where the user configured one (record
which). Record the models actually observed from session receipts/logs in
`finish --model-observed`; config files are not proof of what ran.

## What this skill deliberately does NOT do

- No global hooks installed into user configs (hook snippets would be
  invasive; fingerprint verification covers the safety need).
- No cookies/tokens/secrets copied anywhere.
- No permission-model bypasses: if a harness denies a tool, that's final;
  the adapter falls back instead of escalating.
