# services/order_mcp.py
import os
import sqlite3
from typing import Optional, Tuple
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("order_mcp")

# 数据库路径配置（从环境变量读取，默认使用项目内 data 目录）
DB_PATH = os.getenv("ORDER_DB_PATH", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../data/orders.db")
))


def _fetch_order(order_no: str) -> Optional[Tuple[str, str, str, str, float, str, int, str]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(    #订单号、用户ID、状态、商品详情、金额、物流信息、是否可取消、更新时间
            """
            SELECT
                order_no,
                user_id,
                status,
                items,
                amount,
                logistics,
                cancelable,
                updated_at
            FROM orders WHERE order_no=?
            """,
            (order_no,),
        )
        result = cur.fetchone()
        return result
    finally:
        conn.close()


@mcp.tool()
def get_order(order_no: str, user_id: str) -> str:
    """
    获取指定订单号的订单详情。

    Args:
        order_no: 要查询的订单号
        user_id: 用户ID，用于权限校验

    Returns:
        订单详情的字符串描述
    """
    record = _fetch_order(order_no)
    if record is None:
        return f"未找到订单号 {order_no} 的记录，请确认后再试。"

    order_no_db, user_id_db, status, items, amount, logistics, cancelable, updated_at = record
    if user_id != user_id_db:
        return f"订单 {order_no_db} 不属于当前用户，无权限查看。"

    return (
        f"订单号：{order_no_db}\n"
        f"用户ID：{user_id_db}\n"
        f"订单状态：{status}\n"
        f"商品信息：{items}\n"
        f"支付金额：{amount} 元\n"
        f"物流信息：{logistics if logistics else '暂无物流更新'}\n"
        f"可取消：{'是' if cancelable else '否'}\n"
        f"最近更新：{updated_at}"
    )

@mcp.tool()
def check_cancelable(order_no: str, user_id: str) -> str:
    """
    检查订单是否可以取消（基于表中的 cancelable/status 字段）。
    
    Args:
        order_no: 订单号
        user_id: 用户ID，用于权限校验
    
    Returns:
        订单是否可取消的信息
    """
    record = _fetch_order(order_no)
    if record is None:
        return f"未找到订单号 {order_no} 的记录，请确认后再试。"

    order_no_db, user_id_db, status, items, amount, logistics, cancelable, updated_at = record
    if user_id != user_id_db:
        return f"订单 {order_no_db} 不属于当前用户，无权限查看。"

    if cancelable:
        return f"订单 {order_no_db} 当前状态：{status}，允许取消。"
    return f"订单 {order_no_db} 当前状态：{status}，不可取消。"

@mcp.tool()
def create_refund_draft(order_no: str, user_id: str) -> str:
    """
    创建退款确认草案，不会写入订单或发起退款。
    
    Args:
        order_no: 订单号
        user_id: 用户ID，用于权限校验
    
    Returns:
        需要由用户确认的退款草案
    """
    record = _fetch_order(order_no)
    if record is None:
        return f"未找到订单号 {order_no} 的记录，请确认后再试。"

    order_no_db, user_id_db, status, items, amount, logistics, cancelable, updated_at = record
    if user_id != user_id_db:
        return f"订单 {order_no_db} 不属于当前用户，无权限操作。"

    if not cancelable:
        return f"订单 {order_no_db} 当前状态：{status}，不可取消/退款。"

    return (
        f"订单 {order_no_db} 当前状态：{status}，符合退款/取消申请条件。"
        "请提示用户在页面中明确确认；确认后由受控退款工作流执行。"
    )

if __name__ == "__main__":
    mcp.run(transport="stdio")
