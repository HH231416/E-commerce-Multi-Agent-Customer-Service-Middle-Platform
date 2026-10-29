"""
模型配置中心 — 所有 LLM 和 Embedding 模型的唯一入口
所有模型类型由 .env 文件统一管理，禁止在其他文件中直接读取模型环境变量

设计意图：
- 单一入口：所有模块通过此文件获取模型实例，便于统一切换模型版本和参数
- 缓存复用：避免重复创建相同配置的模型对象（init_chat_model 每次都会新建连接）
- 环境隔离：通过 .env 管理不同环境（开发/测试/生产）的模型配置
- 为什么不用 LangChain Hub：需要自定义 temperature、timeout 等参数，Hub 不够灵活
"""
from langchain.chat_models import init_chat_model  # LangChain 官方的模型初始化工具，兼容 OpenAI 兼容 API
from langchain_openai import OpenAIEmbeddings  # OpenAI 格式的 Embedding 模型客户端
from dotenv import load_dotenv  # 从 .env 文件加载环境变量到 os.environ
import os  # 读取环境变量

load_dotenv()  # 在模块级别加载 .env，确保所有环境变量在导入时即可用

# ============================================================
# 通用认证配置（所有模型共用）
# ============================================================
API_KEY = os.getenv("DASHSCOPE_API_KEY")  # 阿里云 DashScope API 密钥（通义千问系列模型使用）
if not API_KEY:
    raise ValueError("未找到 DASHSCOPE_API_KEY 环境变量，请检查 .env 文件")  # 启动即失败，防止后续隐性错误
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")  # DashScope 兼容 OpenAI 格式的端点

# ============================================================
# 模型名称（全部从 .env 读取，无硬编码默认值）
# 为什么用多个模型而非单一模型：
# - 路由任务（分类）需要低 temperature 保证稳定性
# - 中间件（摘要）需要中等 temperature 兼顾准确性和多样性
# - 主模型（对话）需要较高 temperature 保证回答自然
# - 如果全用一个模型，无法针对不同任务调优，且可能浪费 Token（路由不需要 1024 max_tokens）
# ============================================================
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.7-plus")  # 主对话模型，用于生成最终回复
ROUTING_MODEL = os.getenv("ROUTING_MODEL", "qwen3.7-flash")  # 意图路由模型，用于分类和分发
MIDDLEWARE_MODEL = os.getenv("MIDDLEWARE_MODEL", "qwen3.7-max-2026-05-20")  # 中间件模型，用于摘要等轻量任务（更便宜）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")  # 向量化模型，用于 RAG 和缓存

# ============================================================
# 模型实例缓存（避免重复创建相同的模型对象）
# 为什么需要缓存：init_chat_model 每次调用都会创建新的 HTTP 连接池，
# 多个 Agent 共享同一模型实例可以减少内存和连接开销
# ============================================================
_cached_models = {}  # key 格式: "chat:{model_name}" 或 "embedding:{model_name}"

# ============================================================
# LLM 获取函数
# ============================================================

def get_model(model_name: str = None):
    """
    获取主模型（默认使用 LLM_MODEL 环境变量指定的模型）
    可在 .env 中设置 LLM_MODEL 切换模型

    输入：可选的模型名称（None 则使用默认 LLM_MODEL）
    状态：_cached_models 字典（模块级缓存）
    输出：已缓存的 ChatModel 实例
    失败处理：首次调用 init_chat_model 时若网络不通会抛出异常（未捕获，让调用方处理）
    为什么不更简单（直接全局变量）：全局变量在模块加载时就初始化，会阻塞 import；
        延迟创建可以在启动后再连接，且支持运行时切换模型
    """
    name = model_name or LLM_MODEL  # 如果调用方没指定模型，使用环境变量中的默认值
    cache_key = f"chat:{name}"  # 构造缓存键，区分不同模型
    if cache_key not in _cached_models:  # 缓存未命中时才创建新实例
        _cached_models[cache_key] = init_chat_model(
            name,  # 模型名称，如 "qwen-plus"
            model_provider="openai",  # 使用 OpenAI 兼容协议（DashScope 支持此协议）
            api_key=API_KEY,  # 认证密钥
            base_url=BASE_URL,  # API 端点地址
            temperature=0.7,  # 主模型温度较高，保证回答多样性
            timeout=300,  # 5 分钟超时，处理复杂多步推理
            max_tokens=1024,  # 限制单次生成长度，控制成本
            # 流式返回末尾带上 usage，否则统计里 Token 用量恒为 0
            stream_usage=True,
        )
    return _cached_models[cache_key]  # 返回已缓存的实例


def get_routing_model():
    """
    获取路由模型 — .env 中 ROUTING_MODEL 控制
    路由模型用于意图分类，需要低 temperature 以保证分类结果的一致性。
    为什么不用主模型做路由：主模型 temperature=0.7 可能导致同一问题两次分类结果不同，
        造成用户体验不一致（同样的问法，一次走订单 Agent，一次走商品 Agent）
    """
    cache_key = f"chat:{ROUTING_MODEL}"  # 缓存键
    if cache_key not in _cached_models:  # 缓存未命中
        _cached_models[cache_key] = init_chat_model(
            ROUTING_MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.3,  # 低温度确保分类结果稳定
            timeout=60,  # 路由任务通常很快，60 秒足够
            max_tokens=2048,  # 路由可能需要返回结构化输出，预留足够空间
        )
    return _cached_models[cache_key]


def get_middleware_model():
    """
    获取中间件模型 — .env 中 MIDDLEWARE_MODEL 控制
    中间件模型用于摘要、总结等轻量任务，使用更便宜的模型（qwen-turbo）。
    为什么单独一个模型：摘要任务不需要强推理能力，用 turbo 性价比更高；
        且摘要和主对话可能并发执行，共享模型会造成排队
    """
    cache_key = f"chat:{MIDDLEWARE_MODEL}"  # 缓存键
    if cache_key not in _cached_models:  # 缓存未命中
        _cached_models[cache_key] = init_chat_model(
            MIDDLEWARE_MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.5,  # 中等温度，摘要既要准确又要避免过于机械
            timeout=60,  # 摘要任务轻量，60 秒足够
            max_tokens=2048,  # 摘要可能较长，预留足够 Token
        )
    return _cached_models[cache_key]


# ============================================================
# Embedding 获取函数
# ============================================================

def get_embeddings():
    """
    获取 Embedding 模型 — .env 中 EMBEDDING_MODEL 控制
    其他模块（如 RAG_tool、cache_layer）应通过此函数获取，不要直接读环境变量。
    
    输入：无（从环境变量读取模型名）
    状态：_cached_models 字典
    输出：OpenAIEmbeddings 实例
    失败处理：首次调用时若 API 不可用会抛出异常
    为什么 check_embedding_ctx_length=False：DashScope 的 text-embedding-v3 有特殊的
        token 限制策略，开启预检查可能导致误报，关闭后由服务端自行处理截断
    """
    cache_key = f"embedding:{EMBEDDING_MODEL}"  # 缓存键，与 chat 模型区分前缀
    if cache_key not in _cached_models:  # 缓存未命中
        _cached_models[cache_key] = OpenAIEmbeddings(
            model=EMBEDDING_MODEL,  # Embedding 模型名称
            openai_api_base=BASE_URL,  # 复用相同的 API 端点
            openai_api_key=API_KEY,  # 复用相同的 API 密钥
            check_embedding_ctx_length=False,  # 禁用对输入文本长度的严格预检查，避免 DashScope 兼容性问题
        )
    return _cached_models[cache_key]
