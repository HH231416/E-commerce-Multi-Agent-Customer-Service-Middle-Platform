# Mcpserver/product_mcp.py
# 商品信息 MCP Server：提供商品详情查询、搜索、基本信息获取
#
# 设计意图：
# - MCP (Model Context Protocol) 是一种标准化的工具调用协议，让 LLM 通过统一接口调用外部服务
# - 商品查询与订单查询分离：不同领域的数据模型和业务逻辑独立维护，降低耦合
# - 使用 SQLite 而非 PostgreSQL：演示/中小规模场景下 SQLite 足够，避免额外运维成本
# - 为什么不直接在 Agent 里写 SQL：MCP 将数据访问封装为独立服务，可以独立测试、独立部署、
#     独立扩容；Agent 只关心"调用什么工具"，不关心"数据存在哪"
import logging
import os  # 读取环境变量
import sqlite3  # SQLite 数据库驱动
from typing import Optional, List, Tuple  # 类型注解
from mcp.server.fastmcp import FastMCP  # FastMCP 框架，简化 MCP Server 开发

mcp = FastMCP("product_mcp")  # 创建 MCP Server 实例，名称为 product_mcp

# 数据库路径配置（从环境变量读取，默认使用项目内 data 目录）
# 为什么用环境变量：方便在不同环境（开发/测试/生产）使用不同数据库
DB_PATH = os.getenv("PRODUCT_DB_PATH", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../data/products.db")
))

def _fetch_product(product_id: str = None, product_name: str = None) -> Optional[Tuple]:
    """
    根据商品ID或名称查询单个商品（内部辅助函数，不被 LLM 直接调用）。
    
    输入：product_id 或 product_name（二选一）
    输出：数据库行元组，未找到返回 None
    失败处理：SQLite 异常向上抛出（由调用方 try/except 处理）
    为什么用参数化查询：防止 SQL 注入，即使 product_name 含恶意字符也安全
    """
    conn = sqlite3.connect(DB_PATH)  # 创建数据库连接
    try:
        cur = conn.cursor()  # 创建游标
        if product_id:  # 按商品 ID 精确查询
            cur.execute(
                """
                SELECT
                    product_id, product_name, description, category, price, stock,
                    brand, specifications, image_url, status, created_at, updated_at
                FROM products WHERE product_id = ?
                """,  # ? 是参数化占位符，防止 SQL 注入
                (product_id,),  # 参数元组
            )
        elif product_name:  # 按商品名称模糊查询
            cur.execute(
                """
                SELECT
                    product_id, product_name, description, category, price, stock,
                    brand, specifications, image_url, status, created_at, updated_at
                FROM products WHERE product_name LIKE ? AND status = '在售'
                LIMIT 1
                """,  # LIKE 模糊匹配 + 只查在售商品
                (f"%{product_name}%",),  # % 通配符实现模糊搜索
            )
        else:  # 两个参数都没传
            return None
        return cur.fetchone()  # 返回第一条匹配记录
    finally:
        conn.close()  # finally 确保连接一定关闭，防止连接泄漏


def _search_products(keyword: str = None, category: str = None, max_price: float = None) -> List[Tuple]:
    """
    搜索商品，支持关键词、分类、价格筛选（内部辅助函数）。
    
    输入：可选的 keyword、category、max_price
    输出：匹配的商品列表（最多 10 条）
    失败处理：异常向上抛出
    为什么最多返回 10 条：防止返回过多结果导致 LLM 上下文溢出，同时减少数据库压力
    """
    conn = sqlite3.connect(DB_PATH)  # 创建数据库连接
    try:
        cur = conn.cursor()  # 创建游标
        # 基础查询：只查在售商品
        query = "SELECT product_id, product_name, description, category, price, stock, brand, specifications, image_url, status FROM products WHERE status = '在售'"
        params = []  # 参数列表（与 ? 占位符一一对应）
        
        if keyword:  # 关键词搜索：在名称和描述中匹配
            query += " AND (product_name LIKE ? OR description LIKE ?)"  # 动态拼接条件
            params.extend([f"%{keyword}%", f"%{keyword}%"])  # 两个 LIKE 各需要一个参数
        
        if category:  # 分类筛选：精确匹配
            query += " AND category = ?"
            params.append(category)
        
        if max_price is not None:  # 价格上限筛选
            query += " AND price <= ?"
            params.append(max_price)
        
        query += " ORDER BY price ASC LIMIT 10"  # 按价格升序，最多 10 条
        
        cur.execute(query, params)  # 执行参数化查询
        return cur.fetchall()  # 返回所有匹配记录
    finally:
        conn.close()  # 确保连接关闭


def _stock_status(stock: int) -> str:
    """根据库存数量返回人性化的库存状态描述（内部辅助函数）。"""
    if stock > 50:
        return "库存充足"
    elif stock > 10:
        return "库存正常"
    elif stock > 0:
        return "库存紧张"
    else:
        return "缺货"


@mcp.tool()  # 注册为 MCP 工具，LLM 可直接调用
def get_product_info(product_description: str, detail_level: str = "full") -> str:
    """
    根据商品描述或名称获取**单个**商品的详细信息。

    **注意**：
    - 仅适用于查询**具体**的某个商品（如"夏科有线键鼠套装"）。
    - 如果用户想要搜索**一类**商品（如"电脑外设"、"洗衣机"）或没有具体商品名，**请勿使用此工具**，应使用 `search_products` 工具。

    Args:
        product_description: 具体的商品名称或描述，例如："夏科有线键鼠套装"、"M20洗衣机"等
        detail_level: 详细程度。"full"（默认）返回完整信息包括规格/品牌/描述/图片；
                      "basic" 仅返回价格和库存状态，适用于用户只关心"多少钱""有没有货"的场景

    Returns:
        商品详情的字符串描述
    """
    logging.info("MCP 调用 get_product_info detail_level=%s", detail_level)

    # 先尝试精确匹配商品名称（优先精确查询，性能更好）
    record = _fetch_product(product_name=product_description)

    if record is None:  # 精确匹配失败
        # 降级为模糊搜索（兼容用户输入的描述而非精确名称）
        results = _search_products(keyword=product_description)
        if not results:  # 模糊搜索也失败
            return f"未找到与「{product_description}」相关的商品，请检查商品名称是否正确，或尝试使用其他关键词搜索。"
        # 返回第一个匹配结果（模糊搜索可能有多个结果）
        record = results[0]
        # 调整字段顺序以匹配 _fetch_product 的返回格式（_search_products 少 created_at 和 updated_at）
        record = (record[0], record[1], record[2], record[3], record[4], record[5],
                 record[6], record[7], record[8], record[9], None, None)  # None 填充缺失字段

    # 解包元组到命名变量（提高可读性）
    (product_id, product_name, description, category, price, stock,
     brand, specifications, image_url, status, created_at, updated_at) = record

    # 检查商品状态：非在售商品不应推荐给用户
    if status != "在售":
        return f"商品「{product_name}」当前状态：{status}，暂不可购买。"

    # basic 模式：只返回价格和库存状态
    if detail_level == "basic":
        result = (
            f"商品：{product_name}\n"
            f"价格：{price} 元\n"
            f"当前库存：{stock} 件\n"
            f"库存状态：{_stock_status(stock)}"
        )
        if stock == 0:  # 缺货时给出建议
            result += "\n提示：该商品目前缺货，建议选择其他商品或稍后再试。"
        return result

    # full 模式：返回完整信息
    result = (
        f"商品ID：{product_id}\n"
        f"商品名称：{product_name}\n"
        f"分类：{category}\n"
        f"价格：{price} 元\n"
        f"库存：{stock} 件（{_stock_status(stock)}）\n"
    )

    # 可选字段：存在时才添加（避免显示空值）
    if brand:
        result += f"品牌：{brand}\n"
    if description:
        result += f"商品描述：{description}\n"
    if specifications:
        result += f"规格参数：{specifications}\n"
    if image_url:
        result += f"商品图片：{image_url}\n"

    if stock == 0:
        result += "提示：该商品目前缺货，建议选择其他商品或稍后再试。\n"

    result += f"状态：{status}"  # 商品状态（在售/下架等）

    return result  # 返回格式化后的商品信息


@mcp.tool()  # 注册为 MCP 工具
def search_products(keyword: str = None, category: str = None, max_price: float = None) -> str:
    """
    搜索商品，支持按关键词、分类、价格筛选。
    
    Args:
        keyword: 搜索关键词，会在商品名称和描述中搜索
        category: 商品分类，例如："电脑外设"、"家用电器"、"网络设备"
        max_price: 最高价格（元），只返回价格不超过此值的商品
    
    Returns:
        符合条件的商品列表
    """
    logging.info("MCP 调用 search_products")
    
    results = _search_products(keyword=keyword, category=category, max_price=max_price)  # 执行搜索
    
    if not results:  # 无结果时返回友好的提示信息
        filters = []  # 收集已应用的筛选条件
        if keyword:
            filters.append(f"关键词「{keyword}」")
        if category:
            filters.append(f"分类「{category}」")
        if max_price:
            filters.append(f"价格≤{max_price}元")
        
        filter_str = "、".join(filters) if filters else "当前条件"  # 拼接筛选条件描述
        return f"未找到符合{filter_str}的商品，请尝试调整搜索条件。"
    
    # 有结果时格式化输出
    result = f"找到 {len(results)} 个符合条件的商品：\n\n"  # 结果数量概览
    for i, (product_id, product_name, description, category, price, stock, brand, specifications, image_url, status) in enumerate(results, 1):  # enumerate 从 1 开始编号
        result += f"{i}. {product_name}\n"  # 序号 + 商品名
        result += f"   商品ID：{product_id}\n"
        result += f"   价格：{price} 元\n"
        result += f"   库存：{stock} 件\n"
        if brand:  # 品牌（可选）
            result += f"   品牌：{brand}\n"
        if description:  # 描述（超过 50 字截断，避免 LLM 上下文过长）
            result += f"   描述：{description[:50]}...\n" if len(description) > 50 else f"   描述：{description}\n"
        result += "\n"  # 空行分隔
    
    return result


if __name__ == "__main__":
    # 独立运行 MCP Server（通过 stdio 与 Agent 通信）
    # 使用方式：python Mcpserver/product_mcp.py
    mcp.run(transport="stdio")  # stdio 传输：通过标准输入/输出与 Agent 进程通信
