#!/bin/bash
# 电商智能客服系统 — 启动脚本
# 用法: bash run.sh

set -e

PYTHON="${PYTHON:-python}"

echo "=== 电商智能客服系统 ==="
echo "Python: $PYTHON"

# 检查 Python
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "错误: 未找到 Python 解释器: $PYTHON"
    echo "可通过 PYTHON=/path/to/python bash run.sh 指定解释器"
    exit 1
fi

# 检查 .env
if [ ! -f ".env" ]; then
    echo "错误: 未找到 .env 文件，请先配置环境变量"
    exit 1
fi

# 尝试启动 Redis（可选，失败不阻塞）
echo "[1/3] 启动 Redis..."
if command -v docker &> /dev/null; then
    docker compose up -d redis 2>/dev/null && echo "  Redis 已启动 (Docker)" || echo "  跳过: Docker 不可用"
else
    echo "  跳过: Docker 未安装，使用 MemorySaver 内存模式"
fi

# 安装依赖（首次运行）
echo "[2/3] 检查依赖..."
cd "$(dirname "$0")"
$PYTHON -c "import fastapi" 2>/dev/null || {
    echo "  正在安装依赖..."
    $PYTHON -m pip install -r requirements.txt
}
echo "  依赖就绪"

# 初始化数据库（首次运行）
if [ -f "data/orders.db" ] && [ -f "data/products.db" ]; then
    echo "  数据库已存在，跳过初始化"
else
    echo "  初始化数据库..."
    $PYTHON data/create_db.py && echo "  数据库初始化完成"
fi

# 启动应用
echo "[3/3] 启动 FastAPI 服务..."
echo "  前端: http://localhost:8000/html/index.html"
echo "  API文档: http://localhost:8000/docs"
echo ""
$PYTHON -X utf8 service/main.py
