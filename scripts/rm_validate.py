#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def check_skill_structure(root: str) -> list[str]:
    problems = []
    skill_md = os.path.join(root, "SKILL.md")
    if not os.path.isfile(skill_md):
        return [f"missing {skill_md}"]
    text = open(skill_md, encoding="utf-8").read()
    m = FRONTMATTER_RE.match(text)
    if not m:
        return ["SKILL.md: no YAML frontmatter block"]
    fm = m.group(1)
    name_m = re.search(r"^name:\s*(\S+)\s*$", fm, re.MULTILINE)
    desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
    if not name_m:
        problems.append("SKILL.md frontmatter: missing 'name'")
    else:
        name = name_m.group(1)
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            problems.append(f"SKILL.md name '{name}' violates naming rules")
        if name != os.path.basename(root):
            problems.append(f"SKILL.md name '{name}' != directory '{os.path.basename(root)}'")
    if not desc_m or len(desc_m.group(1).strip()) < 10:
        problems.append("SKILL.md frontmatter: missing/too-short 'description'")

    refs = re.findall(r"\]\((?!https?://|mailto:|#)([^)#]+)", text)
    refs += re.findall(r"(?:scripts|references|schemas|adapters)/[\w./-]+\.(?:py|sh|md|json|toml)", text)
    for ref in sorted(set(refs)):
        target = os.path.normpath(os.path.join(root, ref.split("#")[0]))
        if "*" in target:
            continue
        if not os.path.exists(target):
            problems.append(f"SKILL.md references missing path: {ref}")

    refdir = os.path.join(root, "references")
    if os.path.isdir(refdir):
        for fn in sorted(os.listdir(refdir)):
            rtext = open(os.path.join(refdir, fn), encoding="utf-8").read()
            for ref in re.findall(r"\]\((\.{1,2}/[^)#]+)\)", rtext):
                target = os.path.normpath(os.path.join(refdir, ref))
                if not os.path.exists(target):
                    problems.append(f"references/{fn}: broken relative ref {ref}")
    return problems


def check_scripts(root: str) -> list[str]:
    import ast
    problems = []
    scripts_dir = os.path.join(root, "scripts")
    for fn in sorted(os.listdir(scripts_dir)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(scripts_dir, fn)
        try:
            ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as e:
            problems.append(f"scripts/{fn}: syntax error: {e}")
    return problems


def check_adapters(root: str) -> list[str]:
    problems = []
    adir = os.path.join(root, "adapters")
    for harness in sorted(os.listdir(adir)):
        hdir = os.path.join(adir, harness)
        if not os.path.isdir(hdir):
            continue
        for fn in sorted(os.listdir(hdir)):
            path = os.path.join(hdir, fn)
            body = open(path, encoding="utf-8").read()
            if fn.endswith(".md"):
                m = FRONTMATTER_RE.match(body)
                if not m:
                    problems.append(f"adapters/{harness}/{fn}: missing frontmatter")
                    continue
                fm = m.group(1)
                if "name:" not in fm and "description:" not in fm:
                    problems.append(f"adapters/{harness}/{fn}: frontmatter lacks name/description")
            elif fn.endswith(".toml"):
                if 'name =' not in body or 'description =' not in body or 'developer_instructions' not in body:
                    problems.append(
                        f"adapters/{harness}/{fn}: codex custom agent needs name/description/developer_instructions")
    return problems



def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_state(path: str) -> list[str]:
    problems = []
    state = load_json(path)
    schema = load_json(os.path.join(SKILL_ROOT, "schemas", "state.schema.json"))
    if state.get("version") != 1:
        problems.append("state.version != 1")
    for key in schema["required"]:
        if key not in state:
            problems.append(f"state missing required key '{key}'")
    allowed_top = set(schema["properties"])
    for key in state:
        if key not in allowed_top:
            problems.append(f"state has undeclared top-level key '{key}'")
    p = state.get("params", {})
    for key in ("project_path", "goal", "question_target", "panel_size", "model_policy"):
        if key not in p:
            problems.append(f"state.params missing '{key}'")
    if p.get("model_policy") not in ("inherit", "cheap"):
        problems.append("state.params.model_policy invalid")
    if not isinstance(p.get("panel_size"), int) or not 1 <= p["panel_size"] <= 5:
        problems.append("state.params.panel_size must be an integer in [1,5]")
    allowed_status = {"open", "queued", "aggregated", "needs_review", "parked"}
    allowed_cat = set(schema["$defs"]["question"]["properties"]["category"]["enum"])
    lens_set = set(schema["$defs"]["verdict"]["properties"]["lens"]["enum"])
    for qid, q in state.get("questions", {}).items():
        if not re.fullmatch(r"Q\d{3}", qid):
            problems.append(f"question id '{qid}' malformed")
        if q.get("status") not in allowed_status:
            problems.append(f"{qid}: bad status '{q.get('status')}'")
        if q.get("category") not in allowed_cat:
            problems.append(f"{qid}: bad category '{q.get('category')}'")
        if q.get("kind") not in ("fact", "preference"):
            problems.append(f"{qid}: bad kind")
        for v in q.get("panels", []):
            if v.get("lens") not in lens_set:
                problems.append(f"{qid}: bad verdict lens '{v.get('lens')}'")
            conf = v.get("confidence", -1)
            if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
                problems.append(f"{qid}: verdict confidence out of range")
            lenses = [x["lens"] for x in q["panels"]]
            if len(lenses) != len(set(lenses)):
                problems.append(f"{qid}: duplicate lens verdicts")
        dec = q.get("decision")
        if dec is not None:
            dconf = dec.get("confidence", -1)
            if not isinstance(dconf, (int, float)) or not 0 <= dconf <= 1:
                problems.append(f"{qid}: decision confidence out of range")
            if dec.get("basis") not in ("evidence", "majority", "peer-review"):
                problems.append(f"{qid}: bad decision basis '{dec.get('basis')}'")
    if state.get("stop_reason") not in (None,) + tuple(
            s for s in ("frontier_exhausted", "target_reached", "budget_time",
                        "budget_panels", "diminishing_return")):
        problems.append(f"bad stop_reason '{state.get('stop_reason')}'")
    return problems


def validate_results(path: str) -> list[str]:
    problems = []
    res = load_json(path)
    for key in ("version", "run_id", "params", "stop_reason", "questions",
                "requirements", "open_contradictions", "success_criteria",
                "first_increment", "provenance"):
        if key not in res:
            problems.append(f"results missing '{key}'")
    seen_r = set()
    for r in res.get("requirements", []):
        rid = r.get("id", "")
        if not re.fullmatch(r"R\d{3}", rid):
            problems.append(f"requirement id '{rid}' malformed")
        if rid in seen_r:
            problems.append(f"duplicate requirement id {rid}")
        seen_r.add(rid)
        if r.get("epistemic_class") not in ("fact", "preference", "assumption"):
            problems.append(f"{rid}: bad epistemic_class")
    fi = res.get("first_increment", {})
    if not fi.get("description"):
        problems.append("first_increment.description empty")
    prov = res.get("provenance", {})
    if "harness" not in prov:
        problems.append("provenance.harness missing")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill-root", default=SKILL_ROOT,
                    help="validate a skill tree (default: this skill)")
    ap.add_argument("--state", help="validate a run-state JSON against schema rules")
    ap.add_argument("--results", help="validate a results JSON against schema rules")
    args = ap.parse_args()

    problems = check_skill_structure(args.skill_root)
    problems += check_scripts(args.skill_root)
    problems += check_adapters(args.skill_root)
    if args.state:
        problems += validate_state(args.state)
    if args.results:
        problems += validate_results(args.results)

    if problems:
        for pr in problems:
            print(f"FAIL: {pr}")
        sys.exit(1)
    print("OK: structural validation passed"
          + (f" (state={args.state})" if args.state else "")
          + (f" (results={args.results})" if args.results else ""))


if __name__ == "__main__":
    main()
