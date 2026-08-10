"""知识版本注册表：管理已发布版本与当前活跃版本的指针。

设计意图：
- 秒级回滚：版本指针存于本地 JSON，切换只需原子改写该文件，
    检索器每次检索读取 active 版本，无需重启进程即可回滚到旧版
- 多版本共存：Milvus 按版本隔离为独立 Collection，registry 只维护"活跃版本"
    这一指针，旧版本 Collection 不会被覆盖，回滚即指向旧指针
- 环境变量兜底：registry 文件不存在时回退到 KNOWLEDGE_VERSION 环境变量，
    兼容现有部署（未运行发布流程的场景）

并发注意：单机部署下文件写入量极小（仅在发布/回滚时触发），
    通过"写临时文件 + os.replace 原子改名"避免读到半截 JSON。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

# 注册表文件默认路径：data/knowledge_versions.json，可用环境变量覆盖
REGISTRY_PATH = os.getenv(
    "KNOWLEDGE_REGISTRY_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/knowledge_versions.json")),
)

_logger = logging.getLogger(__name__)


def _empty_registry() -> dict:
    """registry 不存在时的默认结构。"""
    return {
        "active": os.getenv("KNOWLEDGE_VERSION", "unversioned"),
        "published": [],
    }


def load_registry() -> dict:
    """读取注册表；文件不存在或损坏时返回默认结构（不抛异常）。"""
    if not os.path.exists(REGISTRY_PATH):
        return _empty_registry()
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "active": data.get("active", os.getenv("KNOWLEDGE_VERSION", "unversioned")),
            "published": list(data.get("published", [])),
        }
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("[Registry] 读取注册表失败，回退默认结构: %s", exc)
        return _empty_registry()


def _save_registry(registry: dict) -> None:
    """原子写入注册表：先写临时文件再 os.replace，避免并发读到半截内容。"""
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    tmp_path = REGISTRY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, REGISTRY_PATH)
    _logger.info("[Registry] 注册表已更新: active=%s published=%s", registry["active"], registry["published"])


def get_active_version() -> str:
    """返回当前活跃版本号。优先读注册表；无注册表时回退环境变量。"""
    return load_registry()["active"]


def list_published_versions() -> list[str]:
    """返回所有已发布版本号列表。"""
    return load_registry()["published"]


def publish_version(version: str, *, activate: bool = False) -> dict:
    """登记一个已构建的版本；activate=True 时同时设为活跃版本。"""
    registry = load_registry()
    if version not in registry["published"]:
        registry["published"].append(version)
    if activate:
        registry["active"] = version
    _save_registry(registry)
    return registry


def rollback_to(version: str) -> dict:
    """
    将活跃版本指针切换到指定已发布版本，实现秒级回滚。

    校验：目标版本必须已发布（在 published 列表中），否则抛 ValueError，
    防止把指针指到不存在的 Collection。
    """
    registry = load_registry()
    if version not in registry["published"]:
        raise ValueError(
            f"版本 {version} 尚未发布，无法回滚。已发布版本: {registry['published']}"
        )
    if registry["active"] == version:
        _logger.info("[Registry] 已是活跃版本，无需回滚: %s", version)
        return registry
    registry["active"] = version
    _save_registry(registry)
    _logger.info("[Registry] 回滚完成: %s -> %s", version, registry["published"])
    return registry
