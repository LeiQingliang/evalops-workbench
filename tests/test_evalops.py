import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from evalops.__main__ import main
from evalops.core import InputError, compare, evaluate, load_answers, load_cases, number_field, promptfoo_tests
from evalops.report import render_html, write_report


DATA = Path(__file__).parents[1] / "evalops" / "data"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases(DATA / "cases.csv")
        self.baseline = load_answers(DATA / "baseline.csv")
        self.candidate = load_answers(DATA / "candidate.csv")

    def report(self):
        return compare(self.cases, self.baseline, self.candidate, synthetic=True)

    def write_temp(self, directory, name, text):
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_demo_totals_and_case_level_regressions(self):
        report = self.report()
        self.assertEqual(report["baseline"]["passed"], 4)
        self.assertEqual(report["candidate"]["passed"], 6)
        self.assertEqual(report["changes"], {"regression": 1, "improvement": 3, "unchanged_pass": 3, "unchanged_fail": 1})
        self.assertTrue(report["has_regressions"])
        self.assertEqual(report["candidate"]["rule_pass_rate"], 6 / 8)

    def test_missing_and_extra_outputs_rejected(self):
        del self.candidate["reset"]
        with self.assertRaisesRegex(InputError, "缺少"):
            self.report()
        self.candidate["unknown"] = self.baseline["reset"]
        with self.assertRaisesRegex(InputError, "unknown"):
            self.report()

    def test_duplicate_ids_rejected_in_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, loader in [("cases.csv", load_cases), ("baseline.csv", load_answers)]:
                text = (DATA / name).read_text(encoding="utf-8")
                path = self.write_temp(directory, name, text + text.splitlines()[1] + "\n")
                with self.assertRaisesRegex(InputError, "重复"):
                    loader(path)

    def test_empty_dataset_rejected(self):
        with self.assertRaises(InputError):
            compare({}, {}, {})
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_temp(directory, "empty.csv", "id,question\n")
            with self.assertRaises(InputError):
                load_cases(path)

    def test_empty_answer_is_failure_even_without_keyword_rules(self):
        case = {**self.cases["cancel"], "required_terms": [], "forbidden_terms": []}
        self.assertIn("empty_answer", evaluate(case, self.baseline["cancel"])["reasons"])

    def test_case_insensitive_terms(self):
        answer = {**self.candidate["export"], "response": "设置页导出csv"}
        self.assertTrue(evaluate(self.cases["export"], answer)["passed"])

    def test_reference_answer_is_not_a_semantic_judge(self):
        case = {**self.cases["reset"], "reference_answer": "完全不同的参考文本"}
        self.assertTrue(evaluate(case, self.candidate["reset"])["passed"])

    def test_score_threshold_boundary_and_missing(self):
        answer = {**self.candidate["human"], "human_score": 4}
        self.assertTrue(evaluate(self.cases["human"], answer)["passed"])
        answer["human_score"] = None
        self.assertIn("missing_human_score", evaluate(self.cases["human"], answer)["reasons"])

    def test_optional_metrics_have_explicit_denominators(self):
        self.candidate["reset"]["latency_ms"] = None
        expected = sum(row["latency_ms"] for row in self.candidate.values() if row["latency_ms"] is not None) / 7
        metric = self.report()["candidate"]["metrics"]["latency_ms"]
        self.assertEqual((metric["observed"], metric["total"]), (7, 8))
        self.assertAlmostEqual(metric["mean"], expected)
        for row in self.candidate.values():
            row["cost_usd"] = None
        self.assertIsNone(self.report()["candidate"]["metrics"]["cost_usd"]["mean"])

    def test_zero_is_observed_and_invalid_numbers_rejected(self):
        self.assertEqual(number_field({"x": "0"}, "x"), 0)
        for value in ("nan", "inf", "-1", True, [], "oops"):
            with self.subTest(value=value), self.assertRaises(InputError):
                number_field({"x": value}, "x")
        with self.assertRaises(InputError):
            number_field({"x": 5.1}, "x", maximum=5)

    def test_finite_values_cannot_overflow_aggregate(self):
        for answer in self.candidate.values():
            answer["cost_usd"] = 1e308
        with self.assertRaisesRegex(InputError, "汇总溢出"):
            self.report()

    def test_jsonl_and_missing_response_field(self):
        with tempfile.TemporaryDirectory() as directory:
            row = {"id": "x", "question": "Q", "required_terms": ["A", "B"]}
            path = self.write_temp(directory, "cases.jsonl", json.dumps(row) + "\n")
            self.assertEqual(load_cases(path)["x"]["required_terms"], ["A", "B"])
            path = self.write_temp(directory, "answers.jsonl", '{"id":"x"}\n')
            with self.assertRaisesRegex(InputError, "response"):
                load_answers(path)

    def test_malformed_csv_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, text in [("x.csv", "id,question\nx,q,extra\n"), ("x.csv", "id,id\nx,x\n"), ("x.jsonl", "[]\n"), ("x.jsonl", "{broken\n")]:
                path = self.write_temp(directory, name, text)
                with self.subTest(text=text), self.assertRaises(InputError):
                    load_cases(path)

    def test_html_escapes_every_user_surface(self):
        attack = '<script>alert("owned")</script>'
        self.cases["reset"]["question"] = attack
        self.cases["reset"]["category"] = attack
        self.cases["reset"]["reference_answer"] = attack
        self.candidate["reset"]["response"] = attack
        rendered = render_html(self.report())
        self.assertNotIn(attack, rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_report_exports_and_spreadsheet_formula_protection(self):
        self.cases["reset"]["question"] = '=HYPERLINK("https://example.invalid")'
        with tempfile.TemporaryDirectory() as directory:
            write_report(self.report(), directory)
            result = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(result["candidate"]["total"], 8)
            with (Path(directory) / "case-results.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows[0]["question"].startswith("'="))

    def test_promptfoo_export_keeps_human_checks_separate(self):
        tests = promptfoo_tests(self.cases)
        human = next(row for row in tests if row["description"] == "human")
        self.assertEqual(human["metadata"]["human_min_score_not_exported_as_assertion"], 4)
        self.assertEqual(tests[0]["vars"], {"question": self.cases["reset"]["question"]})
        self.assertIn("not-icontains", {a["type"] for a in tests[0]["assert"]})

    def test_cli_fails_regression_after_writing_report(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = main(["run", "--cases", str(DATA / "cases.csv"), "--baseline", str(DATA / "baseline.csv"), "--candidate", str(DATA / "candidate.csv"), "--output", directory, "--fail-on-regression"])
            self.assertEqual(result, 1)
            self.assertTrue((Path(directory) / "report.html").is_file())

    def test_cli_error_exit_code_and_demo(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["demo", "--output", directory]), 0)
            self.assertEqual(main(["run", "--cases", "missing.csv", "--baseline", "missing.csv", "--candidate", "missing.csv"]), 2)

    def test_export_command_quotes_provider_and_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = main(["export-promptfoo", "--cases", str(DATA / "cases.csv"), "--provider", "ollama:chat:my-model", "--output", directory])
            self.assertEqual(result, 0)
            tests = json.loads((Path(directory) / "tests.json").read_text(encoding="utf-8"))
            self.assertEqual(len(tests), 8)
            self.assertIn('"ollama:chat:my-model"', (Path(directory) / "promptfooconfig.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
