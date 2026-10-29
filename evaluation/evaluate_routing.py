"""路由准确率评测 — 复现简历口径「170 条固定标注集，76.5% → 91.2%」。

两种模式：
- hierarchical：真实调用 Manager 的 Plan 节点（Gateway → Manager 分层路由）
- baseline：单 Agent 全工具基线，用同一模型在「全部工具」里做选择
  （仓库没有单 Agent 版本，这里用等价的工具选择任务模拟，口径在 README 中说明）

用法：
    python evaluation/evaluate_routing.py --mode both
    python evaluation/evaluate_routing.py --mode hierarchical --limit 20 --concurrency 4
    python evaluation/evaluate_routing.py --mode baseline --report evaluation/routing_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))

from langchain_core.messages import HumanMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

DATASET = ROOT / "evaluation" / "routing_dataset_170.json"

# 工具 → 领域映射（基线模式把工具选择结果折算回领域，才能与分层路由同口径对比）
TOOL_TO_DOMAIN = {
    "get_order": "order",
    "check_cancelable": "order",
    "create_refund_draft": "order",
    "get_product_info": "product",
    "search_products": "product",
    "track_logistics": "logistics",
    "check_logistics_anomaly": "logistics",
    "estimate_delivery": "logistics",
    "list_user_coupons": "coupon",
    "best_coupon_for_order": "coupon",
    "get_active_promotions": "coupon",
    "search_policy": "policy",
}

BASELINE_PROMPT = """你是一个电商客服 Agent，可以直接调用下列全部工具。

工具清单：
- get_order：查询订单详情与状态
- check_cancelable：检查订单是否可取消
- create_refund_draft：生成退款草案
- get_product_info：查询商品参数、价格、库存
- search_products：按关键词搜索商品
- track_logistics：查询物流轨迹
- check_logistics_anomaly：检查物流异常
- estimate_delivery：预估送达时间
- list_user_coupons：查询用户优惠券
- best_coupon_for_order：计算订单最优优惠券
- get_active_promotions：查询当前促销活动
- search_policy：检索售后政策知识库

规则：
1. 只输出本次需要调用的工具名，不要输出参数；
2. 用户只是问候、感谢、闲聊时返回空列表；
3. 用户一句话涉及多个诉求时，把所有相关工具都列出来。

用户消息：{question}"""


class ToolPlan(BaseModel):
    """单 Agent 基线的工具选择结果。"""

    tools: list[str] = Field(default_factory=list, description="需要调用的工具名列表")


async def run_hierarchical(question: str) -> set[str]:
    """调用真实 Plan 节点，返回预测的领域集合。"""
    from manager_agent import _plan_node  # 延迟导入，避免基线模式也加载 Agent

    result = await _plan_node({"messages": [HumanMessage(content=question)]}, {})
    if "messages" in result:  # is_chat 分支
        return set()
    return {task["agent"] for task in result.get("plan", [])}


async def run_baseline(question: str, model) -> set[str]:
    """单 Agent 全工具基线：让模型在全部工具里选择，再折算成领域。"""
    structured = model.with_structured_output(ToolPlan)
    plan: ToolPlan = await structured.ainvoke(BASELINE_PROMPT.format(question=question))
    return {TOOL_TO_DOMAIN[t] for t in plan.tools if t in TOOL_TO_DOMAIN}


async def evaluate(mode: str, items: list[dict], concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    model = None
    if mode == "baseline":
        from model import get_routing_model

        model = get_routing_model()

    async def one(item: dict) -> dict:
        async with semaphore:
            expected = set(item.get("expected_domains", []))
            error = None
            try:
                if mode == "hierarchical":
                    predicted = await run_hierarchical(item["question"])
                else:
                    predicted = await run_baseline(item["question"], model)
            except Exception as exc:  # 单条失败不影响整体
                predicted, error = set(), str(exc)[:200]
            return {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "correct": predicted == expected,
                "error": error,
            }

    results = await asyncio.gather(*(one(item) for item in items))
    return summarize(mode, results)


def summarize(mode: str, results: list[dict]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    by_category = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        by_category[r["category"]]["total"] += 1
        if r["correct"]:
            by_category[r["category"]]["correct"] += 1

    errors = [r for r in results if r["error"]]
    mismatches = [r for r in results if not r["correct"]]

    return {
        "mode": mode,
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total * 100, 2) if total else 0.0,
        "call_errors": len(errors),
        "by_category": {
            k: {
                **v,
                "accuracy": round(v["correct"] / v["total"] * 100, 2) if v["total"] else 0.0,
            }
            for k, v in sorted(by_category.items())
        },
        "mismatches": mismatches[:20],
    }


def print_report(report: dict) -> None:
    print(f"\n=== 模式：{report['mode']} ===")
    print(f"样本数：{report['total']}　调用失败：{report['call_errors']}")
    print(f"路由准确率：{report['accuracy']}%　({report['correct']}/{report['total']})")
    print("分类明细：")
    for name, stat in report["by_category"].items():
        print(f"  {name:14s} {stat['correct']:3d}/{stat['total']:3d}  {stat['accuracy']:6.2f}%")
    if report["mismatches"]:
        print("错误样例（前 10 条）：")
        for item in report["mismatches"][:10]:
            print(f"  {item['id']} [{item['category']}] {item['question']}")
            print(f"      期望 {item['expected']} → 实际 {item['predicted']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="路由准确率评测")
    parser.add_argument("--mode", choices=["hierarchical", "baseline", "both"], default="both")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0 表示全量")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report", default="", help="把结果写入 JSON 报告文件")
    args = parser.parse_args()

    items = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.limit:
        items = items[: args.limit]

    modes = ["hierarchical", "baseline"] if args.mode == "both" else [args.mode]
    reports = []
    for mode in modes:
        report = await evaluate(mode, items, args.concurrency)
        print_report(report)
        reports.append(report)

    if args.report:
        Path(args.report).write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入：{args.report}")


if __name__ == "__main__":
    asyncio.run(main())