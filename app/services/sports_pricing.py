"""体育口径测算：重量运费、退货期望损失、双口径对照、保本反推、活动体检。

为什么单独一个模块：
    `pricing.py` 是"平台无关的纯计价内核"，它的入参是显式金额（运费按元传进来）。
    体育特化要做的是"**把隐藏成本算出来再喂给它**"——重量计费、体积重、退货期望损失，
    这些是费用生成逻辑，不是计价公式本身。分开放，两者都能单独测。
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Product
from . import pricing, sports

# 固定运费口径的历史默认值（与 pricing_policy.yaml 的 freight_dom 一致）
FIXED_FREIGHT = 19.9


def policy(name: Optional[str] = None) -> Dict[str, Any]:
    cfg = sports.shipping()
    policies = cfg.get("policies") or {}
    key = name or cfg.get("default_policy") or "by_weight"
    return dict(policies.get(key) or {}, name=key)


def _parse_dims(raw: str) -> Optional[float]:
    """'40x30x20' → 24000（cm³）；解析失败返回 None。"""
    if not raw:
        return None
    parts = str(raw).lower().replace("×", "x").replace("*", "x").split("x")
    if len(parts) != 3:
        return None
    try:
        nums = [float(p.strip()) for p in parts]
    except ValueError:
        return None
    if min(nums) <= 0:
        return None
    return nums[0] * nums[1] * nums[2]


def freight_fee(weight_kg: float, dims: str = "",
                policy_name: Optional[str] = None) -> Dict[str, Any]:
    """算一单国内发货运费（体育口径：按计费重量）。

    计费重量 = max(实重, 体积重)，体积重 = 体积/vol_divisor；
    运费 = 首重价 + ceil((计费重量 - 首重) / 续重档) * 续重价。
    """
    cfg = policy(policy_name)
    if cfg.get("mode") != "by_weight":
        fee = float(cfg.get("fee", FIXED_FREIGHT))
        return {
            "policy": cfg.get("name", ""),
            "mode": "fixed",
            "weight_kg": round(float(weight_kg or 0), 3),
            "billable_kg": round(float(weight_kg or 0), 3),
            "volumetric_kg": 0.0,
            "fee": round(fee, 2),
            "breakdown": f"固定运费 {fee:.2f} 元（通用口径，未按重量计费）",
        }

    # 注意：first_kg 在配置里是「首重的运费」，首重的重量固定取 first_weight_kg（默认 1kg）。
    # 早期版本把 first_kg 当成重量阈值，2.3kg 的球拍会被算成 0 档续重 → 运费只剩 12 元，已修正。
    base_fee = float(cfg.get("first_kg_fee", cfg.get("first_kg", 12.0)))
    first_weight = float(cfg.get("first_weight_kg", 1.0))
    step = float(cfg.get("add_step_kg", 0.5)) or 0.5
    add = float(cfg.get("add_fee", 8.0))
    divisor = float(cfg.get("vol_divisor", 6000)) or 6000.0
    remote = float(cfg.get("remote_surcharge", 0.0) or 0.0)

    actual = max(float(weight_kg or 0), 0.0)
    volume = _parse_dims(dims)
    volumetric = round(volume / divisor, 3) if volume else 0.0
    billable = max(actual, volumetric)
    steps = max(0, math.ceil(round((billable - first_weight) / step, 6)))
    fee = base_fee + steps * add + remote
    parts = [f"首重 {first_weight:.2f}kg {base_fee:.2f} 元"]
    if steps:
        parts.append(f"续重 {steps} 档 × {add:.2f} 元")
    if volume:
        parts.append(f"体积重 {volumetric:.2f}kg（{volume / divisor:.2f}）")
    return {
        "policy": cfg.get("name", ""),
        "mode": "by_weight",
        "weight_kg": round(actual, 3),
        "volumetric_kg": volumetric,
        "billable_kg": round(billable, 3),
        "fee": round(fee, 2),
        "breakdown": " + ".join(parts) + f" = {fee:.2f} 元（计费重 {billable:.2f}kg）",
    }


def return_loss(inp: pricing.PricingInput, return_rate: float) -> Dict[str, Any]:
    """退货期望损失 = 退货率×(发+退运费) + 退货率×折损率×商品成本。"""
    cfg = sports.shipping().get("return") or {}
    rate = float(return_rate if return_rate is not None else cfg.get("default_rate", 0.15))
    rate = max(0.0, min(1.0, rate))
    shrink = float(cfg.get("shrink_rate", 0.3))
    back = float(cfg.get("return_ship_fee", 19.9))
    ship = float(inp.freight_dom or 0.0) + back
    goods = float(inp.unit_cost or 0.0) * int(inp.qty or 1)
    loss = rate * ship + rate * shrink * goods
    return {
        "退货率": round(rate * 100, 1),
        "双向运费": round(ship, 2),
        "折损率": round(shrink * 100, 1),
        "损失": round(loss, 2),
        "公式": f"{rate:.0%}×{ship:.2f} + {rate:.0%}×{shrink:.0%}×{goods:.2f}",
    }


def quote(inp: pricing.PricingInput, weight_kg: Optional[float] = None,
          dims: str = "", return_rate: Optional[float] = None,
          policy_name: Optional[str] = None,
          compare: bool = True) -> Dict[str, Any]:
    """体育口径的完整账单：重量运费 + 双口径对照 + 退货期望损失。

    返回值里的「账单」与旧接口完全同构（多两行成本），前端可复用旧渲染；
    「口径对照」是新增的一整块，用来回答"通用口径把利润高估了多少"。
    """
    weight = float(weight_kg if weight_kg is not None else 1.0)
    freight = freight_fee(weight, dims, policy_name)

    weight_inp = replace(inp, freight_dom=freight["fee"])
    bill = pricing.run(weight_inp)

    fixed_inp = replace(inp, freight_dom=FIXED_FREIGHT)
    fixed_bill = pricing.run(fixed_inp)
    diff = round(fixed_bill["利润"] - bill["利润"], 2)

    loss = return_loss(weight_inp, return_rate if return_rate is not None else 0.15)
    profit_after = round(bill["利润"] - loss["损失"], 2)
    margin_after = round(profit_after / bill["成交额"] * 100, 1) if bill["成交额"] else 0.0

    payload: Dict[str, Any] = {
        "账单": bill,
        "口径对照": {
            "体育口径": {
                "名称": "按重量计费（体育口径）",
                "运费": freight["fee"],
                "计费重量": freight["billable_kg"],
                "利润": bill["利润"],
                "利润率": bill["利润率"],
                "说明": freight["breakdown"],
            },
            "通用口径": {
                "名称": "固定运费（通用口径）",
                "运费": FIXED_FREIGHT,
                "计费重量": None,
                "利润": fixed_bill["利润"],
                "利润率": fixed_bill["利润率"],
                "说明": f"固定运费 {FIXED_FREIGHT:.2f} 元，不区分轻重货",
            },
            "高估金额": diff,
            "结论": (f"通用口径高估利润 {diff:.2f} 元" if diff > 0
                     else ("两种口径结论一致" if abs(diff) < 0.01
                           else f"体育口径运费更省，利润比通用口径高 {abs(diff):.2f} 元")),
        },
        "退货期望损失": loss,
        "退货后利润": profit_after,
        "退货后利润率": margin_after,
        "重量": {"kg": weight, "dims": dims or "", "来源": "商品档案" if weight_kg is not None else "品类默认（估算）"},
        "入参": weight_inp.as_dict(),
    }
    if compare:
        payload["口径对照"]["是否影响结论"] = (
            "是：通用口径看起来达标，体育口径跌破红线"
            if fixed_bill["利润率"] >= 5 > bill["利润率"] else
            ("是：两者都跌破红线" if bill["利润率"] < 5 else "否：两者都达标")
        )
    return payload


def max_discount(inp: pricing.PricingInput, target_margin_pct: float,
                 policy_name: Optional[str] = None,
                 weight_kg: Optional[float] = None, dims: str = "") -> Dict[str, Any]:
    """保本反推：给定目标毛利率，反解「最多能打几折 / 最多能发多少券」。

    利润对折扣率单调递增，所以用二分求解（24 次迭代，精度 1e-6）——比闭式解更稳，
    因为计价内核的券后成交额、佣金基数都可能是分段口径。
    """
    chosen = inp
    if weight_kg is not None:
        fee = freight_fee(float(weight_kg), dims, policy_name)["fee"]
        chosen = replace(inp, freight_dom=fee)
    target = float(target_margin_pct)

    def margin_at(rate: float, coupon: float = None) -> float:
        probe = replace(chosen, tier_rate=rate)
        if coupon is not None:
            probe = replace(probe, coupon=coupon)
        try:
            bill = pricing.run(probe)
        except pricing.PricingError:
            return -999.0
        return bill["利润率"]

    # 1) 折扣底线：利润随折扣率单调上升 → 二分「最低可行折扣率」
    #    语义澄清：这里的"上限"指"最多能打多少折"，即折扣率的**下限**（原价的百分之几）。
    top = margin_at(1.0)
    if top < target:
        rate_floor: Optional[float] = None
        rate_note = (f"不打折也只有 {top:.1f}%，达不到 {target:.1f}% 红线——"
                     "这款要么涨标价/换货源，要么别做活动")
    else:
        lo, hi = 0.0, 1.0  # lo 不可行，hi 可行
        for _ in range(24):
            mid = (lo + hi) / 2
            if margin_at(mid) >= target:
                hi = mid
            else:
                lo = mid
        rate_floor = math.ceil(hi * 100) / 100
        if rate_floor >= 1.0:
            rate_note = f"不打折刚好达标（{top:.1f}%），一打折就跌破 {target:.1f}%"
        else:
            rate_note = (f"最低只能卖到原价的 {rate_floor:.0%}"
                         f"（{rate_floor * 10:.1f} 折），再低就跌破 {target:.1f}%")

    # 2) 券额上限：利润随券额单调下降 → 二分「最高可行券额」（折扣保持当前力度）
    listed = float(chosen.price or 0) * int(chosen.qty or 1) * float(chosen.tier_rate or 1)
    if listed <= 0:
        coupon_cap, coupon_note = 0.0, "标价缺失，无法反解券额"
    elif margin_at(float(chosen.tier_rate or 1), coupon=0.0) < target:
        coupon_cap, coupon_note = 0.0, "当前折扣已经让利润低于红线，任何券都不能发"
    elif margin_at(float(chosen.tier_rate or 1), coupon=listed) >= target:
        coupon_cap = listed
        coupon_note = f"发满 {listed:.2f} 元券也达标，券额不是瓶颈"
    else:
        lo, hi = 0.0, listed  # lo 可行，hi 不可行
        for _ in range(24):
            mid = (lo + hi) / 2
            if margin_at(float(chosen.tier_rate or 1), coupon=mid) >= target:
                lo = mid
            else:
                hi = mid
        coupon_cap = math.floor(lo * 100) / 100
        coupon_note = f"券额最多 {coupon_cap:.2f} 元，再多就跌破 {target:.1f}%"

    current = pricing.run(chosen)
    return {
        "目标毛利率": target,
        "当前利润率": current["利润率"],
        "当前利润": current["利润"],
        "折扣下限": rate_floor,
        "折扣下限_展示": (f"{rate_floor * 10:.1f} 折" if rate_floor is not None else "不可行"),
        "折扣说明": rate_note,
        "券额上限": coupon_cap,
        "券额说明": coupon_note,
        "口径": weight_inp_note(chosen),
        "验证": _verify(chosen, rate_floor, target),
    }


def weight_inp_note(inp: pricing.PricingInput) -> str:
    return f"国内运费 {inp.freight_dom:.2f} 元、佣金 {inp.commission:.0%}"


def _verify(inp: pricing.PricingInput, rate_floor: Optional[float], target: float) -> Dict[str, Any]:
    """用正向测算复算折扣底线：复算值应不低于目标红线（自洽即正确性证明）。"""
    if rate_floor is None:
        return {"说明": "无可行折扣，跳过复算"}
    bill = pricing.run(replace(inp, tier_rate=rate_floor))
    return {
        "按折扣下限测算": {"折扣率": rate_floor, "利润率": bill["利润率"], "利润": bill["利润"]},
        "结论": "复算利润率 ≥ 目标红线，口径自洽"
                if bill["利润率"] >= target - 0.01 else "复算低于目标红线，请检查口径",
    }


def event_audit(db: Session, qty: int = 2, tier_rate: float = 0.8, coupon: float = 0.0,
                floor_pct: float = 5.0, policy_name: Optional[str] = None) -> Dict[str, Any]:
    """赛事季活动体检：同一套活动参数扫全店，按亏损金额排序。"""
    rows: List[Dict[str, Any]] = []
    for product in db.query(Product).all():
        if product.price is None or product.cost_price is None:
            continue
        profile = sports.get_profile(db, product)
        inp = pricing.PricingInput(
            sku=product.name,
            price=float(product.price),
            qty=int(qty),
            unit_cost=float(product.cost_price),
            freight_intl=0.0,
            freight_dom=FIXED_FREIGHT,
            freight_cover=0.0,
            tier_rate=float(tier_rate),
            coupon=float(coupon),
            commission=0.06,
            commission_base="gmv_after_coupon",
            ship_fee=19.9,
        )
        result = quote(inp, weight_kg=profile["weight_kg"], dims=profile["package_dims"],
                       return_rate=profile["return_rate"], policy_name=policy_name)
        bill = result["账单"]
        rows.append({
            "product_id": product.id,
            "product_name": product.name,
            "sport": profile["sport_label"],
            "price": product.price,
            "利润": bill["利润"],
            "利润率": bill["利润率"],
            "退货后利润": result["退货后利润"],
            "运费": result["口径对照"]["体育口径"]["运费"],
            "高估": result["口径对照"]["高估金额"],
            "是否跌破红线": bill["利润率"] < floor_pct,
        })
    rows.sort(key=lambda item: item["退货后利润"])
    losing = [row for row in rows if row["退货后利润"] < 0]
    return {
        "活动参数": {"件数": qty, "折扣率": tier_rate, "券额": coupon, "毛利红线": floor_pct},
        "SKU 数": len(rows),
        "亏损 SKU 数": len(losing),
        "合计亏损": round(sum(row["退货后利润"] for row in losing), 2),
        "明细": rows,
        "头号风险": losing[0]["product_name"] if losing else "无",
    }
