"""Milvus 混合检索后端：稠密向量 + BM25 稀疏向量 + BGE Reranker 精排。

设计意图：
- 统一后端：以 Milvus 为唯一生产检索后端，FAISS 仅作本地开发回退
- 混合检索：稠密向量负责语义泛化（"怎么退款"→"退款流程"），BM25 稀疏向量负责精确术语匹配（"7天无理由"）
- 精排增强：BGE Reranker 作为 CrossEncoder 对候选集二次打分，提升 top-3 精度
- 版本隔离：知识版本以独立 Collection 存储，BM25 模型同步版本化
- 降级策略：BM25 不可用 → 纯稠密；hybrid_search 异常 → 纯稠密；Reranker 不可用 → 跳过
"""
from __future__ import annotations

import logging
import os
import pickle
import re
from typing import List

import numpy as np
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from agents.rag_reranker import get_reranker

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "../RAG_data")
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "customer_service_knowledge")
KNOWLEDGE_VERSION = os.getenv("KNOWLEDGE_VERSION", "unversioned")
BM25_MODEL_DIR = os.getenv(
    "BM25_MODEL_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/bm25")),
)

# hybrid_search 稠密/稀疏融合权重（可通过环境变量覆盖）
_weights = os.getenv("RAG_HYBRID_WEIGHTS", "0.7,0.3")
DENSE_WEIGHT, SPARSE_WEIGHT = (float(w) for w in _weights.split(",", 1))


def _collection_name() -> str:
    version = re.sub(r"[^A-Za-z0-9_]", "_", KNOWLEDGE_VERSION)
    return f"{MILVUS_COLLECTION}_{version}"[:240]


def _client():
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RuntimeError("Milvus 后端未安装 pymilvus") from exc
    return MilvusClient(uri=MILVUS_URI)


# ---------------------------------------------------------------------------
# BM25 稀疏向量编码器（基于 jieba + rank_bm25）
# ---------------------------------------------------------------------------
class BM25SparseEncoder:
    """中文 BM25 稀疏向量编码器：分词 → 拟合 → 编码 → 序列化。

    稀疏向量格式：{term_index: weight} dict，L2 归一化后存入 Milvus SPARSE_FLOAT_VECTOR。
    """

    def __init__(self):
        self.vocabulary: dict[str, int] = {}   # term → index
        self.idf: dict[str, float] = {}        # term → idf
        self._fitted = False

    # ---- 公开 API ----------------------------------------------------------

    def fit(self, corpus: list[str]) -> None:
        """在中文语料上拟合 BM25，构建词汇表和 IDF。"""
        import jieba
        from rank_bm25 import BM25Okapi

        tokenized = [jieba.lcut(text) for text in corpus]
        bm25 = BM25Okapi(tokenized)
        # 提取 IDF（BM25Okapi 拟合后存储在实例的 idf 属性中）
        self.idf = dict(bm25.idf) if isinstance(bm25.idf, dict) else {}
        # 构建词汇表
        self.vocabulary = {}
        for tokens in tokenized:
            for t in tokens:
                if t not in self.vocabulary:
                    self.vocabulary[t] = len(self.vocabulary)
        self._fitted = True
        logging.info("[BM25] 拟合完成，词汇表大小=%s", len(self.vocabulary))

    def encode_documents(self, texts: list[str]) -> list[dict]:
        """将文档列表编码为稀疏向量列表（dict 格式，供 Milvus insert 使用）。"""
        self._ensure_fitted()
        return [self._encode_one(text) for text in texts]

    def encode_queries(self, queries: list[str]) -> list[dict]:
        """将查询列表编码为稀疏向量列表（dict 格式，供 AnnSearchRequest 使用）。"""
        self._ensure_fitted()
        return [self._encode_one(q) for q in queries]

    def save(self, path: str) -> None:
        """将词汇表和 IDF 序列化到磁盘。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"vocabulary": self.vocabulary, "idf": self.idf}, f)
        logging.info("[BM25] 模型已保存: %s", path)

    def load(self, path: str) -> None:
        """从磁盘加载词汇表和 IDF。"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"BM25 模型文件不存在: {path}")
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.vocabulary = data["vocabulary"]
        self.idf = data["idf"]
        self._fitted = True
        logging.info("[BM25] 模型已加载: %s (词汇表=%s)", path, len(self.vocabulary))

    # ---- 内部方法 ----------------------------------------------------------

    def _ensure_fitted(self) -> None:
        if not self._fitted:
            raise RuntimeError("BM25SparseEncoder 尚未拟合或加载模型")

    def _encode_one(self, text: str) -> dict:
        import jieba

        tokens = jieba.lcut(text)
        vec: dict[int, float] = {}
        # 计算 TF（词频统计）
        tf_map: dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        # TF × IDF → 稀疏向量
        for term, tf in tf_map.items():
            idx = self.vocabulary.get(term)
            if idx is None:
                continue
            vec[idx] = float(tf) * self.idf.get(term, 0.0)
        # L2 归一化
        norm = np.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        return vec


def _bm25_model_path() -> str:
    return os.path.join(BM25_MODEL_DIR, f"{_collection_name()}.pkl")


def _load_bm25_encoder() -> BM25SparseEncoder | None:
    """加载与当前知识版本对应的 BM25 模型，不存在时返回 None。"""
    path = _bm25_model_path()
    if not os.path.exists(path):
        logging.info("[BM25] 模型未找到 (%s)，降级为纯稠密检索", path)
        return None
    try:
        encoder = BM25SparseEncoder()
        encoder.load(path)
        return encoder
    except Exception as exc:
        logging.warning("[BM25] 加载失败 (%s)，降级为纯稠密检索: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Milvus 检索器
# ---------------------------------------------------------------------------
class MilvusRetriever:
    """Milvus 混合检索器：dense + sparse hybrid_search → Reranker 精排。"""

    def __init__(self) -> None:
        self.client = None
        self.embeddings = None
        self.bm25: BM25SparseEncoder | None = None

    def _ensure_ready(self) -> None:
        if self.client is None:
            self.client = _client()
        if self.embeddings is None:
            try:
                from agents.model import get_embeddings
            except ImportError:
                from model import get_embeddings
            self.embeddings = get_embeddings()
        if not self.client.has_collection(_collection_name()):
            raise RuntimeError(f"Milvus 索引不存在: {_collection_name()}，请先构建并发布知识版本")
        self.client.load_collection(_collection_name())
        # 延迟加载 BM25（失败时降级为纯稠密）
        if self.bm25 is None:
            self.bm25 = _load_bm25_encoder()

    # ---- 检索入口 ----------------------------------------------------------

    def retrieve(self, query: str, top_k: int = 10, final_k: int = 3) -> List[Document]:
        """执行混合检索：dense + sparse → hybrid_search → Reranker → top final_k。

        top_k：双路召回总候选数（默认 10，供 Reranker 精排用）
        final_k：最终返回文档数（默认 3）
        """
        self._ensure_ready()
        dense_vec = self.embeddings.embed_query(query)

        # 生成稀疏向量（BM25 不可用时降级）
        sparse_vec = None
        if self.bm25 is not None:
            try:
                sparse_vec = self.bm25.encode_queries([query])[0]
            except Exception as exc:
                logging.warning("[Milvus] 稀疏编码失败，降级为纯稠密: %s", exc)

        # 检索
        results = self._search(dense_vec, sparse_vec, top_k)

        # 转换结果
        documents = self._hits_to_docs(results)
        if not documents:
            return []

        # Reranker 精排
        reranker = get_reranker()
        if reranker and len(documents) > 1:
            pairs = [(query, doc.page_content[:512]) for doc in documents]
            try:
                scores = reranker.predict(pairs, show_progress_bar=False)
                scored = list(zip(documents, scores))
                scored.sort(key=lambda x: x[1], reverse=True)
                documents = [doc for doc, _ in scored]
            except Exception as exc:
                logging.warning("[Milvus] Reranker 失败，使用原始排序: %s", exc)

        return documents[:final_k]

    # ---- 内部方法 ----------------------------------------------------------

    def _search(self, dense_vec: list[float], sparse_vec: dict | None, top_k: int):
        """执行 hybrid_search（或降级为纯稠密搜索）。"""
        output_fields = ["text", "source", "filename", "document_id", "knowledge_version"]

        if sparse_vec is not None:
            try:
                from pymilvus import AnnSearchRequest, WeightedRanker

                dense_req = AnnSearchRequest(
                    data=[dense_vec],
                    anns_field="dense_vector",
                    param={"metric_type": "COSINE", "params": {}},
                    limit=top_k,
                )
                sparse_req = AnnSearchRequest(
                    data=[sparse_vec],
                    anns_field="sparse_vector",
                    param={"metric_type": "IP", "params": {}},
                    limit=top_k,
                )
                ranker = WeightedRanker(DENSE_WEIGHT, SPARSE_WEIGHT)
                return self.client.hybrid_search(
                    collection_name=_collection_name(),
                    reqs=[dense_req, sparse_req],
                    ranker=ranker,
                    limit=top_k,
                    output_fields=output_fields,
                )
            except Exception as exc:
                logging.warning("[Milvus] hybrid_search 失败，降级为纯稠密: %s", exc)
                # 继续走纯稠密

        return self.client.search(
            collection_name=_collection_name(),
            data=[dense_vec],
            anns_field="dense_vector",
            limit=top_k,
            output_fields=output_fields,
            search_params={"metric_type": "COSINE", "params": {}},
        )

    @staticmethod
    def _hits_to_docs(results) -> list[Document]:
        documents = []
        for hit in results[0] if results else []:
            entity = hit.get("entity", hit)
            text = entity.get("text")
            if not text:
                continue
            documents.append(Document(
                page_content=text,
                metadata={
                    "source": entity.get("source", "未知"),
                    "filename": entity.get("filename", "未知"),
                    "document_id": entity.get("document_id", "未知"),
                    "knowledge_version": entity.get("knowledge_version", KNOWLEDGE_VERSION),
                    "score": hit.get("distance"),
                },
            ))
        return documents


# ---------------------------------------------------------------------------
# 索引构建
# ---------------------------------------------------------------------------
def build_milvus_index() -> int:
    """构建并发布一个不可变知识版本的 Milvus 混合索引。

    流程：
    1. 加载并分割文档
    2. 生成稠密向量（Embedding）
    3. 拟合 BM25 并生成稀疏向量
    4. 创建 Collection（dense_vector + sparse_vector 双字段）
    5. 插入数据并建立索引
    6. 持久化 BM25 模型
    """
    # 加载文档
    try:
        from agents.rag_hybrid import _load_documents
    except ImportError:
        from rag_hybrid import _load_documents

    documents = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50).split_documents(
        _load_documents(DATA_DIR)
    )
    if not documents:
        raise RuntimeError("没有可用于构建 Milvus 索引的知识文档")
    corpus_texts = [doc.page_content for doc in documents]

    # 生成稠密向量
    try:
        from agents.model import get_embeddings
    except ImportError:
        from model import get_embeddings
    embeddings = get_embeddings()
    dense_vectors = embeddings.embed_documents(corpus_texts)

    # 拟合 BM25 并生成稀疏向量
    bm25_encoder = BM25SparseEncoder()
    try:
        bm25_encoder.fit(corpus_texts)
        sparse_vectors = bm25_encoder.encode_documents(corpus_texts)
        bm25_encoder.save(_bm25_model_path())
    except Exception as exc:
        logging.warning("[Milvus] BM25 拟合失败，仅使用稠密向量: %s", exc)
        bm25_encoder = None

    # 建库
    client = _client()
    collection = _collection_name()
    if client.has_collection(collection):
        raise RuntimeError(
            f"Milvus 知识版本已存在: {collection}；请提升 KNOWLEDGE_VERSION 后再发布"
        )

    from pymilvus import DataType

    dim = len(dense_vectors[0])
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("text", DataType.VARCHAR, max_length=4096)
    schema.add_field("source", DataType.VARCHAR, max_length=2048)
    schema.add_field("filename", DataType.VARCHAR, max_length=512)
    schema.add_field("document_id", DataType.VARCHAR, max_length=128)
    schema.add_field("knowledge_version", DataType.VARCHAR, max_length=128)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(
        field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP"
    )
    client.create_collection(collection_name=collection, schema=schema, index_params=index_params)

    # 插入数据
    import hashlib

    records = []
    for i, (doc, dense_vec) in enumerate(zip(documents, dense_vectors), start=1):
        source = doc.metadata.get("source", "未知")
        record: dict = {
            "id": i,
            "dense_vector": dense_vec,
            "text": doc.page_content[:4096],
            "source": source[:2048],
            "filename": doc.metadata.get("filename", os.path.basename(source)),
            "document_id": doc.metadata.get(
                "document_id",
                hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()[:16],
            ),
            "knowledge_version": KNOWLEDGE_VERSION[:128],
        }
        # 只在 BM25 成功拟合时写入稀疏向量
        if bm25_encoder is not None and i - 1 < len(sparse_vectors):
            record["sparse_vector"] = sparse_vectors[i - 1]
        else:
            record["sparse_vector"] = {}  # 空稀疏向量作为占位符
        records.append(record)

    client.insert(collection_name=collection, data=records)
    client.flush(collection_name=collection)
    client.load_collection(collection)
    logging.info(
        "[Milvus] 知识版本发布完成 collection=%s chunks=%s bm25=%s",
        collection,
        len(records),
        bm25_encoder is not None,
    )
    return len(records)


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_milvus_retriever: MilvusRetriever | None = None


def get_milvus_retriever() -> MilvusRetriever:
    global _milvus_retriever
    if _milvus_retriever is None:
        _milvus_retriever = MilvusRetriever()
    return _milvus_retriever
