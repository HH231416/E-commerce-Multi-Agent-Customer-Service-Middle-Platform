"""知识版本发布/回滚 CLI。

用法（Milvus 后端）：
    # 构建索引（默认取 KNOWLEDGE_VERSION，不激活）
    python RAG_data/build_index.py

    # 构建并立即激活为线上版本（发布新版本）
    python RAG_data/build_index.py --release --version 2026-08-01

    # 查看已发布版本与当前活跃版本
    python RAG_data/build_index.py --list

    # 秒级回滚到已发布版本（只需改版本指针，无需重建索引）
    python RAG_data/build_index.py --rollback 2026-07-19

发布流程：构建 → --release 激活新版本 → 观察错误率/延迟/业务反馈
    → 异常时 --rollback 切回上一版本。
"""
import argparse
import os
import sys

# 允许从任意目录运行：把项目根目录加入 sys.path，保证能导入 agents 包
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _version_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        default=None,
        help="知识版本号，缺省取 KNOWLEDGE_VERSION 环境变量",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 知识索引构建与版本管理")
    sub = parser.add_subparsers(dest="command")

    build_p = sub.add_parser("build", help="仅构建索引，不激活")
    _version_arg(build_p)

    release_p = sub.add_parser("release", help="构建并激活为线上版本")
    _version_arg(release_p)

    rollback_p = sub.add_parser("rollback", help="秒级回滚到已发布版本")
    rollback_p.add_argument("version", help="要回滚到的目标版本号")

    list_p = sub.add_parser("list", help="列出已发布版本与活跃版本")

    args = parser.parse_args()

    # 无子命令时默认走 build（保持向后兼容）
    if args.command in (None, "build"):
        from agents.rag_backend import build_index

        count = build_index(version=args.version)
        print(f"已构建 {count} 个知识片段（未激活）")
        return

    if args.command == "release":
        from agents.rag_backend import build_index
        from agents.version_registry import get_active_version, publish_version

        version = args.version or os.getenv("KNOWLEDGE_VERSION")
        if not version:
            print("错误：--version 或 KNOWLEDGE_VERSION 环境变量不能为空", file=sys.stderr)
            sys.exit(2)
        prev_active = get_active_version()
        count = build_index(version=version)
        publish_version(version, activate=True)
        print(f"已发布并激活版本 {version}（{count} 个知识片段），原活跃版本: {prev_active}")
        return

    if args.command == "rollback":
        from agents.version_registry import list_published_versions, rollback_to

        try:
            registry = rollback_to(args.version)
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            sys.exit(2)
        print(f"已回滚，当前活跃版本: {registry['active']}")
        return

    if args.command == "list":
        from agents.version_registry import get_active_version, list_published_versions

        print(f"活跃版本: {get_active_version()}")
        published = list_published_versions()
        if not published:
            print("已发布版本: (无，请先运行 build_index.py --release)")
        else:
            print(f"已发布版本: {', '.join(published)}")
        return


if __name__ == "__main__":
    main()
