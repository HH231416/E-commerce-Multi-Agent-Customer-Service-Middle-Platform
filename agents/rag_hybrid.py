"""混合检索器：BM25关键词 + FAISS语义 + BGE-Reranker 重排序

设计意图：
- 多路召回互补：BM25 擅长关键词精确匹配（如"退款时效"），FAISS 擅长语义相似（如"怎么退钱"和"退款流程"）
- 两阶段检索：粗排（top_k=5）→ 精排（Reranker → final_k=3），兼顾召回率和准确率
- 懒加载：Reranker 模型约 2GB，首次调用时才加载，避免启动延迟
- 为什么不用 Milvus/Elasticsearch：单机场景下 FAISS 足够，减少运维依赖；
    BM25 用纯 Python 实现，不需要额外的搜索引擎
"""
import hashlib
import logging
import os  # 文件路径操作
from typing import List  # 类型注解
from langchain_core.documents import Document  # LangChain 文档对象
from langchain_text_splitters import RecursiveCharacterTextSplitter  # 递归文本分割器
from langchain_community.vectorstores import FAISS  # FAISS 向量数据库（本地）
from langchain_community.retrievers import BM25Retriever  # BM25 关键词检索器
try:
    from agents.model import get_embeddings
except ImportError:  # 兼容从 agents 目录直接启动的旧入口
    from model import get_embeddings

DATA_DIR = os.path.join(os.path.dirname(__file__), "../RAG_data")  # 知识库文档目录
INDEX_DIR = os.getenv(
    "RAG_INDEX_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/rag_faiss"))
)
KNOWLEDGE_VERSION = os.getenv("KNOWLEDGE_VERSION", "unversioned")

from agents.rag_reranker import get_reranker  # 共享 Reranker 模块，Milvus 和 FAISS 路径共用


class HybridRetriever:
    """
    BM25 + FAISS 多路召回 + Reranker 重排序。
    
    检索流程：
    1. BM25 和 FAISS 并行召回 top_k 个候选文档
    2. 基于 document_id + 片段前 100 字符去重合并
    3. Reranker 对合并结果重排序
    4. 返回 final_k 个最相关文档
    """

    def __init__(self, use_bm25: bool = True, use_reranker: bool = True):
        self.faiss_retriever = None  # FAISS 向量检索器
        self.bm25_retriever = None  # BM25 关键词检索器
        self.documents = []  # 所有文档片段
        self.use_bm25 = use_bm25  # 评测对比用：关闭则退化为纯 Dense 检索
        self.use_reranker = use_reranker  # 评测对比用：关闭则跳过精排
        self._initialized = False  # 是否已初始化

    def _ensure_initialized(self):
        """
        加载离线生成的 FAISS 索引，并按需在内存建立 BM25 索引。

        初始化步骤：
        1. 加载 RAG_data 目录下的 .txt/.md/.docx 文档
        2. 递归分割为 500 字符的片段（重叠 50 字符）
        3. 加载 FAISS 向量索引
        4. 构建 BM25 关键词索引（use_bm25=True 时）
        """
        if self._initialized:  # 已初始化，跳过
            return

        if not os.path.exists(DATA_DIR):  # 数据目录不存在
            logging.warning("RAG 数据目录不存在: %s", DATA_DIR)
            return

        docs = _load_documents(DATA_DIR)  # 加载原始文档
        if not docs:  # 没有文档
            logging.warning("RAG 未找到任何知识文档")
            return

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
        )
        self.documents = text_splitter.split_documents(docs)
        index_file = os.path.join(INDEX_DIR, "index.faiss")
        if not os.path.exists(index_file):
            logging.error("RAG 索引不存在，请先运行: python RAG_data/build_index.py")
            return
        # 索引目录是部署流水线生成并以只读方式挂载的可信工件，才允许反序列化本地文档存储。
        faiss_store = FAISS.load_local(
            INDEX_DIR, get_embeddings(), allow_dangerous_deserialization=True
        )
        self.faiss_retriever = faiss_store.as_retriever(
            search_type="similarity",  # 余弦相似度检索
            search_kwargs={"k": 5},  # 返回 top 5
        )

        # 构建 BM25 索引（关键词检索）；纯 Dense 基线时跳过
        # 中文必须接 jieba 分词：langchain 默认按空格分词，中文整句会被当一个 token，BM25 退化为随机排序。
        # 分词统一走 agents.rag_tokenizer（含电商客服领域词典），与 Milvus 稀疏向量路径行为一致
        if self.use_bm25:
            try:
                from agents.rag_tokenizer import tokenize as _tokenize  # 中文分词：领域术语按整词命中
            except ImportError:  # jieba 缺失时回退默认分词，不中断启动
                _tokenize = None
            kwargs = {"preprocess_func": _tokenize} if _tokenize else {}
            self.bm25_retriever = BM25Retriever.from_documents(self.documents, **kwargs)
            self.bm25_retriever.k = 5  # 返回 top 5

        self._initialized = True  # 标记已初始化
        logging.info("RAG 检索器已加载，知识版本=%s，片段数=%s", KNOWLEDGE_VERSION, len(self.documents))

    def retrieve(self, query: str, top_k: int = 5, final_k: int = 3) -> List[Document]:
        """
        执行混合检索：BM25+FAISS 并行召回 → RRF 融合去重 → 二次去重 → Reranker 重排序 → 返回 top final_k。

        输入：
        - query: 用户查询字符串
        - top_k: 每路召回数量（默认 5）
        - final_k: 最终返回数量（默认 3）

        输出：相关文档列表（按相关性降序）

        失败处理：
        - 检索器未初始化 → 返回空列表
        - Reranker 不可用 → 跳过重排序，直接使用 RRF 融合后的顺序
        """
        self._ensure_initialized()  # 确保已初始化

        if not self.faiss_retriever:  # 检索器不可用
            return []

        # Step 1: 多路召回（BM25 可用时与 FAISS 并行）
        # .invoke() 兼容 LangChain 1.x（VectorStoreRetriever/BM25Retriever 已移除公开的 get_relevant_documents）
        faiss_results = self.faiss_retriever.invoke(query)  # FAISS 语义召回
        if self.bm25_retriever is not None:
            bm25_results = self.bm25_retriever.invoke(query)  # BM25 关键词召回
            # Step 2: RRF 融合去重。BM25 和 FAISS 各带独立排名，用 1/(k+rank)
            # 加权融合，避免"BM25 无条件排前"导致精确术语路污染 top-3。
            merged = _rrf_fuse(bm25_results, faiss_results)
        else:
            merged = faiss_results

        if not merged:  # 两路都没召回结果
            return []

        # Step 2.5: 按 document_id + 片段内容前缀二次去重（同源同文片段只保留一次）。
        # RRF 已按 document_id 去重，这里兜底去掉内容完全相同却被重复召回的片段
        merged = _deduplicate_documents(merged)

        # Step 3: Reranker 重排序（用 CrossEncoder 对 query-doc 对打分）
        reranker = get_reranker() if self.use_reranker else None  # 获取共享 Reranker（可能为 None）
        if reranker and len(merged) > 1:  # 有 Reranker 且有多于 1 个候选
            pairs = [(query, doc.page_content[:512]) for doc in merged]  # 构造 query-doc 对（截断到 512）
            scores = reranker.predict(pairs, show_progress_bar=False)  # 批量打分
            scored_docs = list(zip(merged, scores))  # 文档-分数配对
            scored_docs.sort(key=lambda x: x[1], reverse=True)  # 按分数降序
            merged = [doc for doc, _ in scored_docs]  # 提取排序后的文档
            logging.debug("RAG 重排序完成，最高分=%.3f", scored_docs[0][1])

        return merged[:final_k]  # 返回 top final_k


def _load_documents(directory: str) -> List[Document]:
    """
    加载目录下的 .txt/.md/.docx 文档。
    
    输入：目录路径
    输出：Document 对象列表（每个文件一个 Document）
    失败处理：.docx 加载失败时跳过该文件并打印警告（不中断整体加载）
    为什么不支持 PDF：PDF 解析依赖复杂（需要 PyMuPDF 或 pdfplumber），
        当前阶段优先支持最常用的文本格式
    """
    documents = []  # 文档列表
    for filename in os.listdir(directory):  # 遍历目录
        filepath = os.path.join(directory, filename)  # 完整路径
        content = None  # 文件内容
        if filename.endswith('.txt') or filename.endswith('.md'):  # 纯文本/Markdown
            with open(filepath, 'r', encoding='utf-8') as f:  # UTF-8 编码读取
                content = f.read()  # 读取全部内容
        elif filename.endswith('.docx'):  # Word 文档
            try:
                import docx2txt  # 延迟导入 docx2txt（非必需依赖）
                content = docx2txt.process(filepath)  # 提取文本
            except Exception as e:
                logging.warning("RAG 无法加载文档 %s: %s", filename, e)
                continue  # 跳过该文件
        if content:  # 成功读取到内容
            # 文件名 + 内容共同标识一份知识文档，避免不同文件正文相同时发生冲突。
            document_id = hashlib.sha256(
                f"{filename}\0{content}".encode("utf-8")
            ).hexdigest()[:16]
            documents.append(Document(
                page_content=content,
                metadata={
                    "source": filepath,
                    "filename": filename,
                    "document_id": document_id,
                    "knowledge_version": KNOWLEDGE_VERSION,
                },
            ))
    logging.info("RAG 已加载 %s 份知识文档", len(documents))
    return documents


def _deduplicate_documents(documents: List[Document]) -> List[Document]:
    """按 document_id + 片段前 100 字符去重，保留候选列表中首次出现的文档。"""
    seen: set[tuple[str, str]] = set()
    deduplicated = []
    for doc in documents:
        document_id = doc.metadata.get("document_id")
        if document_id is None:
            raise ValueError("知识片段缺少 document_id；请重新构建索引")
        key = (str(document_id), doc.page_content[:100])
        if key not in seen:
            seen.add(key)
            deduplicated.append(doc)
    return deduplicated


def _rrf_fuse(bm25_results: List[Document], faiss_results: List[Document], k: int = 60) -> List[Document]:
    """
    多路召回 RRF（Reciprocal Rank Fusion）融合排序。

    对每个片段，分别按其在 BM25 路和 FAISS 路中的排名计分：score += 1/(k + rank)。
    两路都命中的片段会叠加分数，天然高于单一路径命中的片段；
    相比"BM25 无条件排前"，RRF 让语义与关键词两路互相校验，融合排序更稳。

    用 document_id 作为片段唯一键（同源文档的两个不同片段 id 不同，不会误合并）。
    """
    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}
    for rank, doc in enumerate(bm25_results + faiss_results, start=1):
        document_id = doc.metadata.get("document_id")
        if document_id is None:
            raise ValueError("知识片段缺少 document_id；请重新构建索引")
        key = str(document_id)
        # 两路拼接后 rank 区间不同，为公平起见两路都从 1 开始计分，而非拼接后的连续 rank
        road_rank = rank if rank <= len(bm25_results) else rank - len(bm25_results)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + road_rank)
        docs[key] = doc  # 后写入的（FAISS 路）优先保留，与去重时语义路优先一致
    ranked = sorted(docs.keys(), key=lambda d: scores[d], reverse=True)
    return [docs[key] for key in ranked]


def build_persistent_index() -> int:
    """由发布流程显式调用，生成可复用的 FAISS 索引工件。"""
    docs = _load_documents(DATA_DIR)
    if not docs:
        raise RuntimeError("没有可用于构建索引的知识文档")
    documents = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50).split_documents(docs)
    os.makedirs(INDEX_DIR, exist_ok=True)
    FAISS.from_documents(documents, get_embeddings()).save_local(INDEX_DIR)
    logging.info("RAG 索引构建完成，目录=%s，片段数=%s", INDEX_DIR, len(documents))
    return len(documents)


# 全局单例（避免重复加载文档和构建索引）
_hybrid_retriever = None


def get_hybrid_retriever() -> HybridRetriever:
    """
    获取全局 HybridRetriever 实例（单例模式）。
    
    为什么用单例：文档加载和索引构建耗时（需调用 Embedding API），
        单例确保整个应用生命周期内只执行一次初始化
    """
    global _hybrid_retriever  # 声明修改模块级变量
    if _hybrid_retriever is None:  # 首次调用时创建
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever  # 返回单例
