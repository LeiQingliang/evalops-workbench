"""Standalone reports with escaped user text and no external resources."""

import csv
import html
import json
from pathlib import Path


LABELS = {"empty_answer": "空回答", "missing_required_terms": "缺少必含词",
          "forbidden_terms": "命中禁用词", "missing_human_score": "缺少人工评分",
          "low_human_score": "人工评分未达标", "regression": "回归退步",
          "improvement": "改善", "unchanged_pass": "持续通过", "unchanged_fail": "仍未通过"}


def esc(value):
    return html.escape(str(value), quote=True)


def safe_csv(value):
    """Prevent accidental formula execution when people open exported text in a sheet."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def metric(summary, field, suffix):
    value = summary["metrics"][field]
    mean = "未提供" if value["mean"] is None else f'{value["mean"]:.4f} {suffix}'
    return f'{mean}<small>覆盖 {value["observed"]}/{value["total"]}</small>'


def render_html(report):
    baseline, candidate = report["baseline"], report["candidate"]
    provenance = "合成演示数据 · 非真实业务结果" if report["data_provenance"] == "synthetic_demo" else "用户导入数据 · 来源与测量值未经本工具核验"
    rows = []
    for item in report["items"]:
        case, before, after = item["case"], item["baseline"], item["candidate"]
        reasons = "、".join(LABELS[reason] for reason in after["reasons"]) or "全部配置规则通过"
        rows.append(f'''<tr><td><strong>{esc(case["id"])}</strong><small>{esc(case["category"])}</small></td>
<td>{esc(case["question"])}<details><summary>参考答案与检查规则</summary><p>{esc(case["reference_answer"] or "未提供参考答案")}</p><p>必含：{esc(" / ".join(case["required_terms"]) or "无")}<br>禁用：{esc(" / ".join(case["forbidden_terms"]) or "无")}<br>人工最低分：{esc(case["human_min_score"] if case["human_min_score"] is not None else "未启用")}</p></details></td>
<td class="response">{esc(before["response"]) or '<i>空回答</i>'}<small>{"通过" if before["passed"] else "未通过"}</small></td>
<td class="response">{esc(after["response"]) or '<i>空回答</i>'}<small>{esc(reasons)}</small></td>
<td><span class="tag {item["change"]}">{LABELS[item["change"]]}</span></td></tr>''')
    cards = ''.join(f'<div class="card"><span>{label}</span><b>{value}</b></div>' for label, value in [
        ("固定测试问题", str(baseline["total"])),
        ("基线规则通过率", f'{baseline["rule_pass_rate"]:.1%}'),
        ("候选规则通过率", f'{candidate["rule_pass_rate"]:.1%}'),
        ("退步 / 改善", f'{report["changes"]["regression"]} / {report["changes"]["improvement"]}')])
    metrics = ''.join(f'<tr><td>{label}</td><td>{metric(baseline, field, unit)}</td><td>{metric(candidate, field, unit)}</td></tr>' for label, field, unit in [
        ("平均响应耗时", "latency_ms", "ms"), ("平均单条费用", "cost_usd", "USD"), ("平均人工评分", "human_score", "/ 5")])
    failures = ' · '.join(f'{LABELS[key]} {count}' for key, count in candidate["failure_reasons"].items()) or "无规则失败"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EvalOps · 回答效果对比</title>
<style>:root{{color-scheme:light;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#152b3a;background:#f2f6f8}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1320px;margin:auto;padding:42px 24px}}header{{border-left:5px solid #0c7971;padding-left:22px}}.eyebrow{{color:#0c7971;font-size:13px;letter-spacing:2px;font-weight:700}}h1{{font-size:34px;margin:10px 0}}p{{line-height:1.7}}.sub,small{{color:#607684}}small{{display:block;font-size:12px;margin-top:7px}}.banner{{background:#fff8e4;color:#6b5420;padding:13px 18px;border-radius:9px;margin:24px 0}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card,section{{background:white;border:1px solid #dce5e9;border-radius:12px}}.card{{padding:22px}}.card span{{font-size:13px;color:#607684}}.card b{{display:block;font-size:31px;margin-top:10px}}section{{margin-top:22px;padding:24px}}h2{{font-size:19px;margin:0 0 16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;vertical-align:top;padding:14px 12px;border-bottom:1px solid #e7edef;line-height:1.6}}th{{background:#f6f9fa;font-size:12px;color:#607684}}.scroll{{overflow-x:auto}}.cases{{min-width:1000px}}.cases td:nth-child(2){{width:22%}}.response{{width:25%;white-space:pre-wrap;overflow-wrap:anywhere}}.tag{{display:inline-block;border-radius:30px;padding:4px 10px;font-size:12px;white-space:nowrap;background:#edf2f5}}.regression{{background:#fee9e9;color:#9b2929}}.improvement{{background:#e1f5ed;color:#116344}}details{{margin-top:10px;color:#607684;font-size:12px}}summary{{cursor:pointer}}footer{{font-size:12px;color:#607684;margin-top:24px}}@media(max-width:650px){{main{{padding:24px 14px}}h1{{font-size:26px}}.cards{{grid-template-columns:1fr 1fr}}.card{{padding:16px}}section{{padding:16px}}}}</style></head><body><main>
<header><div class="eyebrow">EVALOPS WORKBENCH</div><h1>每一次回答更新，都有据可查</h1><p class="sub">固定样本 · 规则检查 · 版本回归 · 人工复核</p></header>
<div class="banner">{provenance}。{esc(report["metric_note"])}</div><div class="cards">{cards}</div>
<section><h2>先看变化，再做判断</h2><p>候选版本有 <strong>{report["changes"]["regression"]}</strong> 条原先通过、现在未通过的问题，<strong>{report["changes"]["improvement"]}</strong> 条问题改善。总通过率上升，也不能抵消需要复核的退步案例。</p><p class="sub">候选失败类型：{esc(failures)}。一条回答可触发多个失败类型。</p></section>
<section><h2>输入日志的耗时、费用与人工评分</h2><table><thead><tr><th>指标</th><th>基线版本</th><th>候选版本</th></tr></thead><tbody>{metrics}</tbody></table><p class="sub">缺失值不按 0 计算；覆盖数不一致时不能直接认定效率或质量提升。</p></section>
<section><h2>逐条检查</h2><div class="scroll"><table class="cases"><thead><tr><th>样本</th><th>问题 / 规则</th><th>基线回答</th><th>候选回答</th><th>变化</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<footer>EvalOps Workbench · 本地生成，无外部脚本或统计追踪。参考答案供人工复核，不参与自动语义评分。</footer></main></body></html>'''


def write_report(report, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out / "report.html").write_text(render_html(report), encoding="utf-8")
    with (out / "case-results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "category", "question", "change", "baseline_passed", "candidate_passed", "candidate_reasons", "baseline_response", "candidate_response"])
        for row in report["items"]:
            values = [row["case"]["id"], row["case"]["category"], row["case"]["question"], row["change"], row["baseline"]["passed"], row["candidate"]["passed"], "|".join(row["candidate"]["reasons"]), row["baseline"]["response"], row["candidate"]["response"]]
            writer.writerow([safe_csv(value) for value in values])
    return out / "report.html"
