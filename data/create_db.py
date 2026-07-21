# data/create_db.py
# 数据库初始化脚本：创建所有业务数据库表并插入示例数据
#
# 设计意图：
# - 一键初始化：运行此脚本即可创建全部 4 个 SQLite 数据库（订单/商品/物流/优惠券）
# - 幂等性：使用 IF NOT EXISTS + 检查 COUNT(*)，多次运行不会重复插入数据
# - 示例数据：插入有代表性的电商场景数据，覆盖正常/边界/异常情况
# - 为什么用多个 SQLite 文件而非一个：按业务领域分离，每个 MCP Server 只连接自己的数据库；
#     避免单文件锁竞争（SQLite 写锁是文件级别的）
import os  # 文件路径操作
import sqlite3  # SQLite 数据库驱动

# 数据库文件路径（相对于 data/ 目录）
ORDERS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "orders.db"))  # 订单数据库
PRODUCTS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "products.db"))  # 商品数据库


def init_orders_db() -> None:
    """
    初始化订单数据库：建表并插入示例数据（若表为空）。
    
    输入：无（使用模块级 ORDERS_DB_PATH）
    状态：SQLite 文件（创建或更新）
    输出：无（副作用：创建文件和写入数据）
    失败处理：SQLite 异常向上抛出（调用方在 __main__ 中捕获）
    
    orders 表字段说明：
    - order_no: 订单号（主键），如 "A1001"
    - user_id: 用户 ID（用于权限校验）
    - status: 订单状态（待支付/待发货/配送中/已签收等）
    - items: 商品列表（分号分隔的字符串）
    - amount: 订单金额
    - logistics: 物流简要描述
    - cancelable: 是否可取消（1=可取消, 0=不可取消）
    - updated_at: 最后更新时间
    """
    os.makedirs(os.path.dirname(ORDERS_DB_PATH), exist_ok=True)  # 确保目录存在
    conn = sqlite3.connect(ORDERS_DB_PATH)  # 创建/打开数据库文件
    try:
        cur = conn.cursor()  # 创建游标
        # 建表（IF NOT EXISTS 保证幂等）
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_no TEXT PRIMARY KEY,  -- 订单号，主键
                user_id TEXT NOT NULL,  -- 用户 ID，非空
                status TEXT NOT NULL,  -- 订单状态，非空
                items TEXT NOT NULL,  -- 商品列表（分号分隔）
                amount REAL NOT NULL,  -- 订单金额
                logistics TEXT DEFAULT '',  -- 物流信息
                cancelable INTEGER DEFAULT 1,  -- 是否可取消
                updated_at TEXT DEFAULT (datetime('now'))  -- 更新时间
            )
            """
        )
        # 仅在表为空时插入示例数据（幂等：避免重复插入）
        cur.execute("SELECT COUNT(*) FROM orders")  # 检查表是否已有数据
        if cur.fetchone()[0] == 0:  # 表为空才插入
            cur.executemany(  # 批量插入
                """
                INSERT INTO orders(
                    order_no, user_id, status, items, amount, logistics, cancelable
                ) VALUES (?,?,?,?,?,?,?)
                """,  # 参数化查询，防止 SQL 注入（虽然这里是硬编码数据）
                [
                    # 订单A1001：路由器 x1 + 网线 x2 = 298 + 15*2 = 328
                    ("A1001", "1", "待发货", "路由器 x1; 网线 x2", 328.0, "仓库已出库，等待揽收", 1),
                    # 订单A1002：键盘 x1 + 鼠标 x1 = 189 + 89 = 278（有物流轨迹的配送中订单）
                    ("A1002", "1", "配送中", "键盘 x1; 鼠标 x1", 278.0, "已到达上海转运中心，派送中", 1),
                    # 订单A1003：显示器 x1 = 1088（已签收，不可取消）
                    ("A1003", "2", "已签收", "显示器 x1", 1088.0, "已签收：收件人 张三", 0),
                    # 订单A1004：夏科有线键鼠套装 x1 = 50（待支付，可取消）
                    ("A1004", "1", "待支付", "夏科有线键鼠套装 x1", 50.0, "", 1),
                    # 订单A1005：无线耳机 x2 = 299*2 = 598（user_id=2 的订单）
                    ("A1005", "2", "待发货", "无线耳机 x2", 598.0, "订单已确认，等待发货", 1),
                    # 订单A1006：移动硬盘 x1 + U盘 x1 = 399 + 59 = 458（配送中，有物流轨迹）
                    ("A1006", "3", "配送中", "移动硬盘 x1; U盘 x1", 458.0, "已发货，运输中", 1),
                    # 订单A1007：摄像头 x1 = 199
                    ("A1007", "1", "待发货", "摄像头 x1", 199.0, "订单已确认，等待发货", 1),
                    # 订单A1008：音响 x1 = 399（已签收，不可取消）
                    ("A1008", "2", "已签收", "音响 x1", 399.0, "已签收：收件人 李四", 0),
                    # 订单A1009：打印机 x1 = 899（user_id=3 的订单）
                    ("A1009", "3", "待发货", "打印机 x1", 899.0, "订单已确认，等待发货", 1),
                    # 订单A1010：充电宝 x2 + 数据线 x3 = 129*2 + 29*3 = 345（配送中）
                    ("A1010", "1", "配送中", "充电宝 x2; 数据线 x3", 345.0, "已到达北京转运中心", 1),
                ],
            )
        conn.commit()  # 提交事务
    finally:
        conn.close()  # 确保连接关闭


def init_products_db() -> None:
    """
    初始化商品数据库：建表并插入示例数据（若表为空）。
    
    商品数据覆盖多个品类（电脑外设、家用电器、网络设备等），
    用于测试商品搜索和分类筛选功能。
    
    products 表字段说明：
    - product_id: 商品 ID（主键）
    - product_name: 商品名称
    - category: 分类（电脑外设/家用电器/网络设备等）
    - price: 价格（元）
    - stock: 库存数量
    - status: 商品状态（在售/下架等）
    """
    os.makedirs(os.path.dirname(PRODUCTS_DB_PATH), exist_ok=True)  # 确保目录存在
    conn = sqlite3.connect(PRODUCTS_DB_PATH)  # 创建/打开数据库
    try:
        cur = conn.cursor()
        # 建表（IF NOT EXISTS 保证幂等）
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,  -- 商品 ID
                product_name TEXT NOT NULL,  -- 商品名称
                description TEXT,  -- 商品描述
                category TEXT NOT NULL,  -- 分类
                price REAL NOT NULL,  -- 价格
                stock INTEGER DEFAULT 0,  -- 库存
                brand TEXT,  -- 品牌
                specifications TEXT,  -- 规格参数
                image_url TEXT,  -- 商品图片 URL
                status TEXT DEFAULT '在售',  -- 商品状态
                created_at TEXT DEFAULT (datetime('now')),  -- 创建时间
                updated_at TEXT DEFAULT (datetime('now'))  -- 更新时间
            )
            """
        )
        # 仅在表为空时插入示例数据
        cur.execute("SELECT COUNT(*) FROM products")  # 检查是否有数据
        if cur.fetchone()[0] == 0:  # 表为空
            cur.executemany(  # 批量插入 20 个商品
                """
                INSERT INTO products(
                    product_id, product_name, description, category, price, stock, brand, specifications, image_url, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("P001", "夏科有线键鼠套装", "USB接口，即插即用，适合办公和日常使用", "电脑外设", 50.0, 100, "夏科", "接口：USB 2.0；键盘：104键；鼠标：1200DPI", "https://www.example.cn/product/P001", "在售"),
                    ("P002", "M20洗衣机", "10公斤大容量，智能变频，节能省水", "家用电器", 2580.0, 25, "M20", "容量：10kg；能效等级：一级；洗涤方式：波轮", "https://www.example.cn/product/P002", "在售"),
                    ("P003", "路由器", "千兆双频，WiFi6，适合家庭和小型办公室", "网络设备", 298.0, 50, "品牌A", "速率：AX3000；频段：2.4G/5G双频；接口：4个千兆网口", "https://www.example.cn/product/P003", "在售"),
                    ("P004", "网线", "超五类网线，支持千兆网络，长度可选", "网络设备", 15.0, 200, "品牌B", "类型：超五类；长度：1米/3米/5米可选", "https://www.example.cn/product/P004", "在售"),
                    ("P005", "键盘", "机械键盘，青轴，RGB背光", "电脑外设", 189.0, 30, "品牌C", "轴体：青轴；键数：87键；背光：RGB", "https://www.example.cn/product/P005", "在售"),
                    ("P006", "鼠标", "无线鼠标，2.4G连接，人体工学设计", "电脑外设", 89.0, 80, "品牌D", "连接：2.4G无线；DPI：1600；电池：AA电池x1", "https://www.example.cn/product/P006", "在售"),
                    ("P007", "显示器", "27英寸，4K分辨率，IPS面板", "电脑外设", 1088.0, 15, "品牌E", "尺寸：27英寸；分辨率：3840x2160；面板：IPS", "https://www.example.cn/product/P007", "在售"),
                    ("P008", "笔记本电脑", "14英寸轻薄本，Intel i5处理器，8GB内存，512GB SSD", "电脑设备", 4999.0, 20, "品牌F", "CPU：Intel i5-1135G7；内存：8GB；硬盘：512GB SSD；屏幕：14英寸", "https://www.example.cn/product/P008", "在售"),
                    ("P009", "无线耳机", "蓝牙5.0，降噪，续航30小时", "音频设备", 299.0, 60, "品牌G", "连接：蓝牙5.0；降噪：主动降噪；续航：30小时", "https://www.example.cn/product/P009", "在售"),
                    ("P010", "移动硬盘", "1TB容量，USB 3.0接口，便携设计", "存储设备", 399.0, 45, "品牌H", "容量：1TB；接口：USB 3.0；尺寸：2.5英寸", "https://www.example.cn/product/P010", "在售"),
                    ("P011", "U盘", "64GB容量，USB 3.0，高速传输", "存储设备", 59.0, 150, "品牌I", "容量：64GB；接口：USB 3.0；读取速度：100MB/s", "https://www.example.cn/product/P011", "在售"),
                    ("P012", "摄像头", "1080P高清，自动对焦，内置麦克风", "电脑外设", 199.0, 35, "品牌J", "分辨率：1080P；对焦：自动对焦；麦克风：内置", "https://www.example.cn/product/P012", "在售"),
                    ("P013", "音响", "2.1声道，低音炮，蓝牙连接", "音频设备", 399.0, 28, "品牌K", "声道：2.1；功率：30W；连接：蓝牙/AUX", "https://www.example.cn/product/P013", "在售"),
                    ("P014", "打印机", "激光打印，黑白，自动双面", "办公设备", 899.0, 18, "品牌L", "类型：激光；颜色：黑白；双面：自动", "https://www.example.cn/product/P014", "在售"),
                    ("P015", "扫描仪", "A4幅面，2400DPI，USB连接", "办公设备", 599.0, 22, "品牌M", "幅面：A4；分辨率：2400DPI；接口：USB", "https://www.example.cn/product/P015", "在售"),
                    ("P016", "投影仪", "1080P高清，3000流明，支持无线投屏", "显示设备", 2999.0, 12, "品牌N", "分辨率：1080P；亮度：3000流明；连接：HDMI/无线", "https://www.example.cn/product/P016", "在售"),
                    ("P017", "智能手表", "健康监测，运动追踪，50米防水", "智能设备", 1299.0, 40, "品牌O", "屏幕：1.4英寸；防水：50米；续航：7天", "https://www.example.cn/product/P017", "在售"),
                    ("P018", "充电宝", "20000mAh容量，快充，双USB输出", "配件", 129.0, 90, "品牌P", "容量：20000mAh；输出：双USB；快充：支持", "https://www.example.cn/product/P018", "在售"),
                    ("P019", "数据线", "Type-C转USB，1米长度，支持快充", "配件", 29.0, 300, "品牌Q", "接口：Type-C转USB；长度：1米；快充：支持", "https://www.example.cn/product/P019", "在售"),
                    ("P020", "手机支架", "可调节角度，桌面/车载两用", "配件", 39.0, 200, "品牌R", "角度：可调节；用途：桌面/车载；材质：铝合金", "https://www.example.cn/product/P020", "在售"),
                ],
            )
        conn.commit()  # 提交事务
    finally:
        conn.close()  # 确保连接关闭


# 物流和优惠券数据库路径
LOGISTICS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "logistics.db"))
COUPONS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "coupons.db"))


def init_logistics_db() -> None:
    """
    初始化物流数据库：建表并插入示例物流轨迹。
    
    logistics_tracks 表存储物流节点（时间线），每条记录对应一个物流事件。
    与 orders 表通过 order_no 外键关联。
    """
    os.makedirs(os.path.dirname(LOGISTICS_DB_PATH), exist_ok=True)  # 确保目录存在
    conn = sqlite3.connect(LOGISTICS_DB_PATH)  # 创建/打开数据库
    try:
        cur = conn.cursor()
        # 物流节点表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logistics_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                order_no TEXT NOT NULL,  -- 关联的订单号
                carrier TEXT DEFAULT '顺丰速运',  -- 快递公司
                tracking_no TEXT,  -- 快递单号
                node_time TEXT NOT NULL,  -- 节点时间
                node_desc TEXT NOT NULL,  -- 节点描述（如"快件已揽收"）
                node_status TEXT DEFAULT '运输中',  -- 节点状态
                estimated_arrival TEXT,  -- 预计送达时间
                FOREIGN KEY (order_no) REFERENCES orders(order_no)  -- 外键约束
            )
            """
        )
        cur.execute("SELECT COUNT(*) FROM logistics_tracks")  # 检查是否已有数据
        if cur.fetchone()[0] == 0:  # 表为空才插入
            cur.executemany(
                """
                INSERT INTO logistics_tracks(order_no, carrier, tracking_no, node_time, node_desc, node_status, estimated_arrival)
                VALUES (?,?,?,?,?,?,?)
                """,
                [
                    # A1002 的物流轨迹（顺丰，4个节点，从揽收到派送）
                    ("A1002", "顺丰速运", "SF1234567890", "2026-06-01 08:00", "快件已揽收", "已揽收", "2026-06-04"),
                    ("A1002", "顺丰速运", "SF1234567890", "2026-06-01 14:00", "快件到达上海转运中心", "运输中", "2026-06-04"),
                    ("A1002", "顺丰速运", "SF1234567890", "2026-06-02 06:30", "快件到达北京转运中心", "运输中", "2026-06-04"),
                    ("A1002", "顺丰速运", "SF1234567890", "2026-06-03 09:15", "快件正在派送中，快递员：张师傅 138XXXX", "派送中", "2026-06-04"),
                    # A1006 的物流轨迹（中通，2个节点）
                    ("A1006", "中通快递", "ZTO9876543210", "2026-06-01 10:00", "快件已揽收", "已揽收", "2026-06-05"),
                    ("A1006", "中通快递", "ZTO9876543210", "2026-06-02 16:00", "快件到达广州转运中心", "运输中", "2026-06-05"),
                    # A1010 的物流轨迹（圆通，3个节点，已到派送阶段）
                    ("A1010", "圆通速递", "YT5555555555", "2026-06-01 09:00", "快件已揽收", "已揽收", "2026-06-03"),
                    ("A1010", "圆通速递", "YT5555555555", "2026-06-02 11:00", "快件到达北京转运中心", "运输中", "2026-06-03"),
                    ("A1010", "圆通速递", "YT5555555555", "2026-06-03 07:30", "快件正在派送中", "派送中", "2026-06-03"),
                ],
            )
        conn.commit()  # 提交事务
    finally:
        conn.close()  # 确保连接关闭


def init_coupons_db() -> None:
    """
    初始化优惠券数据库：建表并插入示例数据。
    
    包含两张表：
    - coupons: 用户优惠券（与 user_id 关联）
    - promotions: 平台促销活动（公开信息，不关联用户）
    """
    os.makedirs(os.path.dirname(COUPONS_DB_PATH), exist_ok=True)  # 确保目录存在
    conn = sqlite3.connect(COUPONS_DB_PATH)  # 创建/打开数据库
    try:
        cur = conn.cursor()
        # 优惠券表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                user_id TEXT NOT NULL,  -- 所属用户
                coupon_type TEXT DEFAULT '满减券',  -- 券类型（满减券/折扣券/无门槛券）
                amount REAL NOT NULL,  -- 优惠金额
                min_amount REAL DEFAULT 0,  -- 使用门槛（最低消费金额）
                description TEXT,  -- 优惠券描述
                status TEXT DEFAULT '未使用',  -- 状态（未使用/已使用/已过期）
                expire_date TEXT NOT NULL  -- 过期日期
            )
            """
        )
        # 促销活动表
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                title TEXT NOT NULL,  -- 活动标题
                description TEXT,  -- 活动描述
                start_date TEXT NOT NULL,  -- 开始日期
                end_date TEXT NOT NULL,  -- 结束日期
                priority INTEGER DEFAULT 0  -- 优先级（数字越大越优先展示）
            )
            """
        )
        cur.execute("SELECT COUNT(*) FROM coupons")  # 检查优惠券表
        if cur.fetchone()[0] == 0:  # 表为空
            cur.executemany(
                """INSERT INTO coupons(user_id, coupon_type, amount, min_amount, description, status, expire_date)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    # user_id=1 有 4 张券（覆盖不同状态：未使用/已过期）
                    ("1", "满减券", 50, 200, "新人专享50元券", "未使用", "2026-07-01"),  # 未过期
                    ("1", "满减券", 20, 100, "618年中大促", "未使用", "2026-06-30"),  # 未过期
                    ("1", "无门槛券", 10, 0, "签到领券", "未使用", "2026-06-15"),  # 无门槛券
                    # user_id=2 有 2 张券
                    ("2", "满减券", 100, 500, "会员专享", "未使用", "2026-12-31"),  # 大额券
                    ("2", "满减券", 30, 150, "周末特惠", "未使用", "2026-06-20"),
                    # user_id=3 有 2 张券（一张已使用，一张未使用）
                    ("3", "无门槛券", 5, 0, "新用户注册送", "已使用", "2026-05-01"),  # 已使用（不会被推荐）
                    ("3", "满减券", 80, 300, "618年中大促", "未使用", "2026-06-30"),
                    ("1", "折扣券", 40, 200, "88折券（上限40元）", "未使用", "2026-06-25"),  # 折扣券类型
                ],
            )
        cur.execute("SELECT COUNT(*) FROM promotions")  # 检查促销表
        if cur.fetchone()[0] == 0:  # 表为空
            cur.executemany(
                """INSERT INTO promotions(title, description, start_date, end_date, priority)
                   VALUES (?,?,?,?,?)""",
                [
                    ("618年中大促", "全场满300减50，部分商品低至5折！数码配件、家电好物限时抢购。", "2026-06-01", "2026-06-18", 10),  # 最高优先级
                    ("新用户专享", "首次下单立享9折优惠，最高减100元。注册即送50元新人礼包。", "2026-05-01", "2026-12-31", 5),  # 长期活动
                    ("会员日特惠", "每月15日会员专属折扣，积分翻倍，限量秒杀。", "2026-06-15", "2026-06-16", 8),  # 短期活动
                    ("夏日清凉节", "空调、风扇、冰箱等制冷设备满1000减100，以旧换新补贴加码。", "2026-06-10", "2026-07-10", 7),
                ],
            )
        conn.commit()  # 提交事务
    finally:
        conn.close()  # 确保连接关闭


if __name__ == "__main__":
    # 一键初始化所有数据库（按依赖顺序执行）
    init_orders_db()  # 先创建订单数据库（物流表引用订单号）
    print(f"订单数据库已初始化：{ORDERS_DB_PATH}")
    init_products_db()  # 创建商品数据库
    print(f"商品数据库已初始化：{PRODUCTS_DB_PATH}")
    init_logistics_db()  # 创建物流数据库
    print(f"物流数据库已初始化：{LOGISTICS_DB_PATH}")
    init_coupons_db()  # 创建优惠券数据库
    print(f"优惠券数据库已初始化：{COUPONS_DB_PATH}")
    # 运行方式：python data/create_db.py

