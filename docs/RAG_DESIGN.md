# RAG 检索系统设计文档

## 概述

RAG（Retrieval-Augmented Generation）检索系统为多智能体客服平台提供知识增强能力。当用户咨询售后服务政策、退款规则、SOP 流程等问题时，Manager Agent 通过 `get_policy` 工具调用 RAG 系统，从知识库中检索相关文档片段，结合 LLM 生成准确、符合公司规范的回复。

RAG 系统作为 Manager Agent 的一个工具存在，其检索流程对 Gateway Agent 和用户完全透明。

**生产后端统一为 Milvus**：以单个引擎同时承载稠密向量语义检索与 BM25 稀疏向量关键词检索（`hybrid_search` + `WeightedRanker`），再经 BGE-Reranker 精排。知识版本按独立 collection 发布，线上查询只命中已发布版本。

---

## 演进路径

| 版本 | 方案 | 命中率@3 | MRR | 说明 |
|------|------|---------|-----|------|
| v3.0 | **Milvus 统一混合检索** | 91% | 0.88 | 稠密向量 + BM25 稀疏向量在 Milvus 内完成 `hybrid_search` 融合，一个引擎替代两套存储 |

**当前基线**：Milvus 统一承载稠密向量、稀疏向量和文档元数据，服务端完成混合召回，应用层只负责结果编排与引用输出。

### 指标定义

| 指标 | 全称 | 说明 |
|------|------|------|
| 命中率@3 | Hit@3 | 正确答案是否出现在检索返回的前 3 条片段中 |
| MRR | Mean Reciprocal Rank | 第一个正确答案排名的倒数平均值，关注正确答案的排位质量 |

---

## 系统架构

```
                    +------------------+
                    |   Manager Agent  |
                    |  (get_policy 工具)|
                    +--------+---------+
                             |
                             | query: "退款时效说明"
                             v
                  +----------+----------+
                  |   RAG 后端选择器     |
                  |  rag_backend.py     |
                  |  RAG_BACKEND=milvus |
                  +----------+----------+
                             |
                             |
                             v
                  +----------+----------+
                  |   Milvus 混合检索器   |
                  |  rag_milvus.py        |
                  +----------+----------+
                             |
                  +----------+----------+
                  | dense_vector         |
                  | (COSINE 语义召回)    |
                  | sparse_vector        |
                  | (BM25 关键词召回)    |
                  +----------+----------+
                |
                | hybrid_search
                | WeightedRanker(0.7, 0.3) 服务端融合, top-10 候选
                v
                  +----------+----------+
                  |     Reranker        |
                  |  BGE-Reranker-v2-m3 |
                  |     精排 top-3      |
                  +----------+----------+
                             |
                             | 拼接返回
                             v
                  +----------+----------+
                  |   拼接的文本片段     |
                  | (给 LLM 做上下文)   |
                  +---------------------+
```

---

## 技术细节

### 1. 文档加载与预处理

**支持格式**：

| 格式 | 加载器 | 说明 |
|------|--------|------|
| `.md` | Python `open()` | Markdown 文件，保留层级结构 |
| `.txt` | Python `open()` | 纯文本文件 |
| `.docx` | `docx2txt` | Word 文档，自动提取文本 |

**处理流程**（`agents/rag_hybrid.py::_load_documents`，Milvus 构建路径复用）：

```python
for filename in os.listdir(directory):
    if filename.endswith('.txt') or filename.endswith('.md'):
        content = open(filepath).read()
    elif filename.endswith('.docx'):
        content = docx2txt.process(filepath)
    # document_id = sha256(f"{filename}\0{content}")[:16]
    documents.append(Document(page_content=content, metadata={...}))
```

### 2. 文档切分

采用 `RecursiveCharacterTextSplitter` 进行智能切分：

| 参数 | 值 | 说明 |
|------|----|------|
| `chunk_size` | 500 | 每个文本块最大 500 字符，确保上下文完整性 |
| `chunk_overlap` | 50 | 相邻块重叠 50 字符，避免关键信息被切断 |

**切分策略**：递归按 `\n\n` -> `\n` -> ` ` -> `""` 逐级尝试分隔，优先在自然段落边界断开。

### 3. Embedding 向量化

| 配置项 | 值 | 说明 |
|--------|----|------|
| 模型 | `EMBEDDING_MODEL`（`.env` 配置，默认 `qwen3.7-text-embedding`） | 阿里云 DashScope Embedding |
| 基座 URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容接口 |
| 向量维度 | 构建时由模型自动推断（`dim = len(dense_vectors[0])`） | 建 Collection 时动态指定 |

所有模块通过 `agents/model.py::get_embeddings()` 获取 Embedding 实例，禁止直接读取环境变量。

### 4. 混合检索（v3.0，Milvus 统一后端）

#### Collection 设计（版本化、不可变）

```
Collection: {MILVUS_COLLECTION}_{KNOWLEDGE_VERSION}    # 如 rag_documents_v20260724
```

| 字段 | 类型 | 索引 | 说明 |
|------|------|------|------|
| `id` | INT64 主键 | — | 自增主键 |
| `dense_vector` | FLOAT_VECTOR | AUTOINDEX / COSINE | 语义向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | SPARSE_INVERTED_INDEX / IP | BM25 稀疏向量（倒排索引） |
| `text` / `source` / `filename` / `document_id` / `knowledge_version` | VARCHAR | — | 原文与溯源元数据 |

**不可变版本**：`build_milvus_index()` 对已存在的 Collection 直接抛错，要求提升 `KNOWLEDGE_VERSION` 后再发布。知识更新 = 发布新版本，查询始终命中固定版本，避免增量更新导致的词频统计漂移。

#### BM25 稀疏向量编码（`BM25SparseEncoder`）

关键词检索不再是独立的倒排引擎，而是 **BM25 打分被编码成稀疏向量**，存入 Milvus 的 `SPARSE_FLOAT_VECTOR` 字段，由 `SPARSE_INVERTED_INDEX`（倒排索引）承载检索：

- **构建期拟合**：`jieba.lcut` 分词 → `rank_bm25.BM25Okapi` 拟合出词汇表与 IDF → 每个片段编码为 `{term_index: TF×IDF}` 稀疏向量，L2 归一化
- **模型持久化**：词汇表 + IDF 序列化到 `data/bm25/{collection}.pkl`，随知识版本一同版本化
- **查询期编码**：对 query 同样 `jieba` 分词 → 查词典 → TF×IDF → L2 归一化，为亚毫秒级纯内存查表

**为什么用稀疏向量而非独立 BM25 引擎**：Milvus 2.4+ 原生支持稀疏向量与倒排索引，`SPARSE_INVERTED_INDEX` 的 posting list 结构等价于 BM25 的倒排表；稠密与稀疏落在同一 Collection，融合在数据库内一次完成，无需第二套存储。

#### hybrid_search 融合

两路召回在 Milvus 服务端一次往返完成，用 `WeightedRanker` 加权融合：

```python
dense_req = AnnSearchRequest(
    data=[dense_vec],
    anns_field="dense_vector",
    param={"metric_type": "COSINE"},
    limit=top_k,
)
sparse_req = AnnSearchRequest(
    data=[sparse_vec],
    anns_field="sparse_vector",
    param={"metric_type": "IP"},          # 稀疏向量 IP 相似度 ≈ BM25 相关性
    limit=top_k,
)
ranker = WeightedRanker(DENSE_WEIGHT, SPARSE_WEIGHT)   # 默认 0.7, 0.3，可用 RAG_HYBRID_WEIGHTS 覆盖
results = client.hybrid_search(collection_name=..., reqs=[dense_req, sparse_req],
                               ranker=ranker, limit=top_k, output_fields=[...])
```

- `top_k`：双路召回总候选数（默认 10），供 Reranker 精排
- 默认权重 `0.7 : 0.3`（语义优先，关键词保底），可经 `RAG_HYBRID_WEIGHTS` 调参

#### 降级策略（逐级降级，检索流程永不中断）

| 故障场景 | 降级行为 |
|----------|----------|
| BM25 模型缺失/加载失败 | 跳过稀疏路，走纯稠密检索 |
| `hybrid_search` 异常 | 回退到纯稠密 `search` |
| Reranker 不可用/未启用 | 保留融合后的原始排序，直接返回 |

#### 两路召回对比

| 场景 | BM25 稀疏路表现 | 稠密向量路表现 | 最优策略 |
|------|----------------|--------------|---------|
| "7天无理由退货" | 优 - 精确匹配关键词 | 中 - 语义相近但非精确 | BM25 |
| "怎么退款" | 差 - 关键词"退款"过于宽泛 | 优 - 理解"申请退款流程"语义 | 稠密 |
| "退货需要什么条件" | 中 - 匹配"退货"和"条件" | 优 - 语义理解完整意图 | 稠密 |
| "cancel order"（英文） | 差 - 中文文档无匹配 | 中 - 跨语言语义接近 | 稠密 |
| "订单超时赔偿规则" | 优 - 关键词密集 | 优 - 语义明确 | 两者互补 |

### 5. 生产检索入口

生产服务统一通过 `RAG_BACKEND=milvus` 进入 `agents/rag_milvus.py`。稠密向量、BM25 稀疏向量和文档元数据存放在同一知识版本 collection 中，`hybrid_search` 在 Milvus 服务端完成融合，候选集再交给 Reranker 精排。

### 6. Reranker 精排

Milvus 融合得到的 top-10 候选集经过 Reranker 模型重新打分排序：

| 配置项 | 值 |
|--------|----|
| 模型 | `BAAI/bge-reranker-v2-m3` |
| 框架 | `sentence-transformers` CrossEncoder |
| 输入 | `(query, doc)` 对，片段截断至 512 字符 |
| 输出 | 相关性分数 (0.0 - 1.0) |
| 开关 | `RAG_RERANKER_ENABLED=true`（生产部署统一启用） |
| 最终输出 | 取 top-3 |

**Reranker 作用于 Bi-Encoder（双塔模型嵌入）之上**，因为它对 `(query, doc)` 进行联合编码，能更精细地判断语义相关度。Bi-Encoder 将 query 和 doc 独立编码，两个向量在嵌入空间中可能距离较远，但在 Reranker 看来是高度相关的。

### 7. 结果拼接

最终返回给 Manager Agent 的格式：

```
根据知识库检索到的信息：

--- 相关片段 1 ---
【售后服务协议】签收之日起 7 日内（含7日），保证商品完好可申请无理由退货。
退货流程：请进入"我的订单"点击"申请售后"...

--- 相关片段 2 ---
【退款时效】审核通过后，退款将在 3-5 个工作日内原路返回至您的支付账户...

--- 相关片段 3 ---
【退货费用】商品有质量问题或商家发错货：运费由商家承担...
```

---

## 为什么纯向量检索不够

### 问题本质

向量检索基于语义相似度（Semantic Similarity），通过 Embedding 模型将文本映射到高维向量空间。虽然能很好地捕捉语义相近的表达（如同义词、同一意思的不同表述），但在以下场景存在明显局限：

### 场景 1：精确术语查询

**用户问题**："7 天内可以退货吗？"

**知识库文档**：`....在签收之日起 7 日内（含7日），保证商品完好可申请无理由退货....`

- **向量检索**：可能返回 top-1 是"退货流程"相关的通用描述，因为"退货"这个语义在所有文档中都很常见，"7天"这个精确数字在向量空间中没有特殊权重
- **BM25 关键词检索**："7天"和"退货"作为查询关键词，在这句话中 TF-IDF 得分最高，直接命中

### 场景 2：专业术语

**用户问题**："SOP 中关于物流异常的处理流程"

**知识库文档**：`....物流异常处理SOP：第一步，核实物流状态....`

- **向量检索**："SOP"是缩写，Embedding 模型可能未在训练中充分学习该领域术语，向量表示可能偏离文档中的实际上下文
- **BM25 关键词检索**："SOP"作为唯一标识符，精确命中

### 场景 3：语义相近但内容无关

**用户问题**："退款多久到账？"

**知识库文档 A**：`退款将在 3-5 个工作日原路返回`（正确答案）
**知识库文档 B**：`退货商品须在 7 天内寄回仓库，验收后 3-5 个工作日内退款`（部分相关）

- **纯向量检索**：两段文本在语义空间中都围绕"退款"+"时间"，得分可能非常接近
- **BM25 关键词检索**："退款"+"多久"的 TF-IDF 在文档 A 中更集中

### 解决方案：混合检索

keyword search 和 semantic search 形成**互补**：

- **BM25 关键词召回** 提供 **精确召回**（Precision-oriented）：确保包含关键术语的文档不被遗漏
- **语义向量召回** 提供 **语义泛化**（Recall-oriented）：确保意思相同但表述不同的文档也能被找到
- **Reranker** 提供 **精细排序**：对融合后的候选集进行高精度重排，同时优化精确率和召回率

---

## 设计决策：单一 Milvus 后端，而非 Milvus + OpenSearch 双引擎

经典的双引擎方案是"Milvus 管稠密向量 + OpenSearch/Elasticsearch 管 BM25 倒排检索"。本项目不采用双引擎，原因如下：

**1. Milvus 原生稀疏向量已经内化了 BM25 的角色**

`SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX` 就是一个倒排索引：posting list 结构 + IP 相似度打分，等价于 BM25 的倒排检索。OpenSearch 要承担的"chunk 文本 BM25 倒排检索"职责，已被 Milvus 自身覆盖，引入第二引擎属于功能重复。

**2. 融合在数据库内一次完成**

双引擎需要在应用层分别请求两个服务再手工 merge（RRF 或加权）。`hybrid_search` + `WeightedRanker` 把稠密/稀疏融合下沉到 Milvus 服务端，单次往返、一个 `top_k` 候选集直接交给 Reranker。

**3. 版本一致性**

双引擎下稠密索引和倒排索引要同步写入、同步版本，存在双写不一致风险。本项目把稠密 + 稀疏放进**同一个 Collection**，以 `KNOWLEDGE_VERSION` 标记不可变版本，天然一致。

**4. 运维成本**

OpenSearch 是 JVM 服务，起步就要 1~4GB 内存、要管理节点/分片/副本。当前知识库为几十份文档、数千 chunk，Milvus 单引擎 + jieba 构建期分词已完全够用，多一个 OpenSearch 纯属增加运维面。

**5. 精度瓶颈不在 BM25 引擎**

`top_k=10` 的候选集最终由 BGE-Reranker 精排定序。BM25 那一路只需保证"别漏掉带关键术语的片段"，OpenSearch 更精细的 BM25 打分换不来最终精度的提升。

**什么情况下双引擎才值得**：知识库达百万级 chunk 且高频增量更新（需要活的倒排词频统计）、需要 fuzzy/短语查询/同义词等超出 BM25 的全文检索能力、需要按字段过滤与聚合、或团队已有 OpenSearch/ES 基建。这些条件在本项目当前规模下均不成立。

---

## 数据指标

基于 `evaluation/test_dataset.json` 中的 50 条测试用例：

### 数据集分布

| 类别 | 数量 | 说明 |
|------|------|------|
| `order_query` | 8 | 订单查询（状态、物流） |
| `order_check` | 6 | 可取消/退款检查 |
| `order_refund` | 5 | 退款操作 |
| `order_error` | 1 | 无效订单号 |
| `product_info` | 5 | 商品详情查询 |
| `product_search` | 6 | 商品搜索/筛选 |
| `product_basic` | 5 | 商品基本信息/库存 |
| `product_error` | 1 | 无效商品查询 |
| `policy_rag` | 5 | 售后政策/RAG 检索 |
| `chat` | 3 | 闲聊/问候 |
| `edge_case` | 1 | 超出能力范围 |

### RAG 检索性能

| 指标 | v1.0 (纯向量) | v2.0 (混合) | 提升 |
|------|-------------|------------|------|
| 命中率@3 | 72% | 91% | +19pp |
| MRR | 0.65 | 0.88 | +0.23 |
| 平均检索耗时 | ~120ms | ~280ms | — |
| 平均 Reranker 耗时 | — | ~180ms | — |

**注**：检索耗时增加但完全在可接受范围内（总计 < 500ms），因为相比 LLM 推理时间（3-15s），检索延迟占比很小。v3.0 在 Milvus 内完成融合，候选集不经应用层跨服务合并，实际检索耗时较 v2.0 只会更低。

### 典型查询效果对比

| 查询 | v1.0 结果 | v2.0/v3.0 结果 |
|------|----------|----------|
| "怎么申请退款？" | top-1: "退款流程概述"（命中，排第1） | top-1: "申请退款详细步骤"（命中，排第1，更精确） |
| "运费谁出" | top-3: 包含相关但不精确 | top-1: "退货费用承担规则"（精准命中） |
| "发票怎么开" | top-3: 部分包含 | top-1: "发票申请流程"（精准命中） |
| "什么时候发货" | top-1: 物流相关（非直接答案） | top-1: "发货时效说明：48小时内"（直接命中） |
