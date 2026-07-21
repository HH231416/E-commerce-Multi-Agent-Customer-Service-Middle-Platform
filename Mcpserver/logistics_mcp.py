# Mcpserver/logistics_mcp.py
"""MCP Server: 物流追踪服务 — 提供快递单号查询、物流节点追踪、异常检测

设计意图：
- 物流查询需要权限校验（用户只能查自己的订单），每次调用都会验证 user_id 与订单归属
- 异常检测是主动服务：不等待用户投诉，Agent 可以主动调用 check_logistics_anomaly 发现问题
- 为什么物流和订单分开两个 MCP：职责分离，订单 MCP 负责订单生命周期（创建/取消/退款），
    物流 MCP 负责运输过程追踪；修改物流逻辑不影响订单逻辑
"""
import logging
import os  # 读取环境变量
import sqlite3  # SQLite 数据库驱动
from typing import Optional, Tuple, List  # 类型注解
from mcp.server.fastmcp import FastMCP  # FastMCP 框架

mcp = FastMCP("logistics_mcp")  # 创建 MCP Server 实例

DB_PATH = os.getenv("LOGISTICS_DB_PATH", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../data/logistics.db")
))  # 数据库路径，支持环境变量覆盖


def _get_conn():
    """
    获取数据库连接（内部辅助函数）。
    设置 row_factory = sqlite3.Row 使查询结果可以通过列名访问（如 row["order_no"]），
    比元组索引更可读、更安全。
    """
    conn = sqlite3.connect(DB_PATH)  # 创建连接
    conn.row_factory = sqlite3.Row  # 使查询结果支持字典式访问
    return conn


@mcp.tool()  # 注册为 MCP 工具
def track_logistics(order_no: str, user_id: str) -> str:
    """
    查询指定订单的实时物流追踪信息，包括当前状态、物流节点、预计送达时间。

    Args:
        order_no: 要查询的订单号
        user_id: 用户ID，用于权限校验（由 mcp_wrapper 自动注入，LLM 不可见）

    Returns:
        物流追踪详情的字符串描述
    """
    logging.info("MCP 调用 track_logistics")
    conn = _get_conn()  # 获取数据库连接
    try:
        cur = conn.cursor()  # 创建游标

        # 先校验订单归属（安全第一：用户只能查自己的订单物流）
        cur.execute(
            "SELECT order_no, user_id, status, logistics FROM orders WHERE order_no=?",  # 参数化查询
            (order_no,)
        )
        order = cur.fetchone()  # 获取订单记录
        if order is None:  # 订单不存在
            return f"未找到订单号 {order_no} 的记录，请确认后再试。"
        if order["user_id"] != user_id:  # 订单不属于当前用户，拒绝访问
            return f"订单 {order_no} 不属于当前用户，无权限查看。"

        # 查询物流节点（按时间升序，展示从发到收的完整轨迹）
        cur.execute(
            "SELECT * FROM logistics_tracks WHERE order_no=? ORDER BY node_time ASC",  # ASC 升序：最早的在前面
            (order_no,)
        )
        nodes = cur.fetchall()  # 获取所有物流节点

        if not nodes:  # 没有物流记录（可能刚下单，还没发货）
            return (
                f"订单号：{order_no}\n"
                f"当前状态：{order['status']}\n"
                f"物流信息：{order['logistics'] or '暂无物流更新'}\n"
                "暂无详细物流轨迹。"
            )

        # 组装物流追踪结果
        result = f"订单号：{order_no}\n"
        result += f"当前状态：{order['status']}\n"  # 订单状态
        result += f"物流公司：{nodes[0]['carrier'] or '系统物流'}\n"  # 快递公司
        result += f"快递单号：{nodes[0]['tracking_no'] or order_no}\n\n"  # 快递单号
        result += "📦 物流轨迹：\n"  # 物流时间线
        for i, node in enumerate(nodes, 1):  # 遍历每个物流节点
            # 根据节点状态选择图标：完成/签收用绿色，其他用蓝色
            icon = "🟢" if "完成" in (node["node_status"] or "") or "签收" in (node["node_status"] or "") else "🔵"
            result += f"  {icon} {node['node_time']}  {node['node_desc']}\n"  # 格式：图标 + 时间 + 描述

        last_node = nodes[-1]  # 最新物流节点
        if last_node["estimated_arrival"]:  # 有预计送达时间
            result += f"\n⏱ 预计送达：{last_node['estimated_arrival']}"

        return result  # 返回完整物流信息
    finally:
        conn.close()  # 确保连接关闭


@mcp.tool()  # 注册为 MCP 工具
def check_logistics_anomaly(order_no: str, user_id: str) -> str:
    """
    检查指定订单的物流是否存在异常（如长时间未更新、地址错误等）。
    
    Agent 可在用户询问物流时主动调用此工具，在问题升级前发现并告知用户。

    Args:
        order_no: 订单号
        user_id: 用户ID，用于权限校验

    Returns:
        异常检测结果
    """
    logging.info("MCP 调用 check_logistics_anomaly")
    conn = _get_conn()
    try:
        cur = conn.cursor()

        # 权限校验：先查订单归属
        cur.execute(
            "SELECT order_no, user_id, status FROM orders WHERE order_no=?",  # 参数化查询
            (order_no,)
        )
        order = cur.fetchone()
        if order is None:  # 订单不存在
            return f"未找到订单号 {order_no} 的记录。"
        if order["user_id"] != user_id:  # 权限校验失败
            return f"订单 {order_no} 不属于当前用户。"

        # 查询所有物流节点（按时间倒序，最新在前）
        cur.execute(
            "SELECT * FROM logistics_tracks WHERE order_no=? ORDER BY node_time DESC",  # DESC 倒序
            (order_no,)
        )
        nodes = cur.fetchall()

        if not nodes:  # 无物流记录
            return f"订单 {order_no} 暂无物流记录，请联系商家确认是否已发货。"

        # 获取最新物流时间和订单状态
        latest = nodes[0]["node_time"]  # 最新物流更新时间
        status = order["status"]  # 订单当前状态

        warnings = []  # 异常警告列表
        if status in ["配送中"] and latest:  # 配送中的订单
            # 使用 SQLite 的 julianday 函数计算日期差（避免 Python 日期库依赖）
            cur.execute(
                "SELECT julianday('now') - julianday(?) AS days_since", (latest[:10],)  # 只取日期部分
            )
            days = cur.fetchone()
            if days and days[0] is not None and days[0] > 3:  # 超过 3 天无更新
                warnings.append(f"⚠️ 物流已 {int(days[0])} 天未更新，建议联系快递公司核实")
        if status in ["待发货"] and latest:  # 待发货的订单
            cur.execute(
                "SELECT julianday('now') - julianday(?) AS days_since", (latest[:10],)
            )
            days_since = cur.fetchone()
            if days_since and days_since[0] is not None and days_since[0] > 2:  # 超过 2 天未发货
                warnings.append(f"⚠️ 订单已超过 {int(days_since[0])} 天未发货，建议催促商家")

        if warnings:  # 存在异常
            return f"订单 {order_no} 物流异常检测：\n" + "\n".join(warnings)  # 逐条列出异常

        return f"订单 {order_no} 物流状态正常，最新更新：{latest}"  # 一切正常

    finally:
        conn.close()  # 确保连接关闭


@mcp.tool()  # 注册为 MCP 工具
def estimate_delivery(order_no: str, user_id: str) -> str:
    """
    预估订单的送达时间，基于当前物流节点和配送地址分析。

    Args:
        order_no: 订单号
        user_id: 用户ID，用于权限校验

    Returns:
        预计送达时间信息
    """
    logging.info("MCP 调用 estimate_delivery")
    conn = _get_conn()
    try:
        cur = conn.cursor()

        # 权限校验
        cur.execute(
            "SELECT order_no, user_id, status FROM orders WHERE order_no=?",  # 参数化查询
            (order_no,)
        )
        order = cur.fetchone()
        if order is None:
            return f"未找到订单号 {order_no} 的记录。"
        if order["user_id"] != user_id:  # 权限校验
            return f"订单 {order_no} 不属于当前用户。"

        # 查询物流节点（按时间倒序，取最新状态）
        cur.execute(
            "SELECT * FROM logistics_tracks WHERE order_no=? ORDER BY node_time DESC",  # DESC 取最新
            (order_no,)
        )
        nodes = cur.fetchall()

        if not nodes:  # 无物流信息
            return f"订单 {order_no} 暂无物流信息，无法预估送达时间。"

        latest = nodes[0]  # 最新物流节点
        eta = latest["estimated_arrival"]  # 预计送达时间

        if eta:  # 有预计送达时间
            return (
                f"订单号：{order_no}\n"
                f"当前状态：{order['status']}\n"
                f"最新节点：{latest['node_desc']}（{latest['node_time']}）\n"  # 最新物流描述
                f"预计送达：{eta}"
            )
        else:  # 无预计时间（可能在运输初期）
            return (
                f"订单号：{order_no}\n"
                f"当前状态：{order['status']}\n"
                f"最新节点：{latest['node_desc']}（{latest['node_time']}）\n"
                "预计送达时间暂未确定，请稍后再查或联系商家确认。"
            )

    finally:
        conn.close()  # 确保连接关闭


if __name__ == "__main__":
    # 独立运行模式：通过 stdio 协议与 Agent 进程通信
    mcp.run(transport="stdio")  # stdio 传输：标准输入/输出
