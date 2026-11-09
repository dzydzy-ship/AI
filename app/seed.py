"""演示数据：首次启动时若库为空，写入示例商品并采集一批热点。

新增（促销利润防护）：真实 SKU 与默认口径各一份。
各表分别判断、缺什么补什么，重复启动不会写重。

环境变量 SEED_DEMO=0 可跳过那 3 个示例商品（保温杯/防晒霜/机械键盘），
只留 4 款真实飞机——比赛演示时下拉框干净一些。
"""
import os

from sqlalchemy.orm import Session

from .config import load_yaml
from .models import Product, PromoConfig, SportProfile
from .services.hotspots import collect_hotspots


def seed_if_empty(db: Session) -> None:
    want_demo = os.environ.get("SEED_DEMO", "1") != "0"
    if want_demo and db.query(Product).count() == 0:
        for p in demo_products():
            db.add(p)
        db.commit()
        collect_hotspots(db)
    seed_real_skus(db)
    seed_sports_skus(db)
    seed_sport_profiles(db)
    seed_promo_config(db)
    db.commit()


def demo_products():
    return [
        Product(
            name="316不锈钢保温杯",
            brand="水尚",
            category="杯壶",
            specs={"容量": "500ml", "材质": "316不锈钢", "保温时长": "12小时"},
            price=129.0,
            cost_price=55.0,
            selling_points=["316不锈钢内胆", "12小时长效保温", "便携防漏", "轻量化设计"],
            target_audience={"年龄段": "20-40", "场景": ["户外", "通勤", "办公"], "需求": ["补水", "保温"], "顾虑": ["漏水", "异味"]},
            brand_tone="简约、健康",
            promotion="",
            evidence=["材质检测报告"],
            restrictions=["不得宣称医疗功效"],
            missing_fields=[],
            tags={"品类": ["保温杯", "杯壶"], "功能": ["保温", "防漏", "便携"], "场景": ["户外", "通勤", "办公"], "人群": ["上班族", "学生", "户外爱好者"], "痛点": ["喝水少", "怕漏水"], "价格带": ["中端"], "可蹭热点类型": ["季节", "健康生活", "户外露营"]},
            raw_material="316不锈钢保温杯，容量500ml，材质316不锈钢，保温时长12小时，便携防漏。",
        ),
        Product(
            name="高倍防晒霜",
            brand="晴护",
            category="个护",
            specs={"SPF": "50+", "规格": "60ml", "质地": "清爽不油腻"},
            price=89.0,
            cost_price=30.0,
            selling_points=["SPF50+高倍防护", "清爽不闷痘", "防水防汗", "敏感肌可用"],
            target_audience={"年龄段": "18-35", "场景": ["户外", "通勤"], "需求": ["防晒", "清爽"], "顾虑": ["闷痘", "搓泥"]},
            brand_tone="专业、清爽",
            promotion="",
            evidence=["防晒特证", "SPF检测报告"],
            restrictions=["不得宣称晒不黑等绝对化效果"],
            missing_fields=[],
            tags={"品类": ["防晒霜", "个护"], "功能": ["防晒", "防水", "清爽"], "场景": ["户外", "通勤"], "人群": ["学生", "上班族", "女性"], "痛点": ["晒黑", "闷痘"], "价格带": ["中端"], "可蹭热点类型": ["夏季", "户外露营", "美白护肤"]},
            raw_material="高倍防晒霜 SPF50+ 60ml 清爽不油腻，防水防汗，敏感肌可用。",
        ),
        Product(
            name="客制化机械键盘",
            brand="键魂",
            category="数码外设",
            specs={"配列": "75%", "轴体": "线性轴", "连接": "三模无线"},
            price=399.0,
            cost_price=210.0,
            selling_points=["热插拔轴体", "三模无线连接", "Gasket结构", "RGB背光"],
            target_audience={"年龄段": "18-35", "场景": ["电竞", "办公"], "需求": ["手感", "外观"], "顾虑": ["延迟", "耐用"]},
            brand_tone="极客、性能",
            promotion="",
            evidence=["轴体寿命测试报告"],
            restrictions=["不得宣称零延迟"],
            missing_fields=[],
            tags={"品类": ["机械键盘", "数码外设"], "功能": ["热插拔", "三模无线", "RGB"], "场景": ["电竞", "办公"], "人群": ["玩家", "程序员"], "痛点": ["手感差", "延迟"], "价格带": ["中端"], "可蹭热点类型": ["电竞", "开学季", "数码新品"]},
            raw_material="客制化机械键盘，75%配列，热插拔线性轴，三模无线，Gasket结构。",
        ),
    ]


# ---------------------------------------------------------------------------
# 真实 SKU：来自一家自营进口模型店（出厂价与店铺标价都是真实数字）
# ---------------------------------------------------------------------------
REAL_SKUS = [
    dict(
        name="SR-72 侦察机遥控模型（FX9672）",
        brand="飞熊玩具",
        category="遥控飞机",
        specs={"通道": "两通道", "机型": "SR-72", "包装": "彩盒", "适龄": "14 岁以上"},
        price=298.0,
        cost_price=238.0,
        selling_points=["像真度高，摆件与飞行两用", "两通道易上手，新手半小时能飞",
                        "RTF 到手飞，含遥控器与电池", "海外原厂，配件可续购"],
        target_audience={"年龄段": "14+", "场景": ["送礼", "收藏", "户外飞行"],
                         "需求": ["好飞", "像真"], "顾虑": ["易摔", "配件难买"]},
        brand_tone="专业、可信",
        promotion="满两件八折；店铺券 ¥29 无门槛",
        evidence=["原厂授权书"],
        restrictions=["不得宣称军用用途", "不得使用绝对化表述"],
        missing_fields=[],
        tags={"品类": ["遥控飞机", "模型"], "功能": ["像真", "到手飞", "两通道"], "场景": ["送礼", "收藏", "户外飞行"], "人群": ["青少年", "航空迷", "航模爱好者"], "痛点": ["易摔", "配件难买"], "价格带": ["中端"], "可蹭热点类型": ["低空经济", "航展", "国庆出游", "亲子"]},
        raw_material="SR-72 侦察机遥控模型，两通道，彩盒包装，RTF 到手飞，适龄 14 岁以上。",
    ),
    dict(
        name="J20 涵道遥控飞机（FX9630）",
        brand="飞熊玩具",
        category="遥控飞机",
        specs={"通道": "四通道", "机型": "J20", "动力": "涵道", "包装": "彩盒"},
        price=288.0,
        cost_price=228.0,
        selling_points=["涵道动力，推力更稳", "四通道可完成横滚", "像真涂装"],
        target_audience={"年龄段": "14+", "场景": ["进阶飞行", "收藏"],
                         "需求": ["操控感", "像真"], "顾虑": ["炸机", "维修成本"]},
        brand_tone="专业、硬核",
        promotion="满两件八折",
        evidence=["原厂授权书"],
        restrictions=["不得宣称军用用途"],
        missing_fields=[],
        tags={"品类": ["遥控飞机", "模型"], "功能": ["涵道动力", "四通道", "像真"], "场景": ["进阶飞行", "收藏"], "人群": ["航模爱好者", "航空迷"], "痛点": ["炸机", "维修成本"], "价格带": ["中端"], "可蹭热点类型": ["军迷", "航展", "低空经济"]},
        raw_material="J20 涵道遥控飞机，四通道，涵道动力，彩盒包装。",
    ),
    dict(
        name="赛斯纳 182 遥控飞机（FX9601）",
        brand="飞熊玩具",
        category="遥控飞机",
        specs={"通道": "三通道", "机型": "赛斯纳 182", "翼展": "约 500mm"},
        price=328.0,
        cost_price=228.0,
        selling_points=["上单翼布局，起降温和", "三通道适合练手", "翼展大，抗风好"],
        target_audience={"年龄段": "12+", "场景": ["新手练习", "亲子"],
                         "需求": ["好飞", "耐摔"], "顾虑": ["难起飞"]},
        brand_tone="亲切、入门友好",
        promotion="满两件八折",
        evidence=[],
        restrictions=["不得使用绝对化表述"],
        missing_fields=[],
        tags={"品类": ["遥控飞机", "模型"], "功能": ["上单翼", "三通道", "新手友好"], "场景": ["新手练习", "亲子"], "人群": ["入门玩家", "亲子家庭"], "痛点": ["难起飞"], "价格带": ["中端"], "可蹭热点类型": ["亲子", "开学季", "周末出游"]},
        raw_material="赛斯纳 182 遥控飞机，三通道上单翼，翼展约 500mm。",
    ),
    dict(
        # 故意留空成本：用来演示"缺口径就追问、绝不给数字"
        name="K270 黑鹰直升机（FX9640）",
        brand="伟力",
        category="遥控直升机",
        specs={"通道": "四通道", "机型": "K270", "桨叶": "无刷"},
        price=369.99,
        cost_price=None,
        selling_points=["无刷四通道", "到手飞"],
        target_audience={"年龄段": "16+", "场景": ["进阶飞行"], "需求": ["操控"], "顾虑": ["易损"]},
        brand_tone="专业",
        promotion="满两件八折",
        evidence=[],
        restrictions=[],
        missing_fields=["进货成本"],
        tags={"品类": ["遥控直升机", "模型"], "功能": ["无刷", "四通道", "到手飞"], "场景": ["进阶飞行"], "人群": ["航模爱好者"], "痛点": ["易损"], "价格带": ["中端"], "可蹭热点类型": ["低空经济", "航展"]},
        raw_material="全新伟力无刷 K270 黑鹰四通道直升机，到手飞。",
    ),
]


def seed_real_skus(db: Session) -> int:
    """补三条真实飞机 SKU（已在库则跳过）。"""
    added = 0
    for item in REAL_SKUS:
        exists = db.query(Product).filter(Product.name == item["name"]).first()
        if exists:
            continue
        db.add(Product(**item))
        added += 1
    return added


# 体育特化演示 SKU：跑鞋 / 球拍 / 护具各一件，覆盖「装备-耗材-护具」三类
SPORTS_SKUS = [
    dict(
        name="破风 R5 全掌碳板竞速跑鞋",
        brand="破风",
        category="跑鞋",
        specs={"重量": "235g（42码单只）", "中底": "全掌碳板+超临界发泡", "鞋面": "工程网布", "适用": "半马/全马"},
        price=699.0,
        cost_price=320.0,
        selling_points=["全掌碳纤维板", "超临界发泡中底", "235g 轻量", "宽楦不挤脚"],
        target_audience={"年龄段": "22-45", "场景": ["马拉松", "日常训练"],
                         "需求": ["轻量", "回弹"], "顾虑": ["碳板不适", "不耐磨"]},
        brand_tone="专业但不端着",
        promotion="满两件八折",
        evidence=[],
        restrictions=["不得承诺提升成绩", "不得使用绝对化表述"],
        missing_fields=[],
        tags={"品类": ["跑鞋", "运动鞋"], "功能": ["碳板", "回弹", "轻量"],
              "场景": ["马拉松", "训练"], "人群": ["跑者", "进阶跑者"],
              "痛点": ["磨脚", "鞋子太重"], "价格带": ["中高端"],
              "可蹭热点类型": ["马拉松", "赛事", "跑步打卡"]},
        raw_material="破风 R5 全掌碳板跑鞋，42码单只235g，超临界发泡中底，工程网布鞋面，适合半马全马。",
    ),
    dict(
        name="羽将 A7 全碳素羽毛球拍",
        brand="羽将",
        category="羽毛球拍",
        specs={"重量": "4U（82g）", "平衡点": "295mm", "磅数": "≤28磅", "材质": "全碳素"},
        price=499.0,
        cost_price=210.0,
        selling_points=["全碳素一体成型", "4U 轻量易挥拍", "平衡点 295mm 攻守均衡", "可穿 28 磅"],
        target_audience={"年龄段": "18-50", "场景": ["俱乐部", "周末约球"],
                         "需求": ["轻", "控球"], "顾虑": ["断拍", "震手"]},
        brand_tone="干脆、懂球",
        promotion="满两件八折",
        evidence=[],
        restrictions=["不得使用绝对化表述"],
        missing_fields=[],
        tags={"品类": ["羽毛球拍", "球拍"], "功能": ["全碳素", "轻量", "攻守均衡"],
              "场景": ["球馆", "约球"], "人群": ["业余球友", "进阶球友"],
              "痛点": ["挥拍慢", "震手"], "价格带": ["中端"],
              "可蹭热点类型": ["羽毛球联赛", "全民健身"]},
        raw_material="羽将 A7 全碳素羽毛球拍，4U 82g，平衡点 295mm，最高可穿 28 磅。",
    ),
    dict(
        name="稳膝 K3 髌骨支撑护膝",
        brand="稳膝",
        category="护膝",
        specs={"支撑": "双侧弹簧条", "面料": "高弹针织", "尺码": "M/L/XL"},
        price=159.0,
        cost_price=62.0,
        selling_points=["双侧弹簧支撑", "髌骨双环减压", "高弹透气面料", "可调节绑带"],
        target_audience={"年龄段": "30-60", "场景": ["跑步", "球类", "爬楼"],
                         "需求": ["稳定", "减压"], "顾虑": ["闷热", "下滑"]},
        brand_tone="克制、讲清边界",
        promotion="满两件八折",
        evidence=["质检报告编号 ST-2024-0917"],
        restrictions=["不得出现治疗/康复功效表述", "提认证必须带编号"],
        missing_fields=[],
        tags={"品类": ["护膝", "护具"], "功能": ["髌骨支撑", "减压", "透气"],
              "场景": ["跑步", "球类"], "人群": ["膝盖不适", "中老年跑者"],
              "痛点": ["膝盖疼", "老下滑"], "价格带": ["中端"],
              "可蹭热点类型": ["马拉松", "康复训练"]},
        raw_material="稳膝 K3 髌骨支撑护膝，双侧弹簧条、髌骨双环减压、高弹透气，M/L/XL 可选。",
    ),
]

# 演示 SKU 的运动档案（重量/退货率/认证编号——E 分与账单口径都靠它）
SPORT_PROFILES = {
    "破风 R5 全掌碳板竞速跑鞋": dict(
        sport="running", sub_category="竞速跑鞋", level="advanced",
        size_system="EU", size_range="39-46", weight_kg=0.9,
        package_dims="33x22x12", return_rate=0.12,
        notes="碳板鞋，脚宽建议大半码",
    ),
    "羽将 A7 全碳素羽毛球拍": dict(
        sport="badminton", sub_category="球拍", level="intermediate",
        size_system="均码", size_range="4U", weight_kg=0.45,
        package_dims="70x25x8", return_rate=0.08,
        notes="空拍重量，不含拍套",
    ),
    "稳膝 K3 髌骨支撑护膝": dict(
        sport="fitness", sub_category="护具", level="beginner",
        size_system="均码", size_range="M-XXL", weight_kg=0.35,
        package_dims="25x18x10", return_rate=0.10,
        cert_type="质检报告", cert_no="ST-2024-0917",
        notes="护具，写认证必须带这个编号",
    ),
}


def seed_sports_skus(db: Session) -> int:
    """补三件运动装备 SKU（已在库则跳过）。"""
    added = 0
    for item in SPORTS_SKUS:
        exists = db.query(Product).filter(Product.name == item["name"]).first()
        if exists:
            continue
        db.add(Product(**item))
        added += 1
    return added


def seed_sport_profiles(db: Session) -> int:
    """给演示 SKU 补运动档案（已存在则跳过），让赛事雷达的 E 分不是拍脑袋。

    会话是 autoflush=False，所以先手动 flush：否则刚 add 还没落库的 SKU
    在下面的查询里查不到，档案会全部漏写。
    """
    db.flush()
    added = 0
    for name, attrs in SPORT_PROFILES.items():
        product = db.query(Product).filter(Product.name == name).first()
        if product is None:
            continue
        exists = db.query(SportProfile).filter(SportProfile.product_id == product.id).first()
        if exists:
            continue
        db.add(SportProfile(product_id=product.id, **attrs))
        added += 1
    return added


def seed_promo_config(db: Session) -> int:
    """写入默认测算口径（对应 config/pricing_policy.yaml）。"""
    if db.query(PromoConfig).count() > 0:
        return 0
    defaults = load_yaml("pricing_policy.yaml")
    d = defaults.get("defaults", {})
    db.add(PromoConfig(
        name="淘宝店（默认口径）",
        platform="taobao",
        commission_rate=d.get("commission_rate", 0.06),
        commission_base=d.get("commission_base", "gmv_after_coupon"),
        ship_fee=d.get("ship_fee", 19.9),
        freight_dom=d.get("freight_dom", 19.9),
        freight_cover=d.get("freight_cover", 0.0),
        coupon_borne_by=d.get("coupon_borne_by", "merchant"),
        tier_discounts=d.get("tier_discounts", {"2": 0.8}),
        profit_floor_pct=defaults.get("profit_floor_pct", 5.0),
        is_default=True,
        note="佣金 6%、买家自付运费 19.9、国内发货运费 19.9（收付相抵后计净成本）",
    ))
    return 1