"""共享 BGE Reranker — Milvus 和 FAISS 检索路径共用的 CrossEncoder 精排模块。

设计意图：
- 单例懒加载：首次调用时加载 BGE-Reranker-v2-m3 模型（约 2GB），后续复用
- 环境开关：RAG_RERANKER_ENABLED 控制是否启用，默认关闭以降低开发环境资源占用
- 失败降级：加载失败时静默跳过（不影响主检索流程），仅记录警告日志
"""
from __future__ import annotations

import logging
import os

_reranker = None  # None=未加载, False=加载失败, CrossEncoder=可用


def get_reranker():
    """延迟加载 BGE Reranker-v2-m3 CrossEncoder，返回实例或 None。

    首次调用时加载模型（耗时取决于网络和硬件），后续调用直接返回缓存实例。
    受环境变量 RAG_RERANKER_ENABLED 控制（"1"/"true"/"yes" 启用）。
    """
    if os.getenv("RAG_RERANKER_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return None

    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder

            _reranker = CrossEncoder(
                "BAAI/bge-reranker-v2-m3",
                max_length=512,
            )
            logging.info("[Reranker] BAAI/bge-reranker-v2-m3 加载完成")
        except Exception as exc:
            logging.warning("[Reranker] 加载失败，将跳过重排序: %s", exc)
            _reranker = False

    return _reranker if _reranker is not False else None
