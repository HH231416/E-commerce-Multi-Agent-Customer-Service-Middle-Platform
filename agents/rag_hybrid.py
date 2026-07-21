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
    2. 基于 page_content 前 100 字符去重合并
    3. Reranker 对合并结果重排序
    4. 返回 final_k 个最相关文档
    """

    def __init__(self):
        self.faiss_retriever = None  # FAISS 向量检索器
        self.bm25_retriever = None  # BM25 关键词检索器
        self.documents = []  # 所有文档片段
        self._initialized = False  # 是否已初始化

    def _ensure_initialized(self):
        """
        加载离线生成的 FAISS 索引，并在内存建立 BM25 索引。
        
        初始化步骤：
        1. 加载 RAG_data 目录下的 .txt/.md/.docx 文档
        2. 递归分割为 500 字符的片段（重叠 50 字符）
        3. 加载 FAISS 向量索引
        4. 构建 BM25 关键词索引
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

        # 文本分割：RecursiveCharacterTextSplitter 优先按段落/句子边界分割
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,  # 每段最多 500 字符（平衡语义完整性和检索粒度）
            chunk_overlap=50,  # 相邻片段重叠 50 字符（防止关键信息被切断在边界）
        )
        self.documents = text_splitter.split_documents(docs)  # 分割文档
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

        # 构建 BM25 索引（关键词检索）
        self.bm25_retriever = BM25Retriever.from_documents(self.documents)  # 从文档创建 BM25 索引
        self.bm25_retriever.k = 5  # 返回 top 5

        self._initialized = True  # 标记已初始化
        logging.info("RAG 检索器已加载，知识版本=%s，片段数=%s", KNOWLEDGE_VERSION, len(self.documents))

    def retrieve(self, query: str, top_k: int = 5, final_k: int = 3) -> List[Document]:
        """
        执行混合检索：BM25+FAISS 并行召回 → 去重 → Reranker 重排序 → 返回 top final_k。
        
        输入：
        - query: 用户查询字符串
        - top_k: 每路召回数量（默认 5）
        - final_k: 最终返回数量（默认 3）
        
        输出：相关文档列表（按相关性降序）
        
        失败处理：
        - 检索器未初始化 → 返回空列表
        - Reranker 不可用 → 跳过多路合并去重后直接返回
        """
        self._ensure_initialized()  # 确保已初始化

        if not self.bm25_retriever or not self.faiss_retriever:  # 检索器不可用
            return []

        # Step 1: 多路召回（BM25 + FAISS 并行）
        bm25_results = self.bm25_retriever.get_relevant_documents(query)  # BM25 关键词召回
        faiss_results = self.faiss_retriever.get_relevant_documents(query)  # FAISS 语义召回

        # Step 2: 去重合并（基于 page_content 前 100 字符 hash）
        # 为什么用前 100 字符而非全文 hash：chunk_overlap 导致相邻片段部分重复，
        #     前 100 字符足以区分不同片段，同时容忍少量尾部差异
        seen = set()  # 已见过的 hash 集合
        merged = []  # 去重后的文档列表
        for doc in bm25_results + faiss_results:  # 先 BM25 后 FAISS（BM25 精确匹配优先级更高）
            key = hash(doc.page_content[:100])  # 基于前 100 字符生成 hash
            if key not in seen:  # 未重复
                seen.add(key)  # 记录 hash
                merged.append(doc)  # 添加到合并列表

        if not merged:  # 两路都没召回结果
            return []

        # Step 3: Reranker 重排序（用 CrossEncoder 对 query-doc 对打分）
        reranker = get_reranker()  # 获取共享 Reranker（可能为 None）
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
            document_id = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
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
