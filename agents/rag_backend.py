"""RAG 后端选择器。Milvus 为统一生产后端（稠密+稀疏混合检索+Reranker），FAISS 仅用于本地开发回退。"""
from __future__ import annotations

import os


def get_retriever():
    backend = os.getenv("RAG_BACKEND", "milvus").lower()
    if backend == "milvus":
        from agents.rag_milvus import get_milvus_retriever

        return get_milvus_retriever()
    if backend == "faiss":
        from agents.rag_hybrid import get_hybrid_retriever

        return get_hybrid_retriever()
    raise ValueError(f"不支持的 RAG_BACKEND: {backend}")


def build_index() -> int:
    backend = os.getenv("RAG_BACKEND", "milvus").lower()
    if backend == "milvus":
        from agents.rag_milvus import build_milvus_index

        return build_milvus_index()
    if backend == "faiss":
        from agents.rag_hybrid import build_persistent_index

        return build_persistent_index()
    raise ValueError(f"不支持的 RAG_BACKEND: {backend}")
