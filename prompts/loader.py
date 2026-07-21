"""Prompt 加载器：YAML 配置 + 变量模板 + A/B 分流

设计意图：
- YAML 管理 Prompt：将系统提示词从代码中分离，非技术人员也能修改和审核
- 变量模板：支持 {company_name} 等占位符，同一套 Prompt 模板适配多租户
- A/B 分流：通过 experiments.json 配置实验，基于 user_id 哈希分流，保证同一用户始终看到同一版本
- 为什么不用数据库存 Prompt：YAML 文件可以直接版本管理（Git），修改有记录可追溯；
    数据库方案需要额外开发管理界面，对小团队不划算
"""
import os  # 文件路径操作（当前未直接使用，保留给扩展）
import json  # 解析 experiments.json 配置文件
import random  # 无 user_id 时的随机分流
from pathlib import Path  # 跨平台路径处理
from typing import Optional, Dict  # 类型注解

import yaml  # YAML 解析库

PROMPTS_DIR = Path(__file__).parent  # Prompt 模板目录（prompts/）
EXPERIMENTS_FILE = PROMPTS_DIR / "experiments.json"  # A/B 实验配置文件路径


class PromptLoader:
    """Prompt 加载器：负责 YAML 加载、缓存、A/B 版本解析和变量渲染"""

    def __init__(self):
        self._cache: Dict[str, dict] = {}  # YAML 文件内容缓存，key: "agent:version"
        self._experiments = None  # A/B 实验配置懒加载，None 表示尚未加载

    def _load_experiments(self) -> dict:
        """加载 A/B 实验配置，首次调用时从 JSON 文件读取并缓存"""
        if self._experiments is None:  # 懒加载：只在需要时才读文件
            if EXPERIMENTS_FILE.exists():  # 文件存在才读取
                with open(EXPERIMENTS_FILE, 'r', encoding='utf-8') as f:  # utf-8 编码支持中文
                    self._experiments = json.load(f)  # 解析 JSON
            else:
                self._experiments = {}  # 文件不存在时使用空配置，不中断服务
        return self._experiments  # 返回已缓存的配置

    def _load_yaml(self, agent: str, version: str) -> dict:
        """加载指定 Agent 和版本的 YAML Prompt 文件，结果缓存在内存中"""
        cache_key = f"{agent}:{version}"  # 构造缓存键，如 "gateway:1.0.0"
        if cache_key not in self._cache:  # 缓存未命中才读文件
            yaml_path = PROMPTS_DIR / agent / f"v{version}.yaml"  # 拼接路径：prompts/gateway/v1.0.0.yaml
            if not yaml_path.exists():  # 文件不存在则明确报错
                raise FileNotFoundError(f"Prompt 文件不存在: {yaml_path}")
            with open(yaml_path, 'r', encoding='utf-8') as f:  # 读取 YAML 文件
                self._cache[cache_key] = yaml.safe_load(f)  # safe_load 防止 YAML 注入攻击
        return self._cache[cache_key]  # 返回缓存内容

    def _resolve_version(self, agent: str, user_id: str = None) -> str:
        """
        解析当前请求应使用的 Prompt 版本。
        A/B 分流逻辑：
        - 如果 experiments.json 中该 agent 的 active=true，按 user_id 哈希分流
        - 哈希值 0-49 → version_a，50-99 → version_b（50:50 分流）
        - 同一 user_id 的哈希值不变，保证用户体验一致性
        - 无 user_id 时随机分流（适用于匿名用户）
        - 实验未激活或未配置时，返回 version_a 或默认 "1.0.0"
        """
        experiments = self._load_experiments()  # 获取实验配置
        exp = experiments.get(agent)  # 查找该 Agent 的实验配置
        if exp and exp.get("active", False):  # 实验激活中
            if user_id:
                seed = hash(user_id) % 100  # 基于用户 ID 哈希，确保同一用户始终分到同一组
            else:
                seed = random.randint(0, 99)  # 匿名用户随机分流
            if seed < 50:  # 前 50% 用户使用版本 A
                return exp["version_a"]
            else:  # 后 50% 用户使用版本 B
                return exp["version_b"]
        # 实验未激活但有配置，返回版本 A 作为默认
        if exp and "version_a" in exp:
            return exp["version_a"]
        return "1.0.0"  # 完全未配置时使用默认版本

    def load(self, agent: str, version: str = None, user_id: str = None, **variables) -> str:
        """
        加载并渲染 Prompt 模板。
        
        输入：
        - agent: Agent 名称（如 "gateway", "manager"）
        - version: 指定版本号（None 则自动 A/B 分流）
        - user_id: 用户 ID（用于 A/B 分流的一致性哈希）
        - **variables: 模板变量（如 company_name="XX电商"）
        
        输出：渲染后的完整 system_prompt 字符串
        
        失败处理：模板变量缺失时只打印警告并返回原始模板，不中断服务
        为什么不更简单（直接读文件拼接）：需要支持 A/B 测试和多租户变量替换，
            直接读文件无法满足这些需求
        """
        if version is None:  # 未指定版本则自动分流
            version = self._resolve_version(agent, user_id)
        config = self._load_yaml(agent, version)  # 加载 YAML 配置
        template = config.get("system_prompt", "")  # 获取 system_prompt 模板字符串
        all_vars = {}  # 收集所有模板变量
        if "variables" in config:  # YAML 中定义的默认变量（优先级低）
            all_vars.update(config["variables"])
        all_vars.update(variables)  # 调用方传入的变量（优先级高，可覆盖默认值）
        if all_vars:  # 有变量需要替换
            try:
                return template.format(**all_vars)  # 使用 Python 字符串格式化替换占位符
            except KeyError as e:  # 模板中有 {xxx} 但未提供对应变量
                print(f"[PromptLoader] 警告: 缺少模板变量 {e}，使用原始模板")
                return template  # 降级：返回未替换的原始模板
        return template  # 无变量，直接返回

    def get_version_info(self, agent: str, user_id: str = None) -> dict:
        """获取当前请求使用的 Prompt 版本信息，用于 A/B 测试效果分析和反馈追踪"""
        version = self._resolve_version(agent, user_id)  # 解析版本
        config = self._load_yaml(agent, version)  # 加载配置
        return {"agent": agent, "version": version, "status": config.get("status", "unknown")}  # 返回版本元信息


_loader = None  # 模块级单例缓存

def get_prompt_loader() -> PromptLoader:
    """获取全局 PromptLoader 单例（线程安全依赖 GIL）"""
    global _loader  # 声明使用模块级变量
    if _loader is None:  # 首次调用时创建
        _loader = PromptLoader()
    return _loader  # 返回单例
