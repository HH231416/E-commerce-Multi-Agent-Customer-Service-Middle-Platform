# Mcpserver/coupon_mcp.py
"""MCP Server: 优惠券与促销服务 — 用户优惠券查询、可用优惠券推荐、促销活动查询

设计意图：
- 优惠券是电商客服高频场景（"有什么优惠""能用什么券"），独立 MCP 避免污染订单/商品逻辑
- best_coupon_for_order 是核心增值功能：自动根据订单金额推荐最优券，减少用户决策成本
- 促销活动查询不需要 user_id（公开信息），与优惠券查询需要 user_id（私有数据）形成对比
- 为什么用 SQLite julianday 而非 Python datetime：SQL 层面过滤过期券，减少数据传输量
"""
import logging
import os  # 读取环境变量
import sqlite3  # SQLite 数据库驱动
from datetime import datetime  # 日期时间（当前未直接使用，保留给扩展）
from typing import List  # 类型注解
from mcp.server.fastmcp import FastMCP  # FastMCP 框架

mcp = FastMCP("coupon_mcp")  # 创建 MCP Server 实例

DB_PATH = os.getenv("COUPON_DB_PATH", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../data/coupons.db")
))  # 数据库路径


def _get_conn():
    """获取数据库连接，设置 row_factory 支持字典式列名访问"""
    conn = sqlite3.connect(DB_PATH)  # 创建连接
    conn.row_factory = sqlite3.Row  # 使查询结果可通过列名访问
    return conn


@mcp.tool()  # 注册为 MCP 工具
def list_user_coupons(user_id: str) -> str:
    """
    查询指定用户拥有的所有优惠券，包括金额、使用条件、有效期。
    只返回未使用且在有效期内的优惠券，按面额从高到低排序。

    Args:
        user_id: 用户ID（由 mcp_wrapper 自动注入）

    Returns:
        用户优惠券列表
    """
    logging.info("MCP 调用 list_user_coupons")
    conn = _get_conn()  # 获取连接
    try:
        cur = conn.cursor()  # 创建游标
        # 查询未使用/已领取且未过期的优惠券，按面额降序（大额券优先展示）
        cur.execute(
            """SELECT * FROM coupons WHERE user_id=? AND status IN ('未使用','已领取')
               AND julianday(expire_date) >= julianday('now')  -- SQL 层面过滤过期券，减少无效数据传输
               ORDER BY amount DESC""",  # 按面额降序
            (user_id,)  # 参数化查询
        )
        rows = cur.fetchall()  # 获取所有可用券

        if not rows:  # 没有可用优惠券
            # 检查是否有已过期但未使用的优惠券（告知用户以免误以为没领过）
            cur.execute(
                "SELECT COUNT(*) as cnt FROM coupons WHERE user_id=? AND status='未使用'",
                (user_id,)
            )
            expired = cur.fetchone()
            if expired and expired["cnt"] > 0:  # 有过期券
                return f"您有 {expired['cnt']} 张优惠券已过期，当前暂无可用优惠券。"
            return "您当前暂无可用优惠券。新用户注册可领取新人专享优惠券哦！"  # 引导注册

        # 格式化优惠券列表
        result = f"🎫 您有 {len(rows)} 张可用优惠券：\n\n"
        for i, row in enumerate(rows, 1):  # 从 1 开始编号
            # 格式化使用条件：有门槛显示"满X减Y"，无门槛显示"无门槛减Y"
            cond = f"满{row['min_amount']:.0f}减{row['amount']:.0f}" if row['min_amount'] > 0 else f"无门槛减{row['amount']:.0f}"
            result += f"{i}. {cond}\n"
            result += f"   类型：{row['coupon_type']}\n"  # 满减券/折扣券/无门槛券
            result += f"   有效期至：{row['expire_date']}\n"  # 过期日期
            if row['description']:  # 优惠券说明（可选）
                result += f"   说明：{row['description']}\n"
            result += "\n"  # 空行分隔
        return result
    finally:
        conn.close()  # 确保连接关闭


@mcp.tool()  # 注册为 MCP 工具
def best_coupon_for_order(user_id: str, amount: float) -> str:
    """
    根据用户订单金额，推荐最优的可用优惠券组合。
    只返回满足使用门槛且在有效期内的优惠券，按面额降序推荐面额最大的。

    Args:
        user_id: 用户ID
        amount: 订单金额（元）

    Returns:
        最优优惠券推荐
    """
    logging.info("MCP 调用 best_coupon_for_order")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 查询满足门槛的未使用优惠券：min_amount <= 订单金额，未过期
        cur.execute(
            """SELECT * FROM coupons WHERE user_id=? AND status='未使用'
               AND min_amount <= ?  -- 使用门槛不超过订单金额
               AND julianday(expire_date) >= julianday('now')  -- 未过期
               ORDER BY amount DESC LIMIT 5""",  # 按面额降序取前5张
            (user_id, amount)  # 参数化查询
        )
        rows = cur.fetchall()

        if not rows:  # 没有适用的优惠券
            return f"当前没有适用于 ¥{amount:.2f} 订单的优惠券。建议关注平台促销活动获取更多优惠。"

        best = rows[0]  # 面额最大的就是最优推荐（贪心策略）
        # 为什么用贪心而非动态规划：电商场景下用户通常只有几张券，
        # 且大部分券不能叠加使用，贪心策略简单有效
        return (
            f"💰 最优优惠券推荐：\n"
            f"订单金额：¥{amount:.2f}\n"
            f"使用优惠券后：¥{amount - best['amount']:.2f}（省 ¥{best['amount']:.2f}）\n"  # 实付金额
            f"优惠券详情：满{best['min_amount']:.0f}减{best['amount']:.0f}，有效期至 {best['expire_date']}"
        )
    finally:
        conn.close()  # 确保连接关闭


@mcp.tool()  # 注册为 MCP 工具
def get_active_promotions() -> str:
    """
    查询当前正在进行的促销活动，包括满减、折扣、限时特价等。
    不需要 user_id（公开信息），按优先级从高到低排序。

    Returns:
        当前促销活动列表
    """
    logging.info("MCP 调用 get_active_promotions")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 查询当前有效的促销活动（开始日期 <= 今天 <= 结束日期）
        cur.execute(
            """SELECT * FROM promotions
               WHERE julianday(start_date) <= julianday('now')  -- 活动已开始
               AND julianday(end_date) >= julianday('now')  -- 活动未结束
               ORDER BY priority DESC"""  # 按优先级降序（高优先级活动先展示）
        )
        rows = cur.fetchall()

        if not rows:  # 没有进行中的活动
            return "当前暂无正在进行的促销活动，请关注后续活动通知。"

        # 格式化促销活动列表
        result = "🎉 当前促销活动：\n\n"
        for i, row in enumerate(rows, 1):  # 从 1 开始编号
            result += f"{i}. {row['title']}\n"  # 活动标题
            result += f"   {row['description']}\n"  # 活动描述
            result += f"   活动时间：{row['start_date']} 至 {row['end_date']}\n\n"  # 活动时间范围
        return result
    finally:
        conn.close()  # 确保连接关闭


if __name__ == "__main__":
    # 独立运行模式：通过 stdio 协议与 Agent 进程通信
    mcp.run(transport="stdio")
