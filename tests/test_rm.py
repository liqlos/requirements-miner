#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
RM_STATE = SKILL_ROOT / "scripts" / "rm_state.py"
RM_VALIDATE = SKILL_ROOT / "scripts" / "rm_validate.py"


def run(*argv, expect_ok=True, stdin=None):
    proc = subprocess.run(
        [sys.executable, str(RM_STATE), *argv],
        capture_output=True, text=True, input=stdin,
    )
    if expect_ok and proc.returncode != 0:
        raise AssertionError(f"{argv} failed:\n{proc.stderr}\n{proc.stdout}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout, "code": proc.returncode}


def verdict(lens, answer, confidence=0.8, evidence=None, counter="because"):
    return json.dumps({
        "lens": lens, "answer": answer, "confidence": confidence,
        "rationale": f"{lens} reasoning", "evidence": evidence or [],
        "unknowns": [], "counterargument": counter,
    })


LENSES = ["user-product", "feasibility", "critic", "domain", "scope"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rmtest-"))
        self.state = str(self.tmp / "run.state.json")
        self.project = self.tmp / "proj"
        (self.project / "src").mkdir(parents=True)
        (self.project / "README.md").write_text("# proj\n")
        (self.project / "src" / "app.py").write_text("print('hi')\n")
        run("init", "--state-file", self.state, "--project-path", str(self.project),
            "--goal", "test goal", "--output-path", str(self.tmp / "brief.md"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_q(self, title, body="body text for testing purposes", kind="preference",
              depends_on=None, state_file=None):
        r = run("add-question", "--state-file", state_file or self.state,
                "--title", title, "--body", body, "--category", "product",
                "--kind", kind, "--depends-on", *(depends_on or []))
        return r.get("id")

    def fill_and_settle(self, title):
        qid = next(q["id"] for q in json.loads(
            Path(self.state).read_text())["questions"].values() if q["title"] == title)
        self.fill_panel(qid, ["a", "a"])
        run("aggregate", "--state-file", self.state)
        return qid

    def fill_panel(self, qid, answers, confs=None, evidences=None, state_file=None):
        for i, ans in enumerate(answers):
            lens = LENSES[i % len(LENSES)]
            conf = (confs or [])[i] if confs else 0.8
            ev = (evidences or [None] * len(answers))[i]
            run("record-verdict", "--state-file", state_file or self.state,
                "--question-id", qid,
                "--verdict", "-", stdin=verdict(lens, ans, conf, evidence=ev))


class TestInit(Base):
    def test_fingerprint_recorded_and_project_verified_unchanged(self):
        st = run("verify-project", "--state-file", self.state)
        self.assertTrue(st["ok"])
        (self.tmp / "unrelated.txt").write_text("x")
        self.assertTrue(run("verify-project", "--state-file", self.state)["ok"])
        (self.project / "src" / "app.py").write_text("print('changed')\n")
        self.assertFalse(run("verify-project", "--state-file", self.state,
                             expect_ok=False)["ok"])

    def test_init_refuses_existing_state(self):
        with self.assertRaises(AssertionError):
            run("init", "--state-file", self.state, "--project-path",
                str(self.project), "--goal", "again")


class TestDedupe(Base):
    def test_paraphrase_rejected(self):
        id1 = self.add_q("Should auth use OAuth device flow?",
                         "We need to decide how users authenticate on the CLI.")
        dup = run("add-question", "--state-file", self.state,
                  "--title", "Should authentication use OAuth device flow?",
                  "--body", "Decide how users authenticate on the CLI.",
                  "--category", "product", "--kind", "preference")
        self.assertFalse(dup["ok"])
        self.assertEqual(dup["duplicate_of"], id1)

    def test_genuinely_new_question_accepted(self):
        self.add_q("Storage backend choice", "Where should tasks persist?")
        r = run("add-question", "--state-file", self.state,
                "--title", "Offline conflict resolution strategy",
                "--body", "How do concurrent edits merge after being offline?",
                "--category", "edge-case", "--kind", "preference")
        self.assertTrue(r["ok"])

    def test_unknown_dependency_rejected(self):
        with self.assertRaises(AssertionError):
            self.add_q("Orphan question", "Depends on nothing that exists Q999.",
                       depends_on=["Q999"])


class TestFrontier(Base):
    def test_dependencies_gate_frontier(self):
        parent = self.add_q("Parent decision", "This blocks the child question.")
        child = self.add_q("Child decision", "Waits until the parent is aggregated.",
                           depends_on=[parent])
        fr = run("frontier", "--state-file", self.state)
        ids = [q["id"] for q in fr["selected"]]
        self.assertIn(parent, ids)
        self.assertNotIn(child, ids)
        self.fill_panel(parent, ["yes", "yes"])
        run("aggregate", "--state-file", self.state)
        fr = run("frontier", "--state-file", self.state)
        self.assertIn(child, [q["id"] for q in fr["selected"]])


class TestAggregation(Base):
    def _resolved(self, qid):
        st = json.loads(Path(self.state).read_text())
        return st["questions"][qid]

    def test_weighted_majority_with_dissent_preserved(self):
        qid = self.add_q("UI framework", "Which UI stack should we adopt?")
        self.fill_panel(qid, ["React", "React", "React", "React", "Vue"],
                        confs=[0.9, 0.8, 0.85, 0.7, 0.9])
        out = run("aggregate", "--state-file", self.state)
        rec = out["aggregated"][qid]
        self.assertEqual(rec["status"], "aggregated")
        q = self._resolved(qid)
        self.assertEqual(q["decision"]["resolution"], "React")
        self.assertEqual(len(q["decision"]["dissent"]), 1)
        self.assertEqual(q["decision"]["dissent"][0]["lens"], "scope")
        self.assertIn("Vue", q["decision"]["dissent"][0]["position"])

    def test_no_majority_goes_to_peer_review(self):
        qid = self.add_q("Split call", "Panel splits evenly on this fork.")
        self.fill_panel(qid, ["Option A", "Option B"], confs=[0.9, 0.9])
        out = run("aggregate", "--state-file", self.state)
        self.assertEqual(out["aggregated"][qid]["status"], "needs_review")

    def test_low_confidence_everyone_triggers_review(self):
        qid = self.add_q("Shaky consensus", "Everyone unsure here.")
        self.fill_panel(qid, ["X", "X"], confs=[0.3, 0.35])
        out = run("aggregate", "--state-file", self.state)
        self.assertEqual(out["aggregated"][qid]["status"], "needs_review")

    def test_evidence_beats_lack_of_evidence_for_facts(self):
        qid = self.add_q("Which store does the app use?",
                         "The persistence layer is already implemented somewhere.",
                         kind="fact")
        ev_a = [{"source": "src/app.py:10", "quote": "STORE.write_text"}]
        self.fill_panel(qid, ["JSON file store", "SQLite database", "JSON file store"],
                        confs=[0.9, 0.95, 0.85], evidences=[ev_a, None, ev_a])
        out = run("aggregate", "--state-file", self.state)
        rec = out["aggregated"][qid]
        self.assertEqual(rec["status"], "aggregated")
        self.assertEqual(rec["basis"], "evidence")
        self.assertIn("JSON", rec["resolution"])

    def test_unsupported_fact_downgrades_to_assumption_in_results(self):
        qid = self.add_q("Users want dark mode", "Claim about user preference data.",
                         kind="fact")
        self.fill_panel(qid, ["Yes", "Yes"], confs=[0.9, 0.9])
        run("aggregate", "--state-file", self.state)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        fin = run("finish", "--state-file", self.state, "--harness", "test",
                  "--first-increment", json.dumps({"description": "d", "rationale": "r"}))
        results = json.loads(Path(fin["results"]).read_text())
        req = next(r for r in results["requirements"] if r["source_question"] == qid)
        self.assertEqual(req["epistemic_class"], "assumption")


class TestStopRules(Base):
    def test_budget_panels_stop(self):
        state2 = str(self.tmp / "budget.state.json")
        run("init", "--state-file", state2, "--project-path", str(self.project),
            "--goal", "g", "--max-panels", "2",
            "--output-path", str(self.tmp / "b.md"))
        r = run("add-question", "--state-file", state2, "--title", "Question one title",
                "--body", "Body one for the budget test.", "--category", "risk")
        q1, q2 = r["id"], None
        r2 = run("add-question", "--state-file", state2, "--title", "Second question",
                 "--body", "Body two distinct content.", "--category", "user")
        q2 = r2["id"]
        for q in (q1, q2):
            run("claim", "--state-file", state2, "--question-id", q)
            run("record-verdict", "--state-file", state2, "--question-id", q,
                "--verdict", "-", stdin=verdict(LENSES[0], "ans"))
            run("record-verdict", "--state-file", state2, "--question-id", q,
                "--verdict", "-", stdin=verdict(LENSES[1], "ans"))
        run("aggregate", "--state-file", state2)
        out = run("round-done", "--state-file", state2, "--new-children", "0")
        self.assertEqual(out["stop_reason"], "budget_panels")

    def test_budget_time_stop(self):
        state2 = str(self.tmp / "time.state.json")
        run("init", "--state-file", state2, "--project-path", str(self.project),
            "--goal", "g", "--time-budget-minutes", "0",
            "--output-path", str(self.tmp / "t.md"))
        self.add_q("Some open question", "Keeps the frontier alive for this test.",
                   state_file=state2)
        out = run("round-done", "--state-file", state2, "--new-children", "0")
        self.assertEqual(out["stop_reason"], "budget_time")

    def test_output_path_inside_project_rejected(self):
        state2 = str(self.tmp / "guard.state.json")
        with self.assertRaises(AssertionError):
            run("init", "--state-file", state2, "--project-path", str(self.project),
                "--goal", "g", "--output-path", str(self.project / "brief.md"))

    def test_diminishing_return_after_three_barren_rounds(self):
        self.add_q("Settled question", "Resolves immediately, spawns nothing.")
        qid = self.fill_and_settle("Settled question")
        self.add_q("Still open question", "Never answered during this run.")
        out = {}
        for i in range(3):
            out = run("round-done", "--state-file", self.state, "--new-children", "0")
        self.assertEqual(out["stop_reason"], "diminishing_return")

    def test_target_reached(self):
        state2 = str(self.tmp / "target.state.json")
        run("init", "--state-file", state2, "--project-path", str(self.project),
            "--goal", "g", "--question-target", "1",
            "--output-path", str(self.tmp / "t2.md"))
        qid = self.add_q("Single target question", "One is enough for this tiny scope.",
                         state_file=state2)
        self.fill_panel(qid, ["a", "a"], state_file=state2)
        run("aggregate", "--state-file", state2)
        out = run("round-done", "--state-file", state2, "--new-children", "0")
        self.assertEqual(out["stop_reason"], "target_reached")

    def test_stopped_run_refuses_new_questions(self):
        qid = self.add_q("Blocker question", "Fill me then stop the run.")
        self.fill_panel(qid, ["a", "a"])
        run("aggregate", "--state-file", self.state)
        for _ in range(3):
            run("round-done", "--state-file", self.state, "--new-children", "0")
        with self.assertRaises(AssertionError):
            self.add_q("Post-stop question", "Must be refused after stopping.")


class TestResume(Base):
    def test_resume_never_reruns_completed_panels(self):
        q1 = self.add_q("First settled question", "This one completes fully.")
        self.fill_panel(q1, ["settled-a", "settled-a"])
        run("aggregate", "--state-file", self.state)
        before = json.loads(Path(self.state).read_text())
        panels_before = dict(
            (k, list(v["panels"])) for k, v in before["questions"].items())

        status = run("status", "--state-file", self.state)
        self.assertTrue(status["resumable"])
        self.assertEqual(status["pending_panel_completion"], [])
        q2 = self.add_q("Second fresh question", "Asked only after the crash.")
        fr = run("frontier", "--state-file", self.state)
        self.assertIn(q2, [x["id"] for x in fr["selected"]])

        after = json.loads(Path(self.state).read_text())
        for k, v in after["questions"].items():
            if k in panels_before:
                self.assertEqual(v["panels"], panels_before[k],
                                 "resume mutated completed panel verdicts")

    def test_duplicate_lens_verdict_rejected(self):
        qid = self.add_q("Dup lens guard", "Same expert cannot vote twice.")
        run("claim", "--state-file", self.state, "--question-id", qid)
        v = verdict("user-product", "ans")
        run("record-verdict", "--state-file", self.state, "--question-id", qid,
            "--verdict", "-", stdin=v)
        out = run("record-verdict", "--state-file", self.state, "--question-id",
                  qid, "--verdict", "-", stdin=v)
        self.assertFalse(out["ok"])

    def test_partial_panel_survives_restart(self):
        qid = self.add_q("Interrupted panel question", "Only two of five answered.")
        run("claim", "--state-file", self.state, "--question-id", qid)
        run("record-verdict", "--state-file", self.state, "--question-id", qid,
            "--verdict", "-", stdin=verdict("user-product", "ans1"))
        status = run("status", "--state-file", self.state)
        self.assertIn(qid, status["pending_panel_completion"])
        run("record-verdict", "--state-file", self.state, "--question-id", qid,
            "--verdict", "-", stdin=verdict("feasibility", "ans1"))
        out = run("aggregate", "--state-file", self.state)
        self.assertEqual(out["aggregated"][qid]["status"], "aggregated")


class TestReviewRegressions(Base):
    def test_conflicting_evidence_on_fact_goes_to_review(self):
        qid = self.add_q("Which database does the app use?",
                         "Two experts cite real sources that disagree.", kind="fact")
        ev_a = [{"source": "src/app.py:1", "quote": "sqlite3.connect"}]
        ev_b = [{"source": "README.md:1", "quote": "# proj uses postgres"}]
        self.fill_panel(qid, ["SQLite", "SQLite", "Postgres", "Postgres"],
                        confs=[0.9, 0.85, 0.9, 0.8], evidences=[ev_a, ev_a, ev_b, ev_b])
        out = run("aggregate", "--state-file", self.state)
        rec = out["aggregated"][qid]
        self.assertEqual(rec["status"], "needs_review")
        st = json.loads(Path(self.state).read_text())
        dissent = st["questions"][qid]["decision"]["dissent"]
        self.assertGreaterEqual(len(dissent), 2)

    def test_share_exactly_060_triggers_review(self):
        qid = self.add_q("Even panel preference", "Three of five is not a mandate.")
        self.fill_panel(qid, ["A", "A", "A", "B", "B"],
                        confs=[0.9] * 5)
        out = run("aggregate", "--state-file", self.state)
        self.assertEqual(out["aggregated"][qid]["status"], "needs_review")

    def test_verdict_on_aggregated_question_rejected(self):
        qid = self.add_q("Settled immutability check", "Late verdicts must bounce.")
        self.fill_panel(qid, ["x", "x"])
        run("aggregate", "--state-file", self.state)
        out = run("record-verdict", "--state-file", self.state,
                  "--question-id", qid, "--verdict", "-",
                  stdin=verdict(LENSES[2], "late"), expect_ok=False)
        self.assertEqual(out.get("code"), 1)

    def test_resolve_review_end_to_end_keeps_dissent_and_downgrades_fact(self):
        qid = self.add_q("Contested fact question", "Panel splits with evidence.",
                         kind="fact")
        ev_a = [{"source": "src/app.py:1", "quote": "a"}]
        ev_b = [{"source": "README.md:1", "quote": "b"}]
        run("claim", "--state-file", self.state, "--question-id", qid)
        for lens, ans, ev in (("user-product", "Alpha design", ev_a),
                              ("feasibility", "Beta design", ev_b)):
            run("record-verdict", "--state-file", self.state,
                "--question-id", qid, "--verdict", "-",
                stdin=verdict(lens, ans, evidence=ev))
        run("aggregate", "--state-file", self.state)
        run("resolve-review", "--state-file", self.state, "--question-id", qid,
            "--resolution", "totally unrelated wording", "--confidence", "0.7")
        st = json.loads(Path(self.state).read_text())
        q = st["questions"][qid]
        self.assertEqual(q["status"], "aggregated")
        self.assertEqual(len(q["decision"]["dissent"]), 1)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        fin = run("finish", "--state-file", self.state, "--harness", "t",
                  "--model-observed", "m",
                  "--first-increment",
                  json.dumps({"description": "d", "rationale": "r"}))
        results = json.loads(Path(fin["results"]).read_text())
        req = next(r for r in results["requirements"] if r["source_question"] == qid)
        self.assertEqual(req["epistemic_class"], "assumption")

    def test_resolve_review_preserves_evidence_basis_when_verdict_echoes_cited_answer(self):
        qid = self.add_q("Fact settled by critic citing code",
                         "Critic picks the cited side.", kind="fact")
        ev_a = [{"source": "src/app.py:1", "quote": "a"}]
        ev_b = [{"source": "README.md:1", "quote": "b"}]
        run("claim", "--state-file", self.state, "--question-id", qid)
        for lens, ans, ev in (("user-product", "Alpha design", ev_a),
                              ("feasibility", "Beta design", ev_b)):
            run("record-verdict", "--state-file", self.state,
                "--question-id", qid, "--verdict", "-",
                stdin=verdict(lens, ans, evidence=ev))
        run("aggregate", "--state-file", self.state)
        out = run("resolve-review", "--state-file", self.state,
                  "--question-id", qid,
                  "--resolution", "keep the alpha design", "--confidence", "0.8")
        self.assertEqual(out["basis"], "evidence")

    def test_parked_question_ships_as_open_contradiction(self):
        qid = self.add_q("Unresolvable fork question", "Nobody can settle this.")
        self.fill_panel(qid, ["left path", "right path"], confs=[0.9, 0.9])
        run("aggregate", "--state-file", self.state)
        run("park", "--state-file", self.state, "--question-id", qid)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        fin = run("finish", "--state-file", self.state, "--harness", "t",
                  "--first-increment", json.dumps({"description": "d", "rationale": "r"}))
        results = json.loads(Path(fin["results"]).read_text())
        ids = [c["question_id"] for c in results["open_contradictions"]]
        self.assertIn(qid, ids)
        brief = Path(fin["brief"]).read_text()
        self.assertIn("Open contradictions", brief)

    def test_park_rejects_aggregated_question(self):
        qid = self.add_q("Already settled question", "Parking this would hide it.")
        self.fill_panel(qid, ["x", "x"])
        run("aggregate", "--state-file", self.state)
        out = run("park", "--state-file", self.state, "--question-id", qid,
                  expect_ok=False)
        self.assertEqual(out.get("code"), 1)

    def test_resolve_review_confidence_out_of_range_rejected(self):
        qid = self.add_q("Bad confidence question", "Critic sends nonsense value.")
        self.fill_panel(qid, ["a", "b"], confs=[0.9, 0.9])
        run("aggregate", "--state-file", self.state)
        out = run("resolve-review", "--state-file", self.state,
                  "--question-id", qid, "--resolution", "whatever",
                  "--confidence", "7.5", expect_ok=False)
        self.assertEqual(out.get("code"), 1)

    def test_finish_rejects_malformed_first_increment_before_writing(self):
        qid = self.add_q("Guard the outputs question", "Finish must validate inputs first.")
        self.fill_panel(qid, ["a", "a"])
        run("aggregate", "--state-file", self.state)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        out_base = str(self.tmp / "guarded")
        st = json.loads(Path(self.state).read_text())
        st["params"]["output_path"] = out_base + ".md"
        Path(self.state).write_text(json.dumps(st))
        proc = subprocess.run(
            [sys.executable, str(RM_STATE), "finish", "--state-file", self.state,
             "--harness", "t", "--first-increment", '{"description":"d"}'],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(out_base + ".md"))
        self.assertFalse(os.path.exists(out_base + ".results.json"))

    def test_finish_requires_first_increment(self):
        qid = self.add_q("Anything at all", "Body long enough for validation.")
        self.fill_panel(qid, ["a", "a"])
        run("aggregate", "--state-file", self.state)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        proc = subprocess.run(
            [sys.executable, str(RM_STATE), "finish", "--state-file", self.state],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)


class TestFinishAndValidation(Base):
    def test_full_pipeline_render_and_validate(self):
        qfact = self.add_q("What language is implemented in?",
                           "Check the source files to answer.", kind="fact")
        ev = [{"source": "fixtures/mini-todo/src/todo.py:1", "quote": "#!/usr/bin/env python3"}]
        self.fill_panel(qfact, ["Python", "Python"], confs=[0.95, 0.9], evidences=[ev, ev])
        qpref = self.add_q("Rename command set?", "Preference about CLI naming.")
        self.fill_panel(qpref, ["keep names", "keep names", "rename all"],
                        confs=[0.9, 0.8, 0.6])
        run("aggregate", "--state-file", self.state)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        run("verify-project", "--state-file", self.state, "--save")
        fin = run("finish", "--state-file", self.state, "--harness", "unittest",
                  "--model-observed", "test-model",
                  "--first-increment", json.dumps({
                      "description": "Ship the smallest working slice",
                      "rationale": "Everything upstream is settled"}))
        brief = Path(fin["brief"]).read_text()
        results = json.loads(Path(fin["results"]).read_text())
        self.assertIn("# Requirements Brief", brief)
        self.assertIn("Preserved dissent", brief)
        req_by_src = {r["source_question"]: r for r in results["requirements"]}
        self.assertEqual(req_by_src[qfact]["epistemic_class"], "fact")
        self.assertEqual(req_by_src[qpref]["epistemic_class"], "preference")
        self.assertEqual(results["provenance"]["harness"], "unittest")
        self.assertEqual(results["provenance"]["model_observed"], "test-model")
        self.assertTrue(results["provenance"]["project_fingerprint_end"])

    def test_structural_validation_of_skill_and_artifacts(self):
        proc = subprocess.run([sys.executable, str(RM_VALIDATE)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        qid = self.add_q("Pipeline for validator check", "Minimal settle to render outputs.")
        self.fill_panel(qid, ["a", "a"])
        run("aggregate", "--state-file", self.state)
        run("round-done", "--state-file", self.state, "--new-children", "0")
        run("verify-project", "--state-file", self.state, "--save")
        fin = run("finish", "--state-file", self.state, "--harness", "unittest",
                  "--model-observed", "none",
                  "--first-increment", json.dumps({"description": "d", "rationale": "r"}))
        proc2 = subprocess.run(
            [sys.executable, str(RM_VALIDATE), "--state", self.state,
             "--results", fin["results"]], capture_output=True, text=True)
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)

    def test_fixture_project_round_trip(self):
        fixture = SKILL_ROOT / "fixtures" / "mini-todo"
        state2 = str(self.tmp / "fixture.state.json")
        run("init", "--state-file", state2, "--project-path", str(fixture),
            "--goal", "smoke check", "--output-path", str(self.tmp / "fx.md"))
        out = run("verify-project", "--state-file", state2)
        self.assertTrue(out["ok"])
        before = (fixture / "src" / "todo.py").read_bytes()
        run("finish", "--state-file", state2, "--harness", "unittest",
            "--model-observed", "none",
            "--first-increment", json.dumps({"description": "x", "rationale": "y"}))
        self.assertEqual((fixture / "src" / "todo.py").read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
