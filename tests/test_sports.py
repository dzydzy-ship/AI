# -*- coding: utf-8 -*-
"""体育特化层的口径测试：赛事日历、重量运费、双口径对照、保本反推、合规闸、E 分与硬门槛。

跑法（必须单独跑，不能和 tests/_stubs.py 混在一个进程里）：
    cd 店子宠物
    .venv\\Scripts\\python.exe -m unittest tests.test_sports -v

为什么不能混跑：tests/_stubs.py 会把 sqlalchemy / pydantic / yaml 换成极简替身，
本模块要的是真库（JSON 列、真实 YAML 结构），混跑会拿到替身而错乱，所以这里主动跳过。

这些用例只验证"数字与判定"，不碰网络与真实大模型（LLM_API_KEY 置空即 Mock 模式）。
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_API_KEY"] = ""

# 与 tests/_stubs.py 同进程时（python -m unittest discover）跳过本模块：
# 替身库没有真正的 JSON 列与 YAML 结构，测出来的结论没有意义。
if "tests._stubs" in sys.modules:
    raise unittest.SkipTest("tests/_stubs.py 已生效，请单独运行 tests.test_sports")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import Product  # noqa: E402
from app.services import matching, sports, sports_content, sports_pricing  # noqa: E402
from app.services.pricing import PricingInput  # noqa: E402


def _input(**kw):
    base = dict(
        sku="SKU-TEST",
        price=399.0,
        qty=2,
        unit_cost=268.0,
        freight_intl=0.0,
        freight_dom=19.9,
        freight_cover=0.0,
        tier_rate=0.85,
        coupon=50.0,
        commission=0.06,
        commission_base="gmv_after_coupon",
        ship_fee=12.0,
    )
    base.update(kw)
    return PricingInput(**base)


class SportsConfigTest(unittest.TestCase):
    def test_taxonomy_has_eight_sports_and_forms(self):
        data = sports.taxonomy()
        self.assertEqual(len(data["sports"]), 8)
        for key in ("running", "badminton", "fitness", "football", "basketball", "cycling", "snow", "outdoor"):
            self.assertIn(key, data["sports"])
            self.assertTrue(data["sports"][key]["name"])
            self.assertTrue(data["sports"][key]["keywords"])
        self.assertEqual(len(sports_content.FORMS), 4)

    def test_calendar_windows_and_countdown(self):
        events = sports.list_events(days_ahead=90, today=date(2026, 11, 5))
        self.assertGreaterEqual(len(events), 6)
        by_name = {e["name"]: e for e in events}
        self.assertIn("北京马拉松", by_name)
        bj = by_name["北京马拉松"]
        self.assertEqual(bj["countdown"], 10)
        self.assertEqual(bj["window"], "prepare")
        # 排序：按日期升序，最近的在最前
        self.assertEqual(events[0]["name"], min(by_name, key=lambda n: by_name[n]["date"]))

    def test_unknown_event_returns_none(self):
        self.assertIsNone(sports.event_by_key("not-a-real-event"))


class FreightTest(unittest.TestCase):
    def test_by_weight_tiers(self):
        # 首重 1kg 12 元，续重 0.5kg 8 元：2.3kg → 12 + ceil(1.3/0.5)*8 = 36
        r = sports_pricing.freight_fee(2.3)
        self.assertEqual(r["billable_kg"], 2.3)
        self.assertEqual(r["fee"], 36.0)
        self.assertEqual(sports_pricing.freight_fee(0.6)["fee"], 12.0)

    def test_volumetric_weight_wins(self):
        # 40x30x20 / 6000 = 4kg，体积重大于实重
        r = sports_pricing.freight_fee(1.0, "40x30x20")
        self.assertEqual(r["volumetric_kg"], 4.0)
        self.assertEqual(r["billable_kg"], 4.0)
        self.assertEqual(r["fee"], 12.0 + (4.0 - 1.0) / 0.5 * 8.0)

    def test_fixed_policy_still_available(self):
        self.assertEqual(sports_pricing.freight_fee(2.3, policy_name="fixed")["fee"], 19.9)


class QuoteTest(unittest.TestCase):
    def setUp(self):
        self.q = sports_pricing.quote(_input(), weight_kg=2.3, return_rate=0.15)

    def test_bill_reuses_legacy_shape(self):
        bill = self.q["账单"]
        for key in ("成交额", "平台佣金", "利润", "利润率"):
            self.assertIn(key, bill)
        # 成交额 = 399*2*0.85 - 50 = 628.30
        self.assertAlmostEqual(bill["成交额"], 628.3, places=2)

    def test_dual_caliber_explains_gap(self):
        cmp_ = self.q["口径对照"]
        self.assertIn("体育口径", cmp_)
        self.assertIn("通用口径", cmp_)
        self.assertGreater(cmp_["高估金额"], 0)  # 固定 19.9 < 36，通用口径必然高估
        self.assertIn("高估", cmp_["结论"])

    def test_return_loss_is_lined_out(self):
        loss = self.q["退货期望损失"]
        self.assertEqual(loss["退货率"], 15.0)  # 服务里「退货率」按百分数展示
        self.assertGreater(loss["损失"], 0)
        self.assertLess(self.q["退货后利润"], self.q["账单"]["利润"])

    def test_target_margin_boundary(self):
        cheap = sports_pricing.quote(_input(price=120.0, unit_cost=100.0), weight_kg=2.3)
        self.assertLess(cheap["账单"]["利润率"], 5.0)


class MaxDiscountTest(unittest.TestCase):
    def test_discount_floor_verifies(self):
        r = sports_pricing.max_discount(_input(), target_margin_pct=5.0, weight_kg=2.3)
        self.assertIn("折扣下限", r)
        self.assertIsNotNone(r["折扣下限"])
        check = r["验证"]["按折扣下限测算"]
        self.assertGreaterEqual(check["利润率"], 4.99)
        self.assertGreaterEqual(r["折扣下限"], 0.0)
        self.assertLessEqual(r["折扣下限"], 1.0)

    def test_coupon_ceiling_verifies(self):
        r = sports_pricing.max_discount(_input(), target_margin_pct=5.0, weight_kg=2.3)
        self.assertGreaterEqual(r["券额上限"], 0.0)
        self.assertLess(r["券额上限"], 50.0)  # 当前券 50 已经跌破红线，上限应更小


class ComplianceTest(unittest.TestCase):
    def test_medical_claim_blocked(self):
        p = Product(name="稳膝 K3 髌骨支撑护膝", category="护膝", price=159, cost_price=80)
        res = sports.check_compliance("这款护膝能包治膝盖疼，戴上就不疼了", p, {})
        self.assertFalse(res["passed"])
        self.assertTrue(res["hits"])

    def test_clean_copy_passes(self):
        p = Product(name="羽将 A7 全碳素羽毛球拍", category="羽毛球拍", price=499, cost_price=300)
        res = sports.check_compliance("4U 82g，平衡点 295mm，适合拉吊突击打法。", p, {})
        self.assertTrue(res["passed"])
        self.assertTrue(res["disclaimer"])


class ContentTest(unittest.TestCase):
    def setUp(self):
        self.p = Product(
            name="破风 R5 全掌碳板竞速跑鞋",
            category="跑鞋",
            price=899,
            cost_price=520,
            selling_points=["全掌碳板", "轻量回弹", "透气网面"],
            target_audience={"场景": ["半马", "全马"]},
        )
        self.event = sports.event_by_key("bm2026")

    def test_generate_within_platform_limits(self):
        if self.event is None:
            self.skipTest("日历里没有北京马拉松")
        r = sports_content.generate(self.p, None, self.event, "checklist", "xiaohongshu")
        self.assertLessEqual(len(r["title"]), r["limits"]["title_max"])
        self.assertLessEqual(len(r["tags"]), r["limits"]["tags_max"])
        self.assertEqual(r["limits"]["title_max"], 20)  # 小红书标题 20 字硬限制
        self.assertTrue(r["fact_check"]["passed"])
        self.assertTrue(r["can_publish"])

    def test_forms_all_available(self):
        for key in sports_content.FORMS:
            self.assertTrue(sports_content.form_meta(key)["name"])


class DbCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.product = Product(
            name="破风 R5 全掌碳板竞速跑鞋",
            category="跑鞋",
            price=899,
            cost_price=520,
            selling_points=["全掌碳板", "轻量化"],
            tags={"品类": ["跑鞋"], "功能": ["碳板"]},
        )
        self.db.add(self.product)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_guess_sport_from_category(self):
        self.assertEqual(sports.guess_sport(self.product), "running")

    def test_profile_defaults_are_estimates(self):
        prof = sports.get_profile(self.db, self.product)
        self.assertTrue(prof["weight_estimated"])
        self.assertGreater(prof["weight_kg"], 0)

    def test_event_fit_rewards_same_sport(self):
        event = sports.event_by_key("bm2026")
        if event is None:
            self.skipTest("日历里没有北京马拉松")
        fit = sports.event_fit_score(self.product, event, sports.get_profile(self.db, self.product))
        self.assertGreaterEqual(fit["e"], 60)
        self.assertGreater(fit["project"], 0)

    def test_sync_event_hotspots_is_idempotent(self):
        first = sports.sync_event_hotspots(self.db, days_ahead=60)
        second = sports.sync_event_hotspots(self.db, days_ahead=60)
        self.assertEqual(second["created"], 0)
        self.assertGreaterEqual(first["created"] + first["updated"], 1)

    def test_matching_uses_sports_weights_and_gate(self):
        sports.sync_event_hotspots(self.db, days_ahead=60)
        rows = matching.run_matching(self.db, product_ids=[self.product.id])
        self.assertTrue(rows)
        event_rows = [m for m in rows if "e" in (m.subscores or {})]
        self.assertTrue(event_rows, "至少应有一张赛事热点机会卡")
        m = event_rows[0]
        weights = matching.W_SPORTS
        expected = round(sum(weights[k] * m.subscores[k] for k in weights), 1)
        self.assertAlmostEqual(m.score, expected, places=1)
        self.assertGreaterEqual(m.subscores["e"], matching.E_GATE)

    def test_plans_are_idempotent(self):
        event = sports.event_by_key("bm2026")
        if event is None:
            self.skipTest("日历里没有北京马拉松")
        a = sports.create_plans(self.db, "bm2026", product_id=self.product.id)
        b = sports.create_plans(self.db, "bm2026", product_id=self.product.id)
        self.assertTrue(a)
        self.assertEqual(len(a), len(b))


class SeedTest(unittest.TestCase):
    """回归：会话 autoflush=False 时，刚 add 未 flush 的 SKU 曾导致运动档案全部漏写。"""

    def test_profiles_written_for_fresh_db(self):
        from app import seed
        from app.models import SportProfile

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            seed.seed_sports_skus(db)
            added = seed.seed_sport_profiles(db)
            db.commit()
            self.assertEqual(added, 3)
            self.assertEqual(db.query(SportProfile).count(), 3)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
