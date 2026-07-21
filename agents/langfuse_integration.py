"""Langfuse 可观测集成 — 单例管理 CallbackHandler

设计意图：
- LLM 调用链路追踪：记录每次 LLM 调用的输入、输出、Token 用量、延迟
- 调试与优化：通过 Langfuse Dashboard 分析哪些 Prompt 效果好、哪些 Agent 调用慢
- 单例模式：全局共享一个 CallbackHandler，避免重复创建连接
- 为什么用 Langfuse 而非其他方案（如 MLflow/Weights & Biases）：
    Langfuse 专为 LLM 应用设计，原生支持 LangChain/LangGraph，配置简单；
    开源版本可自部署，数据不出企业内网
- 降级策略：Langfuse 不可用时不影响主业务流程（可观测性是辅助功能）
"""
import os  # 读取环境变量（LANGFUSE_PUBLIC_KEY 等）
import logging  # 日志记录
from langfuse.langchain import CallbackHandler  # Langfuse 的 LangChain 回调处理器

_langfuse_handler = None  # 模块级单例缓存

def get_langfuse_handler() -> CallbackHandler:
    """
    获取或创建 Langfuse CallbackHandler（单例模式）。
    
    输入：无（从环境变量读取配置）
    输出：CallbackHandler 实例或 None（初始化失败时）
    失败处理：初始化失败返回 None，调用方（main.py）检查后跳过追踪
    
    Langfuse 配置方式（环境变量）：
    - LANGFUSE_PUBLIC_KEY: 公钥
    - LANGFUSE_SECRET_KEY: 私钥（Langfuse 自动从环境变量读取）
    - LANGFUSE_HOST: 自部署地址（可选）
    """
    global _langfuse_handler  # 声明修改模块级变量
    if _langfuse_handler is None:  # 首次调用时初始化
        try:
            # Langfuse 4.x: secret_key + host 从环境变量读取
            public_key = os.getenv("LANGFUSE_PUBLIC_KEY")  # 读取公钥
            if public_key:  # 配置了公钥（完整配置）
                _langfuse_handler = CallbackHandler(public_key=public_key)  # 使用公钥初始化
                logging.info("[Langfuse] 已初始化")
            else:  # 未配置公钥
                _langfuse_handler = CallbackHandler()  # 使用默认模式（可能连接本地或不做任何事）
                logging.info("[Langfuse] 未配置密钥，使用默认模式")
        except Exception as e:  # 初始化失败（网络问题/配置错误）
            logging.warning(f"[Langfuse] 初始化失败 ({e})，跳过可观测")
            _langfuse_handler = None  # 设为 None，调用方检查后跳过
    return _langfuse_handler  # 返回单例
