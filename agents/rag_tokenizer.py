"""中文分词统一入口：加载电商客服领域词典后再调用 jieba.lcut。

为什么单独抽模块：
- BM25 依赖"同一套分词"：建库时拟合的词汇表（fit）与查询编码（encode）必须用
  完全一致的分词结果，否则 query 切出的词在词表里找不到，稀疏向量退化为空。
- FAISS 路径（BM25Retriever）与 Milvus 路径（BM25SparseEncoder）共用这一份
  jieba + 领域词典，保证两条路径的精确术语命中行为一致。
- jieba.add_word 是进程级全局状态，集中在本模块注册一次，避免散落各处重复注册。
"""
import jieba  # jieba 缺失时本模块导入即抛 ImportError，由调用方回退默认分词

# 电商客服领域术语：默认分词可能拆碎（"运费险"→"运费"+"险"），
# 强制注册为整词，让精确术语在 BM25 路按整词命中（"7天无理由""价格保护"）。
# 生产上可改为 jieba.load_userdict 读取知识团队维护的词典文件，术语表可独立演进。
_DOMAIN_TERMS = [
    "运费险",
    "极速退款",
    "仅退款",
    "退货退款",
    "7天无理由",
    "价格保护",
    "满减",
    "优惠券",
    "售后时效",
    "人工客服",
    "跨境购物",
]

for _term in _DOMAIN_TERMS:
    jieba.add_word(_term)
_TOKENIZE = jieba.lcut


def tokenize(text: str) -> list[str]:
    """jieba 中文分词：字符串 → 词列表。

    供 BM25Retriever 的 preprocess_func 与 BM25SparseEncoder 编码共用，
    保证建库与查询两侧的分词、词典完全一致。
    """
    return _TOKENIZE(text)
