"""Self-contained, escaped results page; no import-time reads or network calls.

Accepts collector records, arm_results, or historical response replay. Execution,
deterministic validation, model support and semantic trust remain separate.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

SCHEMA = "RAG2_results_page_v2"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json(value: Any) -> str:
    return esc(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def rows_from_data(data: Any) -> list[Any]:
    """Accept an arm_results list, a replay object, or one collector record."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("cases", "rows", "results"):
            if key in data:
                if not isinstance(data[key], list):
                    raise ValueError(f"results_{key}_must_be_a_list")
                return data[key]
        return [data]
    raise ValueError("results_must_be_an_object_or_list")


def semantic_report(row: dict) -> dict:
    validation = _dict(row.get("validation_report"))
    for item in (row.get("semantic_validation"), row.get("semantic_report"),
                 validation.get("semantic_validation"), validation.get("semantic_report")):
        if isinstance(item, dict):
            return item
    return validation if validation.get("schema") == "RAG2_semantic_validation_v1" else {}


def _trust(row: dict, report: dict) -> tuple[str, str]:
    validation = _dict(row.get("validation_report"))
    values = [source.get(key) for source in (row, validation, report)
              for key in ('whole_answer_trust', 'answer_trust') if source.get(key) is not None]
    # The composite report includes structure/location/reference checks absent
    # from the semantic subreport. A child cannot downgrade a parent's block.
    if "blocked" in values:
        return "blocked", "存在阻断问题"
    if "not_evaluated" in values:
        return "not_evaluated", "未评估"
    if "review_required" in values:
        return "review_required", "待语义复核"
    if any(value in ('verified', 'trusted', 'confirmed') for value in values):
        review = _dict(row.get("independent_semantic_review"))
        if review.get("status") == "approved" and review.get("reference"):
            return "verified", "已附独立语义复核记录"
        return "review_required", "可信标记缺复核依据"
    return "not_evaluated", "未评估"


def _check_value(row: dict, report: dict, key: str):
    for source in (row, _dict(row.get('validation_report')), report):
        if isinstance(source.get(key), bool):
            return source[key]
    return None


def _findings(row: dict, report: dict) -> list:
    validation = _dict(row.get('validation_report'))
    result, seen = [], set()
    for item in (_list(validation.get('risk_findings')) + _list(validation.get('schema_errors'))
                 + _list(report.get('findings')) + _list(row.get('new_blocking'))):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def comparison_metrics(rows: list[Any]) -> dict:
    """Comparable gate counts; missing outcomes are not counted as passes.

    Duplicate case identifiers remain separate observations and are reported.
    These are gate agreement metrics, never answer accuracy/model improvement.
    """
    old, new, seen, duplicates = {}, {}, set(), []
    for index, item in enumerate(rows):
        row = _dict(item)
        cid = str(row.get("case_id", f"row-{index + 1}"))
        if cid in seen:
            duplicates.append(cid)
        seen.add(cid)
        key = f"{index}:{cid}"
        if isinstance(row.get("old_mechanical_passed"), bool):
            old[key] = row["old_mechanical_passed"]
        if isinstance(row.get("new_passed"), bool):
            new[key] = row["new_passed"]
    common = set(old) & set(new)
    return {
        "observations": len(rows),
        "old_gate": {"passed": sum(old.values()), "denominator": len(old)},
        "new_gate": {"passed": sum(new.values()), "denominator": len(new)},
        "comparable_denominator": len(common),
        "both_gate_passed": sum(old[k] and new[k] for k in common),
        "newly_blocked": sum(old[k] and not new[k] for k in common),
        "newly_allowed": sum(not old[k] and new[k] for k in common),
        "old_outcome_missing": len(rows) - len(old),
        "new_outcome_missing": len(rows) - len(new),
        "duplicate_case_ids": sorted(set(duplicates)),
        "interpretation": "gate_agreement_not_answer_accuracy_or_model_improvement",
    }


def _boolean(value: Any) -> str:
    return "通过" if value is True else "未通过" if value is False else "未评估"


def render_results(data: Any, title: str = "RAG 2.0 · 问答审查结果") -> str:
    rows = rows_from_data(data)
    envelope = _dict(data)
    metrics = comparison_metrics(rows)
    counts = {"blocked": 0, "review_required": 0, "verified": 0, "not_evaluated": 0}
    contents = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            contents.append(f'<article><h2>记录 {index + 1}</h2><p class="bad">记录格式错误，未评估。</p><pre>{_json(item)}</pre></article>')
            counts["not_evaluated"] += 1
            continue
        row = item
        report = semantic_report(row)
        trust, label = _trust(row, report)
        counts[trust] += 1
        obj = next((row[k] for k in ("answer_object", "model_assertions", "canonical_object") if isinstance(row.get(k), dict)), {})
        answer = obj.get("answer", row.get("answer"))
        question = row.get("question", _dict(row.get("input_projection")).get("question"))
        support = obj.get("answer_support", row.get("answer_support", "未记录"))
        technical = row.get("technical_status", row.get("status", row.get("error_class", "未记录")))
        findings = _findings(row, report)
        issues = ''.join('<li><code>' + esc(_dict(f).get("code", "未命名问题")) + '</code> '
                         + esc(_dict(f).get("detail", _dict(f).get("reason", ""))) + ' <span class="muted">'
                         + esc(_dict(f).get("locus", "")) + '</span></li>' for f in findings)
        if not issues:
            issues = '<li class="muted">未附问题列表；这不等同于整答正确。</li>'
        quotes = []
        for statement in _list(obj.get("source_statement")):
            s = _dict(statement)
            quotes.append('<li><code>' + esc(s.get("id", "")) + '</code> · 来源 '
                          + esc(s.get("source_id", "未记录")) + ' · ' + esc(s.get("source_state", ""))
                          + '<blockquote>' + esc(s.get("quote", "")) + '</blockquote></li>')
        history = {key: row[key] for key in ("old_status", "historical_status", "historical_technical_status", "old_mechanical_passed", "old_semantic", "score", "historical_score", "old_codes", "new_passed") if key in row}
        history_html = ('<details><summary>历史状态与评分（保留原值）</summary><pre>' + _json(history) + '</pre></details>') if history else ''
        contents.append(f'''<article id="record-{index + 1}"><div class="heading"><h2>{esc(row.get('case_id', f'记录 {index + 1}'))}</h2><span class="badge {trust}">{esc(label)}</span></div>
<p class="question">{esc(question) if question is not None else '问题未随此记录提供'}</p>
<dl><dt>执行 / 技术状态</dt><dd>{esc(technical)}</dd><dt>模型自述证据支持</dt><dd>{esc(support)}</dd>
<dt>结构 / 引用等确定性检查</dt><dd>{_boolean(_check_value(row, report, 'mechanical_valid'))}</dd>
<dt>自由回答检查</dt><dd>{_boolean(_check_value(row, report, 'narrative_valid'))}（仅限已实现规则）</dd></dl>
<h3>原回答</h3><p class="answer">{esc(answer) if answer is not None else '尚无回答'}</p>
<h3>检查发现</h3><ul>{issues}</ul><details><summary>引用和原文状态（{len(quotes)} 条）</summary><ol>{''.join(quotes) or '<li>未附引用。</li>'}</ol></details>
<details><summary>结构化主张</summary><pre>{_json(obj.get('claims', []))}</pre></details>{history_html}
<details><summary>完整输入记录（原值展示）</summary><pre>{_json(item)}</pre></details></article>''')
    gate_html = ''
    if metrics['old_gate']['denominator'] or metrics['new_gate']['denominator']:
        gate_html = f'''<section><h2>门禁对照</h2><p>旧门禁 {metrics['old_gate']['passed']}/{metrics['old_gate']['denominator']}；新门禁 {metrics['new_gate']['passed']}/{metrics['new_gate']['denominator']}；可比记录交集通过 {metrics['both_gate_passed']}/{metrics['comparable_denominator']}。
新放行 {metrics['newly_allowed']}，新阻断 {metrics['newly_blocked']}。交集反映一致性，不能代替准确率、覆盖率或其他声明清楚的指标。</p><details><summary>分母及缺失项</summary><pre>{_json(metrics)}</pre></details></section>'''
    calls = envelope.get('new_model_calls', envelope.get('model_calls'))
    calls_text = f'本次记录声明的新增模型调用：{esc(calls)}。' if calls is not None else '新增模型调用数未随此文件提供。'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(title)}</title><style>
:root{{--bg:#f4f6fa;--panel:#fff;--text:#182337;--muted:#58667a;--line:#d7e0eb;--accent:#2457a6}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1120px;margin:auto;padding:36px 24px 60px}}
h1{{font-size:28px;line-height:1.3}}h2{{font-size:19px;margin:0 0 12px}}h3{{font-size:15px;margin:20px 0 8px}}
header,section,article{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 26px;margin-bottom:18px}}header{{border-top:5px solid var(--accent)}}
.muted,dt{{color:var(--muted)}}.notice{{border-left:4px solid var(--accent);padding:8px 14px;background:#eef4ff}}.summary{{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}}
.summary span{{border:1px solid var(--line);padding:7px 12px;border-radius:7px}}.heading{{display:flex;align-items:start;justify-content:space-between;gap:12px}}
.badge{{font-size:13px;border-radius:6px;padding:3px 9px;background:#edf2f7}}.blocked,.bad{{color:#a92330}}.review_required{{color:#805800}}.verified{{color:#116a4f}}
.question{{font-weight:600}}.answer{{white-space:pre-wrap;background:#f6f8fc;padding:16px;border-radius:7px}}dl{{display:grid;grid-template-columns:220px 1fr;gap:5px 15px;font-size:13px}}dd{{margin:0}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:500px;overflow:auto;background:#f6f8fc;padding:14px}}details{{border-top:1px solid var(--line);padding:10px 0}}
summary{{cursor:pointer;color:var(--accent)}}blockquote{{margin:8px 0 16px;padding-left:14px;border-left:3px solid var(--line);white-space:pre-wrap}}code{{overflow-wrap:anywhere}}footer{{font-size:12px;color:var(--muted)}}
@media(max-width:620px){{main{{padding:16px 12px}}header,section,article{{padding:16px}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}}}
</style></head><body><main><header><h1>{esc(title)}</h1><p class="muted">数据模式：{esc(envelope.get('mode', 'provided_records'))}。{calls_text}</p>
<p class="notice">执行完成、格式合法、引用匹配与整答正确分别记录。模型自述的 evidence_supported 也不是独立审查结论。旧响应回放只说明检查器行为，不能称为新模型提升。</p>
<div class="summary"><span>记录 {len(rows)}</span><span>阻断 {counts['blocked']}</span><span>待复核 {counts['review_required']}</span><span>未评估 {counts['not_evaluated']}</span><span>附独立复核记录 {counts['verified']}</span></div></header>
{gate_html}{''.join(contents)}<footer>页面由 {SCHEMA} 生成，仅展示提供的数据；未凭路径推断本机验证、全量运行或冻结完整性。</footer></main></body></html>'''


def write_results_page(data: Any, output: str | Path, title: str = "RAG 2.0 · 问答审查结果") -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_results(data, title), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='collector/arm_results/replay JSON')
    parser.add_argument('--output', type=Path, required=True, help='offline HTML file')
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error('input and output must differ; historical inputs are read-only')
    data = json.loads(args.input.read_text(encoding='utf-8'))
    path = write_results_page(data, args.output)
    print(json.dumps({'output': str(path), 'rows': len(rows_from_data(data))}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
