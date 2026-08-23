#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

STATE_VERSION = 1
LENSES = ["user-product", "feasibility", "critic", "domain", "scope"]
CATEGORIES = [
    "product", "user", "constraint", "behavior", "risk",
    "contradiction", "edge-case", "success-criterion", "assumption",
]
STOP_REASONS = [
    "frontier_exhausted", "target_reached", "budget_time",
    "budget_panels", "diminishing_return",
]
DEDUPE_THRESHOLD = 0.62
DIMINISHING_RETURN_ROUNDS = 3
IGNORED_DIR_NAMES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    "venv", ".idea", ".vscode", "dist", "build", ".pytest_cache",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".bz2", ".7z", ".rar", ".dylib", ".so", ".dll",
    ".exe", ".bin", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4",
    ".mov", ".mp3", ".wav", ".db", ".sqlite", ".sqlite3",
}

STOPWORDS = frozenset(
    """a an and are as at be but by can does for from has have how if in into is
    it its must of on or should so that the their then there these this to we
    what when where which who why will with would need needs do""".split()
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    if state.get("version") != STATE_VERSION:
        raise SystemExit(f"unsupported state version in {path}: {state.get('version')}")
    return state


def save_state(path: str, state: dict) -> None:
    state["updated_at"] = now_iso()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)



def project_files(root: str) -> list[str]:
    files = []
    if os.path.isfile(root):
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIR_NAMES)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in BINARY_SUFFIXES:
                continue
            files.append(os.path.join(dirpath, name))
    return files


def fingerprint(root: str) -> dict:
    h = hashlib.sha256()
    count = 0
    skipped = 0
    for path in project_files(root):
        if os.path.islink(path):
            skipped += 1
            continue
        rel = os.path.relpath(path, root) if os.path.isdir(root) else os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            skipped += 1
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
        h.update(b"\0")
        count += 1
    return {"algorithm": "sha256-manifest-v1",
            "digest": f"{h.hexdigest()}:{count}:skipped{skipped}"}


def fingerprint_matches(a: dict | None, b: dict | None) -> bool:
    return bool(a) and bool(b) and a.get("digest") == b.get("digest")



def normalize(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    tokens = re.findall(r"[a-z0-9]{2,}", text)
    return {t for t in tokens if t not in STOPWORDS} or set(tokens)


def similarity(a: str, b: str) -> float:
    ta, tb = normalize(a), normalize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def find_duplicate(state: dict, title: str, body: str) -> tuple[str, float] | None:
    best_id, best_score = None, 0.0
    for qid, q in state["questions"].items():
        score = max(similarity(title, q["title"]), similarity(body, q["body"]) * 0.95)
        if score > best_score:
            best_id, best_score = qid, score
    if best_id and best_score >= DEDUPE_THRESHOLD:
        return best_id, round(best_score, 3)
    return None



def cmd_init(args) -> None:
    project_abs = os.path.abspath(args.project_path)
    if not os.path.exists(project_abs):
        raise SystemExit(f"PROJECT_PATH does not exist: {project_abs}")
    output_abs = os.path.abspath(args.output_path)
    out_dir = os.path.dirname(output_abs) or "."
    proj_dir = project_abs if os.path.isdir(project_abs) else os.path.dirname(project_abs)
    if os.path.commonpath([os.path.abspath(out_dir), os.path.abspath(proj_dir)]) == os.path.abspath(proj_dir):
        raise SystemExit(
            "OUTPUT_PATH must live outside PROJECT_PATH: the analyzed project is "
            f"read-only end-to-end, but outputs would be written under {proj_dir}")
    if not 1 <= args.panel_size <= len(LENSES):
        raise SystemExit(f"panel-size must be between 1 and {len(LENSES)} (one expert per lens)")
    if os.path.exists(args.state_file):
        existing = load_state(args.state_file)
        raise SystemExit(
            f"state file already exists ({args.state_file}, run {existing['run_id']}); "
            "use `resume` instead of `init` to continue it"
        )
    state = {
        "version": STATE_VERSION,
        "run_id": f"rm-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "params": {
            "project_path": project_abs,
            "goal": args.goal,
            "knowledge_base": args.knowledge_base,
            "question_target": args.question_target,
            "panel_size": args.panel_size,
            "model_policy": args.model_policy,
            "time_budget_minutes": args.time_budget_minutes,
            "max_panels": args.max_panels,
            "output_path": output_abs,
        },
        "project_fingerprint_start": fingerprint(args.project_path),
        "questions": {},
        "counters": {"rounds": 0, "panels_run": 0, "rounds_without_new_children": 0},
        "started_at_epoch": time.time(),
        "stop_reason": None,
    }
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "run_id": state["run_id"]}, ensure_ascii=False))


def next_question_id(state: dict) -> str:
    return f"Q{len(state['questions']) + 1:03d}"


def cmd_add_question(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    if len(state["questions"]) >= state["params"]["question_target"]:
        print(json.dumps({"ok": False, "reason": "question_target_reached"}, ensure_ascii=False))
        return
    if args.category not in CATEGORIES:
        raise SystemExit(f"unknown category '{args.category}'; expected one of {CATEGORIES}")
    kind = args.kind
    if kind not in ("fact", "preference"):
        raise SystemExit("kind must be 'fact' or 'preference'")
    dup = find_duplicate(state, args.title, args.body)
    if dup:
        dup_id, score = dup
        print(json.dumps(
            {"ok": False, "duplicate_of": dup_id, "similarity": score,
             "note": "not added; ask a genuinely different question"},
            ensure_ascii=False))
        return
    for dep in args.depends_on or []:
        if dep not in state["questions"]:
            raise SystemExit(f"unknown dependency '{dep}'")
    qid = next_question_id(state)
    state["questions"][qid] = {
        "id": qid,
        "title": args.title.strip(),
        "body": args.body.strip(),
        "category": args.category,
        "kind": kind,
        "depends_on": list(args.depends_on or []),
        "children": [],
        "status": "open",
        "panels": [],
        "decision": None,
    }
    for dep in args.depends_on or []:
        state["questions"][dep]["children"].append(qid)
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "id": qid}, ensure_ascii=False))


def cmd_frontier(args) -> None:
    state = load_state(args.state_file)
    limit = args.limit
    frontier = [
        q for q in state["questions"].values()
        if q["status"] == "open" and all(
            state["questions"][d]["status"] in ("aggregated", "parked")
            for d in q["depends_on"]
        )
    ]
    frontier.sort(key=lambda q: (len(q["depends_on"]), q["id"]))
    selected = frontier[:limit]
    print(json.dumps({
        "frontier_size": len(frontier),
        "selected": [
            {"id": q["id"], "title": q["title"], "body": q["body"],
             "category": q["category"], "kind": q["kind"]}
            for q in selected
        ],
    }, ensure_ascii=False, indent=2))


def cmd_claim(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    q = state["questions"].get(args.question_id)
    if not q:
        raise SystemExit(f"unknown question {args.question_id}")
    if q["status"] != "open":
        raise SystemExit(f"question {args.question_id} has status '{q['status']}', expected 'open'")
    q["status"] = "queued"
    save_state(args.state_file, state)
    print(json.dumps({"ok": True}, ensure_ascii=False))


def parse_verdict(text: str) -> dict:
    candidates = [
        m.group(1) for m in re.finditer(
            r"```(?:json|JSON)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    ]
    candidates.append(text)
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    raise SystemExit(
        "could not parse an expert verdict: no JSON object found in reply; "
        "re-run that expert once with a reminder to end with exactly one fenced JSON block"
    )


def validate_verdict(v: dict) -> None:
    missing = {"lens", "answer", "confidence", "rationale", "evidence", "unknowns", "counterargument"} - set(v)
    if missing:
        raise SystemExit(f"verdict missing fields: {sorted(missing)}")
    if v["lens"] not in LENSES:
        raise SystemExit(f"unknown lens '{v['lens']}'; expected one of {LENSES}")
    conf = v["confidence"]
    if not isinstance(conf, (int, float)) or not 0 <= float(conf) <= 1:
        raise SystemExit("confidence must be a number in [0,1]")


def cmd_record_verdict(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    q = state["questions"].get(args.question_id)
    if not q:
        raise SystemExit(f"unknown question {args.question_id}")
    if q["status"] not in ("open", "queued"):
        raise SystemExit(
            "question %s is '%s' and already settled; completed panels are "
            "immutable (resume guarantee)" % (args.question_id, q["status"]))
    verdict = parse_verdict(sys.stdin.read() if args.verdict == "-" else args.verdict)
    validate_verdict(verdict)
    if any(p["lens"] == verdict["lens"] for p in q["panels"]):
        print(json.dumps({"ok": False, "reason": f"lens '{verdict['lens']}' already recorded"},
                         ensure_ascii=False))
        return
    verdict["confidence"] = round(float(verdict["confidence"]), 3)
    q["panels"].append(verdict)
    q["status"] = "queued"
    state["counters"]["panels_run"] += 1
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "panels_recorded": len(q["panels"]),
                      "panel_size_target": state["params"]["panel_size"]}, ensure_ascii=False))


def norm_answer(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def evidence_strength(panels: list[dict]) -> tuple[int, int]:
    with_ev = sum(1 for p in panels if p.get("evidence"))
    sources = {e["source"] for p in panels for e in p.get("evidence", []) if e.get("source")}
    return with_ev, len(sources)


def aggregate_one(question: dict) -> dict | None:
    panels = question["panels"]
    if not panels:
        return None
    groups: dict[str, dict] = {}
    for p in panels:
        key = norm_answer(p["answer"])
        g = groups.setdefault(key, {"answer": p["answer"].strip(), "count": 0,
                                    "confidence_sum": 0.0, "lenses": [],
                                    "evidenced": False})
        g["count"] += 1
        g["confidence_sum"] += float(p["confidence"])
        g["lenses"].append(p["lens"])
        g["evidenced"] = g["evidenced"] or bool(p.get("evidence"))
    ranked = sorted(groups.values(), key=lambda g: (-g["count"], -g["confidence_sum"]))
    top = ranked[0]
    total = len(panels)
    share = top["count"] / total
    avg_conf = top["confidence_sum"] / top["count"]

    dissent = [{
        "lens": p["lens"],
        "position": p["answer"].strip(),
        "reason": p.get("counterargument") or p.get("rationale", ""),
    } for p in panels if norm_answer(p["answer"]) != norm_answer(top["answer"])]

    weak_evidence = all(float(p["confidence"]) < 0.5 for p in panels)

    if question["kind"] == "fact":
        with_ev, distinct_sources = evidence_strength(panels)
        evidential_groups = [g for g in ranked if g["evidenced"]]
        evidential_top = top["evidenced"]
        conflicting_evidence = len(evidential_groups) > 1
        if conflicting_evidence:
            return {
                "resolution": "", "basis": "peer-review", "confidence": 0.0,
                "dissent": dissent, "_unresolved_conflict": True,
            }
        if evidential_top and (with_ev / total >= 0.5 or distinct_sources >= 2):
            basis = "evidence"
        elif weak_evidence:
            return {
                "resolution": "", "basis": "peer-review", "confidence": 0.0,
                "dissent": dissent, "_unresolved_conflict": True,
            }
        else:
            basis = "majority"
    else:
        basis = "majority"

    needs_review = share <= 0.6 or weak_evidence
    confidence = round(min(1.0, avg_conf * (0.6 + 0.4 * share)), 3)
    return {
        "resolution": top["answer"],
        "basis": basis,
        "confidence": confidence,
        "dissent": dissent,
        "_needs_review": needs_review,
    }


def cmd_aggregate(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    ids = [args.question_id] if args.question_id else sorted(state["questions"])
    out = {}
    changed = False
    for qid in ids:
        q = state["questions"][qid]
        if q["status"] != "queued":
            continue
        if len(q["panels"]) < min(2, state["params"]["panel_size"]):
            continue
        decision = aggregate_one(q)
        unresolved = decision.pop("_unresolved_conflict", False)
        needs_review = decision.pop("_needs_review", False) or unresolved
        q["decision"] = decision
        q["status"] = "needs_review" if needs_review else "aggregated"
        out[qid] = {"status": q["status"], "basis": decision["basis"],
                    "resolution": decision["resolution"] or "(unresolved conflict)",
                    "dissent_count": len(decision["dissent"])}
        changed = True
    if changed:
        save_state(args.state_file, state)
    print(json.dumps({"ok": True, "aggregated": out}, ensure_ascii=False, indent=2))


def cmd_resolve_review(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    q = state["questions"].get(args.question_id)
    if not q:
        raise SystemExit(f"unknown question {args.question_id}")
    if q["status"] != "needs_review":
        raise SystemExit(f"question {args.question_id} status is '{q['status']}', expected 'needs_review'")
    if not args.resolution.strip():
        raise SystemExit("resolution must not be empty; park the question instead")
    if not 0 <= args.confidence <= 1:
        raise SystemExit("confidence must be within [0,1]")
    basis = "peer-review"
    if q["kind"] == "fact":
        res_norm = norm_answer(args.resolution)
        for p in q["panels"]:
            if not p.get("evidence"):
                continue
            ans_norm = norm_answer(p["answer"])
            if ans_norm and (ans_norm in res_norm or res_norm in ans_norm):
                basis = "evidence"
                break
    q["decision"] = {
        "resolution": args.resolution.strip(),
        "basis": basis,
        "confidence": round(args.confidence, 3),
        "dissent": q["decision"]["dissent"] if q.get("decision") else [],
    }
    q["status"] = "aggregated"
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "basis": basis}, ensure_ascii=False))


def cmd_park(args) -> None:
    state = load_state(args.state_file)
    assert_stop_not_set(state)
    q = state["questions"].get(args.question_id)
    if not q:
        raise SystemExit(f"unknown question {args.question_id}")
    if q["status"] == "aggregated":
        raise SystemExit("question is aggregated and settled; parking would hide a decided requirement")
    q["status"] = "parked"
    save_state(args.state_file, state)
    print(json.dumps({"ok": True}, ensure_ascii=False))


def cmd_round_done(args) -> None:
    state = load_state(args.state_file)
    if state["stop_reason"]:
        print(json.dumps({"ok": False, "stop_reason": state["stop_reason"]}, ensure_ascii=False))
        return
    state["counters"]["rounds"] += 1
    if args.new_children is not None:
        grew = args.new_children > 0
        state["counters"]["rounds_without_new_children"] = (
            0 if grew else state["counters"]["rounds_without_new_children"] + 1
        )
    reason = evaluate_stop(state)
    state["stop_reason"] = reason
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "rounds": state["counters"]["rounds"],
                      "stop_reason": reason}, ensure_ascii=False))


def evaluate_stop(state: dict) -> str | None:
    p = state["params"]
    c = state["counters"]
    resolved = sum(1 for q in state["questions"].values()
                   if q["status"] in ("aggregated",))
    if resolved >= p["question_target"]:
        return "target_reached"
    if p.get("max_panels") is not None and c["panels_run"] >= p["max_panels"]:
        return "budget_panels"
    if p.get("time_budget_minutes") is not None and state.get("started_at_epoch"):
        elapsed_min = (time.time() - state["started_at_epoch"]) / 60
        if elapsed_min >= p["time_budget_minutes"]:
            return "budget_time"
    if c.get("rounds_without_new_children", 0) >= DIMINISHING_RETURN_ROUNDS:
        return "diminishing_return"
    frontier_alive = any(q["status"] in ("open", "queued", "needs_review")
                         for q in state["questions"].values())
    if not frontier_alive:
        return "frontier_exhausted"
    return None


def assert_stop_not_set(state: dict) -> None:
    if state.get("stop_reason"):
        raise SystemExit(f"run already stopped ({state['stop_reason']})")


def cmd_status(args) -> None:
    state = load_state(args.state_file)
    qs = state["questions"]
    by_status: dict[str, int] = {}
    for q in qs.values():
        by_status[q["status"]] = by_status.get(q["status"], 0) + 1
    resolved = sum(1 for q in qs.values() if q["status"] == "aggregated")
    pending = [q["id"] for q in qs.values() if q["status"] in ("queued",)]
    needs_review = [q["id"] for q in qs.values() if q["status"] == "needs_review"]
    print(json.dumps({
        "run_id": state["run_id"],
        "resumable": state["stop_reason"] is None,
        "stop_reason": state["stop_reason"],
        "target": state["params"]["question_target"],
        "resolved": resolved,
        "by_status": by_status,
        "pending_panel_completion": pending,
        "needs_review": needs_review,
        "panels_run": state["counters"]["panels_run"],
        "rounds": state["counters"]["rounds"],
        "next_id": f"Q{len(qs) + 1:03d}",
    }, ensure_ascii=False, indent=2))


def cmd_verify_project(args) -> bool:
    state = load_state(args.state_file)
    current = fingerprint(state["params"]["project_path"])
    ok = fingerprint_matches(state["project_fingerprint_start"], current)
    result = {
        "ok": ok,
        "start": state["project_fingerprint_start"]["digest"] if state.get("project_fingerprint_start") else None,
        "current": current["digest"],
        "note": "target project unchanged (byte-for-byte)" if ok
        else "TARGET PROJECT CHANGED since run start — investigate before trusting outputs",
    }
    if args.save:
        state.setdefault("provenance", {})["project_fingerprint_end"] = current
        state["provenance"]["project_unchanged"] = ok
        save_state(args.state_file, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return ok


def render_brief_md(results: dict) -> str:
    lines = []
    p = results["params"]
    lines.append("# Requirements Brief")
    lines.append("")
    lines.append(f"- **Goal:** {p['goal']}")
    lines.append(f"- **Project:** `{p['project_path']}`")
    if p.get("knowledge_base"):
        lines.append(f"- **Knowledge base:** {p['knowledge_base']}")
    prov = results.get("provenance", {})
    lines.append(f"- **Run:** `{results['run_id']}` · stop: `{results['stop_reason']}`"
                 f" · rounds: {prov.get('counters', {}).get('rounds', '?')}"
                 f" · panels: {prov.get('counters', {}).get('panels_run', '?')}"
                 f" · model policy: `{prov.get('model_policy', 'inherit')}`")
    if prov.get("model_observed"):
        lines.append(f"- **Models observed (receipts):** {prov['model_observed']}")
    lines.append("")

    reqs = results["requirements"]
    facts = [r for r in reqs if r["epistemic_class"] == "fact"]
    prefs = [r for r in reqs if r["epistemic_class"] == "preference"]
    assum = [r for r in reqs if r["epistemic_class"] == "assumption"]

    lines.append("## Accepted requirements")
    lines.append("")
    lines.append("### Facts (decided by evidence)")
    lines.append("")
    for r in facts:
        src = f" — `{r['evidence'][0]['source']}`" if r.get("evidence") else ""
        lines.append(f"- **{r['id']}**. {r['text']}{src}")
    lines.append("")
    lines.append("### Preferences (weighted panel majority)")
    lines.append("")
    for r in prefs:
        lines.append(f"- **{r['id']}**. {r['text']} (from {r['source_question'] or 'seed'})")
    lines.append("")
    lines.append("### Working assumptions (unverified, carry risk)")
    lines.append("")
    for r in assum:
        lines.append(f"- **{r['id']}**. {r['text']}")
    lines.append("")

    lines.append("## Open contradictions")
    lines.append("")
    if results["open_contradictions"]:
        for c in results["open_contradictions"]:
            lines.append(f"- **{c['question_id']}**: {c['summary']}")
            for pos in c["positions"]:
                lines.append(f"  - {pos}")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Preserved dissent")
    lines.append("")
    if results.get("dissent_log"):
        for d in results["dissent_log"]:
            lines.append(f"- **{d['question_id']}** ({d['lens']}): {d['position']} — {d['reason']}")
    else:
        lines.append("- No material dissent recorded.")
    lines.append("")

    lines.append("## Success criteria")
    lines.append("")
    for s in results["success_criteria"]:
        mark = "x" if s.get("verifiable") else "~"
        how = f" (verify: {s['how_to_verify']})" if s.get("how_to_verify") else ""
        lines.append(f"- [{mark}] {s['criterion']}{how}")
    lines.append("")

    fi = results["first_increment"]
    lines.append("## Recommended first increment")
    lines.append("")
    lines.append(fi["description"])
    lines.append("")
    lines.append(f"*Why this first:* {fi['rationale']}")
    if fi.get("excluded_until_later"):
        lines.append("")
        lines.append("*Deliberately excluded until later:* " + "; ".join(fi["excluded_until_later"]))
    lines.append("")
    return "\n".join(lines)


def collect_results(state: dict, harness: str, model_observed: str | None) -> dict:
    questions = []
    requirements = []
    contradictions = []
    dissent_log = []
    success_criteria = []
    counter = 0

    def rid() -> str:
        nonlocal counter
        counter += 1
        return f"R{counter:03d}"

    for qid in sorted(state["questions"]):
        q = state["questions"][qid]
        questions.append({
            "id": q["id"], "title": q["title"], "body": q["body"],
            "category": q["category"], "kind": q["kind"],
            "depends_on": q["depends_on"], "status": q["status"],
            "panels": q["panels"], "decision": q["decision"],
        })
        if q["status"] == "aggregated" and q.get("decision"):
            if q["kind"] == "fact":
                epistemic = "fact" if q["decision"]["basis"] == "evidence" else "assumption"
            else:
                epistemic = "preference"
            ev = []
            for p in q["panels"]:
                if re.sub(r"\s+", " ", p["answer"].strip().lower()) == \
                        re.sub(r"\s+", " ", q["decision"]["resolution"].strip().lower()):
                    ev.extend(p.get("evidence", []))
            requirements.append({
                "id": rid(), "text": q["decision"]["resolution"],
                "source_question": qid, "epistemic_class": epistemic,
                "evidence": ev[:3],
            })
        elif q["status"] == "needs_review":
            positions = [f"{p['lens']}: {p['answer'].strip()}" for p in q["panels"]]
            contradictions.append({
                "question_id": qid, "summary": q["title"], "positions": positions,
            })
        elif q["status"] == "parked":
            positions = [f"{p['lens']}: {p['answer'].strip()}" for p in q["panels"]]
            contradictions.append({
                "question_id": qid,
                "summary": f"{q['title']} (parked unresolved)",
                "positions": positions or [q["body"]],
            })
        elif q["status"] in ("open", "queued"):
            requirements.append({
                "id": rid(), "text": f"[ASSUMPTION] {q['title']} — never settled: {q['body']}",
                "source_question": qid, "epistemic_class": "assumption", "evidence": [],
            })
        if q.get("decision"):
            for d in q["decision"].get("dissent", []):
                dissent_log.append({"question_id": qid, **d})
        if q["category"] == "success-criterion":
            dec = q.get("decision")
            if dec:
                success_criteria.append({
                    "criterion": dec["resolution"],
                    "verifiable": any(p.get("evidence") for p in q["panels"]),
                    "how_to_verify": "",
                })

    if not success_criteria:
        success_criteria.append({
            "criterion": "No explicit success criteria surfaced — treat all requirements below as provisional.",
            "verifiable": False, "how_to_verify": "",
        })

    return {
        "version": STATE_VERSION,
        "run_id": state["run_id"],
        "params": state["params"],
        "stop_reason": state["stop_reason"] or "incomplete",
        "questions": questions,
        "requirements": requirements,
        "open_contradictions": contradictions,
        "success_criteria": success_criteria,
        "first_increment": {"description": "", "rationale": ""},
        "dissent_log": dissent_log,
        "provenance": {
            "harness": harness,
            "model_observed": model_observed,
            "model_policy": state["params"]["model_policy"],
            "project_fingerprint_end": state.get("provenance", {}).get("project_fingerprint_end"),
            "counters": {k: v for k, v in state["counters"].items() if not k.startswith("_")},
        },
    }


def cmd_finish(args) -> None:
    state = load_state(args.state_file)
    if not state["stop_reason"]:
        state["stop_reason"] = evaluate_stop(state) or "frontier_exhausted"
    try:
        first_increment = json.loads(args.first_increment)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--first-increment is not valid JSON: {exc}")
    if not isinstance(first_increment, dict):
        raise SystemExit("--first-increment must be a JSON object")
    if not str(first_increment.get("description", "")).strip():
        raise SystemExit("first_increment.description must not be empty")
    if not str(first_increment.get("rationale", "")).strip():
        raise SystemExit("first_increment.rationale must not be empty")
    results = collect_results(state, args.harness, args.model_observed)
    out_base = os.path.splitext(state["params"]["output_path"])[0]
    brief_path = out_base + ".md"
    results_path = out_base + ".results.json"
    results["first_increment"] = first_increment
    os.makedirs(os.path.dirname(os.path.abspath(brief_path)) or ".", exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write(render_brief_md(results))
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "brief": brief_path, "results": results_path},
                     ensure_ascii=False))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="rm_state.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        sp = sub.add_parser(name, **kw)
        return sp

    sp = add("init")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--project-path", required=True)
    sp.add_argument("--goal", required=True)
    sp.add_argument("--knowledge-base", default=None)
    sp.add_argument("--question-target", type=int, default=100)
    sp.add_argument("--panel-size", type=int, default=5)
    sp.add_argument("--model-policy", choices=["inherit", "cheap"], default="inherit")
    sp.add_argument("--time-budget-minutes", type=int, default=None)
    sp.add_argument("--max-panels", type=int, default=None)
    sp.add_argument("--output-path", default="./requirements-brief.md")
    sp.set_defaults(func=cmd_init)

    sp = add("add-question")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--category", required=True)
    sp.add_argument("--kind", default="preference")
    sp.add_argument("--depends-on", nargs="*", default=[])
    sp.set_defaults(func=cmd_add_question)

    sp = add("frontier")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_frontier)

    sp = add("claim")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--question-id", required=True)
    sp.set_defaults(func=cmd_claim)

    sp = add("record-verdict")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--question-id", required=True)
    sp.add_argument("--verdict", default="-",
                    help="JSON string or '-' to read stdin (raw reply with fenced JSON ok)")
    sp.set_defaults(func=cmd_record_verdict)

    sp = add("aggregate")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--question-id", default=None)
    sp.set_defaults(func=cmd_aggregate)

    sp = add("resolve-review")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--question-id", required=True)
    sp.add_argument("--resolution", required=True)
    sp.add_argument("--confidence", type=float, default=0.6)
    sp.set_defaults(func=cmd_resolve_review)

    sp = add("park")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--question-id", required=True)
    sp.set_defaults(func=cmd_park)

    sp = add("round-done")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--new-children", type=int, default=None,
                    help="total dependent questions added during this round")
    sp.set_defaults(func=cmd_round_done)

    sp = add("status")
    sp.add_argument("--state-file", required=True)
    sp.set_defaults(func=cmd_status)

    sp = add("verify-project")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--save", action="store_true")
    sp.set_defaults(func=cmd_verify_project)

    sp = add("finish")
    sp.add_argument("--state-file", required=True)
    sp.add_argument("--harness", default="unknown")
    sp.add_argument("--model-observed", default=None)
    sp.add_argument("--first-increment", required=True,
                    help='JSON object {"description":..., "rationale":..., "excluded_until_later":[...]}')
    sp.set_defaults(func=cmd_finish)

    args = ap.parse_args(argv)
    ret = args.func(args)
    if isinstance(ret, bool) and not ret:
        sys.exit(1)


if __name__ == "__main__":
    main()
