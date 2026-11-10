import argparse
import csv
import json
import sys
from pathlib import Path

from .core import InputError, compare, load_answers, load_cases, promptfoo_tests
from .report import write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线 AI 回答规则评测与版本回归分析")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="使用明确标记的合成数据生成报告")
    demo.add_argument("--out", "--output", default="artifacts/demo")
    run = sub.add_parser("run", help="比较 CSV/JSONL 预录回答")
    for flag in ("cases", "baseline", "candidate"):
        run.add_argument("--" + flag, required=True)
    run.add_argument("--out", "--output", default="artifacts/report")
    run.add_argument("--fail-on-regression", action="store_true", help="发现退步仍写报告，然后返回退出码 1")
    export = sub.add_parser("export-promptfoo", help="导出 promptfoo 规则测试集与配置模板")
    export.add_argument("--cases", required=True)
    export.add_argument("--provider", required=True, help="你自行配置的 promptfoo provider id")
    export.add_argument("--out", "--output", default="artifacts/promptfoo")
    args = parser.parse_args(argv)
    try:
        if args.command == "export-promptfoo":
            tests = promptfoo_tests(load_cases(args.cases))
            if not args.provider.strip():
                raise InputError("provider 不能为空")
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "tests.json").write_text(json.dumps(tests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # JSON string syntax is also valid YAML and safely quotes the provider.
            config = "description: EvalOps text-rule export\nprompts:\n  - '{{question}}'\nproviders:\n  - " + json.dumps(args.provider, ensure_ascii=False) + "\ntests: file://tests.json\n"
            (out / "promptfooconfig.yaml").write_text(config, encoding="utf-8")
            print(f"已导出 {len(tests)} 条文本规则测试: {out.resolve()}")
            print("人工评分阈值仅写入 metadata，未导出为自动断言；本工具未调用 promptfoo 或任何模型。")
            return 0
        if args.command == "demo":
            sample = Path(__file__).parent / "data"
            paths = [sample / name for name in ("cases.csv", "baseline.csv", "candidate.csv")]
        else:
            paths = [args.cases, args.baseline, args.candidate]
        result = compare(load_cases(paths[0]), load_answers(paths[1]), load_answers(paths[2]), synthetic=args.command == "demo")
        report_path = write_report(result, args.out)
        print(f"报告: {report_path.resolve()}")
        print(f"固定样本 {result['baseline']['total']} 条；退步 {result['changes']['regression']} 条；改善 {result['changes']['improvement']} 条")
        print("规则通过率不是事实准确率。演示命令使用合成数据。")
        return 1 if getattr(args, "fail_on_regression", False) and result["has_regressions"] else 0
    except (InputError, OSError, UnicodeError, csv.Error) as error:
        print(f"输入错误: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
