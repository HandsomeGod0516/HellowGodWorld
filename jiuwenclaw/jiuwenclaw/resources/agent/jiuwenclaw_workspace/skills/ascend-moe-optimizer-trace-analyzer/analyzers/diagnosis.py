from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AutoDiagnosisInput:
    overview: dict
    phase_summary: List[dict]
    category_summary: List[dict]
    core_group_summary: List[dict]
    phase_core_group_summary: List[dict]
    category_core_group_summary: List[dict]
    name_summary: List[dict]
    overlap_summary: List[dict]
    bubble_summary: List[dict]


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _find_row(rows: List[dict], key: str, value: str) -> Optional[dict]:
    for row in rows:
        if row.get(key) == value:
            return row
    return None


def _top_non_container_phase(phase_summary: List[dict]) -> Optional[dict]:
    top = None
    for row in phase_summary:
        if row.get("category") != "container" and row.get("phase") != "processing":
            top = row
            break
    if top is None:
        return None

    for row in phase_summary:
        if row.get("category") == "wait" and _num(row.get("union_us")) >= _num(top.get("union_us")) * 0.8:
            return row
    return top


def _top_category(category_summary: List[dict]) -> Optional[dict]:
    wait_row = _find_row(category_summary, "category", "wait")
    if wait_row and _num(wait_row.get("ratio_to_total_wall")) >= 0.2:
        return wait_row

    for row in category_summary:
        if row.get("category") not in {"container", "other"}:
            return row
    return None


def _pair_overlap(overlap_summary: List[dict], phase_a: str, phase_b: str) -> Optional[dict]:
    for row in overlap_summary:
        pair = {row.get("phase_a"), row.get("phase_b")}
        if pair == {phase_a, phase_b}:
            return row
    return None


def _top_names_by_category(name_summary: List[dict], category: str, limit: int = 3) -> List[dict]:
    return [row for row in name_summary if row.get("category") == category][:limit]


def _top_rows_by_category_core_group(rows: List[dict], category: str, limit: int = 3) -> List[dict]:
    matched = [row for row in rows if row.get("category") == category]
    matched.sort(key=lambda row: (_num(row.get("union_us")), _num(row.get("total_us"))), reverse=True)
    return matched[:limit]


def _fmt_us(value: Any) -> str:
    return f"{_num(value):.3f} us"


def _fmt_ratio(value: Any) -> str:
    return f"{_num(value) * 100:.1f}%"


def build_auto_diagnosis(inp: AutoDiagnosisInput) -> dict:
    overview = inp.overview
    phase_summary = inp.phase_summary
    category_summary = inp.category_summary
    core_group_summary = inp.core_group_summary
    category_core_group_summary = inp.category_core_group_summary
    name_summary = inp.name_summary
    overlap_summary = inp.overlap_summary
    bubble_summary = inp.bubble_summary
    total_wall = _num(overview.get("total_wall_us"))
    findings: List[Dict[str, str]] = []

    top_phase = _top_non_container_phase(phase_summary)
    if top_phase:
        findings.append(
            {
                "severity": "high" if _num(top_phase.get("ratio_to_total_wall")) >= 0.5 else "medium",
                "title": f"主耗時階段是 {top_phase.get('phase')}",
                "detail": (
                    f"union={_fmt_us(top_phase.get('union_us'))}, "
                    f"佔 wall time {_fmt_ratio(top_phase.get('ratio_to_total_wall'))}, "
                    f"category={top_phase.get('category')}."
                ),
                "recommendation": "優先檢查該階段內部的 top raw names 和同 category 的等待/計運算元階段。",
            }
        )

    top_category = _top_category(category_summary)
    if top_category:
        category = top_category.get("category")
        finding = {
            "severity": "high" if _num(top_category.get("ratio_to_total_wall")) >= 0.5 else "medium",
            "title": f"瓶頸型別傾向於 {category}",
            "detail": (
                f"{category} union={_fmt_us(top_category.get('union_us'))}, "
                f"佔 wall time {_fmt_ratio(top_category.get('ratio_to_total_wall'))}."
            ),
            "recommendation": "若該型別是 wait/sync，優先看跨核訊號與通訊；若是 compute/epilogue，優先看矩陣維度、分塊和核間負載。",
        }
        findings.append(finding)

    wait_category = _find_row(category_summary, "category", "wait")
    if wait_category and _num(wait_category.get("ratio_to_total_wall")) >= 0.1:
        top_wait_names = _top_names_by_category(name_summary, "wait", 3)
        wait_names = ", ".join(str(row.get("normalized_name") or row.get("name")) for row in top_wait_names)
        findings.append(
            {
                "severity": "high" if _num(wait_category.get("ratio_to_total_wall")) >= 0.4 else "medium",
                "title": "存在顯著等待開銷",
                "detail": (
                    f"wait union={_fmt_us(wait_category.get('union_us'))}, "
                    f"佔 wall time {_fmt_ratio(wait_category.get('ratio_to_total_wall'))}; "
                    f"top wait={wait_names or 'N/A'}."
                ),
                "recommendation": "重點排查 token ready、combine status、shared expert 同步和 AIC/AIV pipeline 依賴。",
            }
        )

    wait_by_core = _top_rows_by_category_core_group(category_core_group_summary, "wait", 3)
    if wait_by_core:
        top_wait_core = wait_by_core[0]
        details = ", ".join(
            f"{row.get('core_group')}={_fmt_us(row.get('union_us'))}"
            for row in wait_by_core
        )
        findings.append(
            {
                "severity": "high" if _num(top_wait_core.get("ratio_to_total_wall")) >= 0.4 else "medium",
                "title": f"等待主要出現在 {top_wait_core.get('core_group')} 核組",
                "detail": (
                    f"{details}; "
                    f"{top_wait_core.get('core_group')} 內 wait 覆蓋該核組 "
                    f"{_fmt_ratio(top_wait_core.get('ratio_to_core_group_wall'))}."
                ),
                "recommendation": (
                    "分別檢視 phase_core_group_summary.csv，確認是 cube 等 token，"
                    "還是 vector_recv/vector_send 在等待 combine/status。"
                ),
            }
        )

    observed_groups = {
        row.get("core_group") for row in core_group_summary
        if row.get("core_group") not in {None, "", "unknown"}
    }
    if observed_groups and int(overview.get("num_tids", 0)) >= 24:
        for core_group in ("cube", "vector_recv", "vector_send"):
            group_row = _find_row(core_group_summary, "core_group", core_group)
            if group_row is None:
                findings.append(
                    {
                        "severity": "medium",
                        "title": f"未觀察到 {core_group} 核組事件",
                        "detail": f"trace 中沒有 {core_group} 對應的已對映 phase instance。",
                        "recommendation": "確認 trace_collector 是否按 1C2V 模式拆分，或者 profiling tensor 是否缺少該核組資料。",
                    }
                )

    dispatch_gmm2 = _pair_overlap(overlap_summary, "dispatch_gmm1", "gmm2_combine")
    if dispatch_gmm2:
        overlap_min = _num(dispatch_gmm2.get("overlap_ratio_min"))
        if overlap_min < 0.2:
            severity = "high"
            title = "dispatch_gmm1 與 gmm2_combine 基本序列"
            action = "檢查是否缺少跨階段流水覆蓋，或 gmm2 是否被 gmm1 的 token/quant 輸出長時間阻塞。"
        elif overlap_min < 0.5:
            severity = "medium"
            title = "dispatch_gmm1 與 gmm2_combine 流水覆蓋偏弱"
            action = "優先檢查 gmm2 wait quant、combine wait status、gmm1 quant/sync 是否限制後半段啟動。"
        else:
            severity = "info"
            title = "dispatch_gmm1 與 gmm2_combine 有一定流水覆蓋"
            action = "繼續看各自內部 wait 和 epilogue 是否佔主導。"
        findings.append(
            {
                "severity": severity,
                "title": title,
                "detail": (
                    f"overlap={_fmt_us(dispatch_gmm2.get('overlap_us'))}, "
                    f"相對較短階段覆蓋 {_fmt_ratio(overlap_min)}."
                ),
                "recommendation": action,
            }
        )

    if bubble_summary:
        top_bubble = bubble_summary[0]
        if _num(top_bubble.get("bubble_ratio")) >= 0.05:
            findings.append(
                {
                    "severity": "medium",
                    "title": f"{top_bubble.get('parent_phase')} 記憶體在未被子階段覆蓋的空洞",
                    "detail": (
                        f"bubble={_fmt_us(top_bubble.get('bubble_us'))}, "
                        f"佔 parent {_fmt_ratio(top_bubble.get('bubble_ratio'))}, "
                        f"max_gap={_fmt_us(top_bubble.get('max_gap_us'))}."
                    ),
                    "recommendation": "結合低層 leaf trace 檢視這段空洞是否來自未打點程式碼、跨核等待，還是 trace depth 過濾後的可見性缺口。",
                }
            )

    if not findings:
        findings.append(
            {
                "severity": "info",
                "title": "未發現明顯單點瓶頸",
                "detail": f"已解析 {overview.get('num_instances', 0)} 個 phase instance，total wall={_fmt_us(total_wall)}.",
                "recommendation": "繼續結合 phase_tid_summary.csv 檢查不同 core 的長尾差異。",
            }
        )

    high_or_medium = [finding for finding in findings if finding.get("severity") in {"high", "medium"}]
    headline = "；".join(finding["title"] for finding in high_or_medium[:2])
    if not headline:
        headline = findings[0]["title"]

    return {
        "headline": headline,
        "total_wall_us": total_wall,
        "bottleneck_phase": top_phase,
        "bottleneck_category": top_category,
        "core_groups": core_group_summary,
        "wait_by_core_group": wait_by_core,
        "findings": findings[:6],
    }
