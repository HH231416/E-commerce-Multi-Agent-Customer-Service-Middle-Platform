"""RAG 检索质量评测：纯 Dense vs 混合检索（BM25 + Dense [+ Reranker]）。

用途：
- 复现简历/设计文档中的 Hit@3 / MRR 指标，验证"相较纯 Dense，混合检索
    Hit@3 由 72% 提升至 91%、MRR 由 0.65 提升至 0.88"可计算、可解释
- 判据：对每条评测问题，检索返回 top-3 片段，若某片段同时包含该问题
    标注的期望关键词，则视为命中；Hit@3 = 命中问题数 / 总数，
    MRR = 首个命中片段排名倒数的均值

用法：
    # 本地 FAISS 后端（无需 Milvus，自动构建临时索引）
    python evaluation/evaluate_rag.py --backend faiss

    # 生产 Milvus 后端（需已构建并发布知识版本）
    python evaluation/evaluate_rag.py --backend milvus

    # 指定评测集路径与临时索引目录
    python evaluation/evaluate_rag.py --dataset evaluation/rag_dataset.json \
        --index-dir data/rag_faiss_eval
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Iterable, Sequence

# 允许以 python evaluation/evaluate_rag.py 直接运行
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def load_dataset(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def _hit(doc_text: str, keywords: Sequence[str]) -> bool:
    """片段是否命中：包含该问题标注的全部期望关键词。

    归一化：去除所有空白字符后匹配，避免"5%违约金"与"5% 违约金"这类
    标注与文档的空格差异导致误判。
    """
    normalized = "".join(doc_text.split())
    return all("".join(kw.split()) in normalized for kw in keywords)


def evaluate_retriever(retriever, dataset: list[dict], top_k: int = 3) -> dict:
    """
    对评测集逐条检索，计算 Hit@top_k 与 MRR。

    返回：
    - hit@k: 命中率（float 0-1）
    - mrr: 平均倒数排名（float 0-1）
    - details: 每条问题的命中明细（供排查）
    - misses: 未命中列表（含期望关键词与实际 top-3 来源）
    """
    hits = 0
    reciprocal_sum = 0.0
    details = []
    misses = []

    for item in dataset:
        query = item["question"]
        keywords = item["expected_keywords"]
        docs = retriever.retrieve(query, top_k=10, final_k=top_k)

        hit_rank = None  # 1-based 首个命中片段排名
        for rank, doc in enumerate(docs, start=1):
            if _hit(doc.page_content, keywords):
                hit_rank = rank
                break

        if hit_rank is not None:
            hits += 1
            reciprocal_sum += 1.0 / hit_rank
        else:
            misses.append(
                {
                    "question": query,
                    "keywords": keywords,
                    "expected_source": item.get("expected_source", ""),
                    "top_sources": [d.metadata.get("filename", "?") for d in docs],
                    "top_preview": [d.page_content[:60] for d in docs],
                }
            )

        details.append(
            {
                "question": query,
                "hit": hit_rank is not None,
                "hit_rank": hit_rank,
                "top_sources": [d.metadata.get("filename", "?") for d in docs],
            }
        )

    n = len(dataset)
    return {
        "total": n,
        "hits": hits,
        "hit@k": round(hits / n, 4) if n else 0.0,
        "mrr": round(reciprocal_sum / n, 4) if n else 0.0,
        "details": details,
        "misses": misses,
    }


def make_faiss_retriever(index_dir: str, use_bm25: bool, use_reranker: bool):
    """在指定索引目录下构建/加载 FAISS 检索器（评测专用，不污染生产索引）。"""
    from agents.rag_hybrid import DATA_DIR, HybridRetriever, build_persistent_index

    if not os.path.exists(os.path.join(index_dir, "index.faiss")):
        # 临时构建 FAISS 索引（写入评测专用目录）
        os.environ["RAG_INDEX_DIR"] = index_dir
        build_persistent_index()
    else:
        os.environ["RAG_INDEX_DIR"] = index_dir

    retriever = HybridRetriever(use_bm25=use_bm25, use_reranker=use_reranker)
    retriever._ensure_initialized()
    return retriever


def make_milvus_retriever(use_bm25: bool, use_reranker: bool):
    """构造 Milvus 检索器（需已构建并发布知识版本）。"""
    from agents.rag_milvus import MilvusRetriever

    retriever = MilvusRetriever(use_bm25=use_bm25, use_reranker=use_reranker)
    retriever._ensure_ready()
    return retriever


def run_mode(
    label: str,
    retriever,
    dataset: list[dict],
    top_k: int = 3,
) -> dict:
    print(f"\n=== {label} ===")
    result = evaluate_retriever(retriever, dataset, top_k=top_k)
    print(f"样本数: {result['total']}  命中数: {result['hits']}")
    print(f"Hit@{top_k}: {result['hit@k']:.1%}   MRR: {result['mrr']:.4f}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测（Hit@3 / MRR）")
    parser.add_argument("--backend", choices=["faiss", "milvus"], default="faiss")
    parser.add_argument("--dataset", default=os.path.join(os.path.dirname(__file__), "rag_dataset.json"))
    parser.add_argument("--index-dir", default=os.path.join(os.path.dirname(__file__), "../data/rag_faiss_eval"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json-out", default=None, help="输出 JSON 汇总到指定文件")
    parser.add_argument(
        "--show-misses", action="store_true", help="打印未命中的问题及实际 top-3"
    )
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    print(f"评测集: {args.dataset}（{len(dataset)} 条，后端={args.backend}）")

    results = {}
    if args.backend == "faiss":
        # 纯 Dense 基线：关闭 BM25 与 Reranker
        dense = make_faiss_retriever(args.index_dir, use_bm25=False, use_reranker=False)
        results["dense_only"] = run_mode(
            "纯 Dense（无 BM25 / 无 Reranker）", dense, dataset, args.top_k
        )

        # 混合检索：开启 BM25；Reranker 依赖 sentence_transformers，未安装则自动跳过
        hybrid = make_faiss_retriever(args.index_dir, use_bm25=True, use_reranker=True)
        results["hybrid"] = run_mode("混合检索（BM25 + Dense）", hybrid, dataset, args.top_k)
    else:
        dense = make_milvus_retriever(use_bm25=False, use_reranker=False)
        results["dense_only"] = run_mode("纯 Dense（无 BM25 / 无 Reranker）", dense, dataset, args.top_k)
        hybrid = make_milvus_retriever(use_bm25=True, use_reranker=True)
        results["hybrid"] = run_mode("混合检索（BM25 + Dense）", hybrid, dataset, args.top_k)

    d = results["dense_only"]
    h = results["hybrid"]
    print("\n================ 对比汇总 ================")
    print(f"{'指标':<10}{'纯 Dense':>10}{'混合':>10}{'提升':>12}")
    print(f"{'Hit@' + str(args.top_k):<10}{d['hit@k']:>10.1%}{h['hit@k']:>10.1%}"
          f"{(h['hit@k'] - d['hit@k']):>+12.1%}")
    print(f"{'MRR':<10}{d['mrr']:>10.4f}{h['mrr']:>10.4f}"
          f"{(h['mrr'] - d['mrr']):>+12.4f}")

    if args.show_misses:
        for mode, r in results.items():
            print(f"\n--- {mode} 未命中 {len(r['misses'])} 条 ---")
            for m in r["misses"]:
                print(f"  Q: {m['question']}")
                print(f"    期望关键词: {m['keywords']} | 期望来源: {m['expected_source']}")
                print(f"    实际 top-3 来源: {m['top_sources']}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n汇总已写入: {args.json_out}")


if __name__ == "__main__":
    main()
