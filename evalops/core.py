"""Input validation and deterministic evaluation; no model calls or telemetry."""

import csv
import json
import math
from collections import Counter
from pathlib import Path


class InputError(ValueError):
    """A dataset is incomplete or cannot be compared safely."""


def read_rows(path):
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            if not headers or len(headers) != len(set(headers)):
                raise InputError(f"{path.name}: CSV 表头为空或重复")
            rows = list(reader)
            if any(None in row or any(v is None for v in row.values()) for row in rows):
                raise InputError(f"{path.name}: CSV 行的字段数与表头不同")
        elif path.suffix.lower() == ".jsonl":
            rows = []
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise InputError(f"{path.name}:{number}: JSON 格式错误") from error
                if not isinstance(row, dict):
                    raise InputError(f"{path.name}:{number}: 每行必须为 JSON 对象")
                rows.append(row)
        else:
            raise InputError("输入文件必须为 .csv 或 .jsonl")
    if not rows:
        raise InputError(f"{path.name}: 数据集不能为空")
    return rows


def text_field(row, name, *, required=False, allow_empty=False):
    if name not in row:
        if required:
            raise InputError(f"缺少字段 {name}")
        return ""
    value = row[name]
    if not isinstance(value, str):
        raise InputError(f"{name} 必须是文本")
    if required and not allow_empty and not value.strip():
        raise InputError(f"{name} 不能为空")
    return value


def number_field(row, name, maximum=None):
    value = row.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InputError(f"{name} 必须是数值，不能是布尔值")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise InputError(f"{name} 必须是非负有限数值") from error
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        raise InputError(f"{name} 必须在 0 到 {maximum if maximum is not None else '有限正数'} 之间")
    return number


def terms_field(row, name):
    value = row.get(name, "")
    if value == "" or value == []:
        return []
    if isinstance(value, str):
        value = value.split("|")
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise InputError(f"{name} 必须为非空字符串数组或以 | 分隔的文本")
    return list(dict.fromkeys(x.strip() for x in value))


def _indexed(rows, convert):
    records = {}
    for row in rows:
        record = convert(row)
        identifier = record["id"]
        if identifier in records:
            raise InputError(f"样本 id 重复: {identifier}")
        records[identifier] = record
    return records


def load_cases(path):
    def convert(row):
        return {
            "id": text_field(row, "id", required=True).strip(),
            "question": text_field(row, "question", required=True),
            "reference_answer": text_field(row, "reference_answer"),
            "required_terms": terms_field(row, "required_terms"),
            "forbidden_terms": terms_field(row, "forbidden_terms"),
            "human_min_score": number_field(row, "human_min_score", 5),
            "category": text_field(row, "category") or "未分类",
        }
    return _indexed(read_rows(path), convert)


def load_answers(path):
    def convert(row):
        return {
            "id": text_field(row, "id", required=True).strip(),
            "response": text_field(row, "response", required=True, allow_empty=True),
            "latency_ms": number_field(row, "latency_ms"),
            "cost_usd": number_field(row, "cost_usd"),
            "human_score": number_field(row, "human_score", 5),
        }
    return _indexed(read_rows(path), convert)


def evaluate(case, answer):
    response = answer["response"]
    normalized = response.lower()
    missing = [term for term in case["required_terms"] if term.lower() not in normalized]
    forbidden = [term for term in case["forbidden_terms"] if term.lower() in normalized]
    reasons = []
    if not response.strip():
        reasons.append("empty_answer")
    if missing:
        reasons.append("missing_required_terms")
    if forbidden:
        reasons.append("forbidden_terms")
    threshold = case["human_min_score"]
    score = answer["human_score"]
    if threshold is not None:
        if score is None:
            reasons.append("missing_human_score")
        elif score < threshold:
            reasons.append("low_human_score")
    return {**answer, "passed": not reasons, "reasons": reasons,
            "missing_terms": missing, "matched_forbidden_terms": forbidden}


def summarize(results):
    total = len(results)
    if not total:
        raise InputError("评测集不能为空")
    metrics = {}
    for field in ("latency_ms", "cost_usd", "human_score"):
        values = [row[field] for row in results if row[field] is not None]
        total_value = sum(values) if values else None
        if total_value is not None and not math.isfinite(total_value):
            raise InputError(f"{field} 汇总溢出，请检查输入数值的单位和范围")
        metrics[field] = {
            "observed": len(values), "total": total,
            "mean": total_value / len(values) if values else None,
            "sum": total_value,
        }
    passed = sum(row["passed"] for row in results)
    return {"total": total, "passed": passed, "failed": total - passed,
            "rule_pass_rate": passed / total, "metrics": metrics,
            "failure_reasons": dict(Counter(reason for row in results for reason in row["reasons"]))}


def compare(cases, baseline, candidate, *, synthetic=False):
    if not cases:
        raise InputError("评测集不能为空")
    for label, answers in (("baseline", baseline), ("candidate", candidate)):
        missing = sorted(set(cases) - set(answers))
        extra = sorted(set(answers) - set(cases))
        if missing or extra:
            raise InputError(f"{label} 的 id 必须与评测集完全一致；缺少={missing}，多余={extra}")
    items = []
    for identifier, case in cases.items():
        before, after = evaluate(case, baseline[identifier]), evaluate(case, candidate[identifier])
        status = "unchanged_pass" if before["passed"] else "unchanged_fail"
        if before["passed"] and not after["passed"]:
            status = "regression"
        elif not before["passed"] and after["passed"]:
            status = "improvement"
        items.append({"case": case, "baseline": before, "candidate": after, "change": status})
    counts = Counter(item["change"] for item in items)
    return {"schema_version": 1,
            "data_provenance": "synthetic_demo" if synthetic else "user_supplied_unverified",
            "metric_note": "规则通过率不是事实准确率；耗时、费用和人工分数均来自输入，非本工具测量或打分。",
            "baseline": summarize([row["baseline"] for row in items]),
            "candidate": summarize([row["candidate"] for row in items]),
            "changes": {key: counts[key] for key in ("regression", "improvement", "unchanged_pass", "unchanged_fail")},
            "has_regressions": bool(counts["regression"]), "items": items}


def promptfoo_tests(cases):
    """Export only automatable text checks, never claim to export human scoring."""
    tests = []
    for case in cases.values():
        assertions = [{"type": "javascript", "value": "typeof output === 'string' && output.trim().length > 0"}]
        assertions += [{"type": "icontains", "value": term} for term in case["required_terms"]]
        assertions += [{"type": "not-icontains", "value": term} for term in case["forbidden_terms"]]
        tests.append({"description": case["id"], "vars": {"question": case["question"]},
                      "assert": assertions,
                      "metadata": {"reference_answer": case["reference_answer"],
                                   "human_min_score_not_exported_as_assertion": case["human_min_score"]}})
    return tests
