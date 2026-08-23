# Knowledge base adapters

`KNOWLEDGE_BASE` is optional and read-only. Supported shapes and how to hand
them to panel experts:

| Shape | Detection | What experts do |
|-------|-----------|-----------------|
| Local directory or file path | existing filesystem path | read directly (read-only tools suffice) |
| URL / online docs | `http(s)://` | `WebFetch`/web tools where the harness grants them; otherwise orchestrator pre-fetches key pages into a temp dir under OUTPUT dir and passes that path |
| Installed Agent Skill by name | matches a skill visible to the current harness (`~/.agents/skills/<name>`) | pass its absolute path as a local directory |
| MCP server | MCP configured in the harness | experts use MCP read tools only if the adapter's tool allowlist permits; otherwise orchestrator queries MCP itself and writes an extract file into the run's output dir for experts to read |
| LLM Wiki / other corpus | anything else readable | reduce to files or extracts first |

Rules:

- Never point experts at sources that can be written to.
- Extracts live next to the run state (`<output>.kb-extract-*.md`), never
  inside PROJECT_PATH.
- Record what the KB actually was in the brief's provenance line.
- If KNOWLEDGE_BASE is unreachable, say so in the brief and continue without
  it — do not silently pretend it was consulted.
