"""体育特化的接口：赛事雷达、运动档案、重量运费、双口径账单、保本反推、赛事内容。

设计原则（和全项目一致）：
    * 所有金额来自 `services.pricing` / `services.sports_pricing` 的确定性计算；
    * 本层只做参数组装与持久化，不做业务判断；
    * 未配 Key 时全部接口照常可用（模板内容 + Mock），保证离线答辩可演示。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Content, Product
from ..schemas import ContentGenerate
from ..services import pricing
from ..services import sports, sports_content, sports_pricing

router = APIRouter(prefix="/sports", tags=["sports"])

FIXED_FREIGHT = sports_pricing.FIXED_FREIGHT


# --------------------------------------------------------------------- 入参模型
class ProfileBody(BaseModel):
    sport: Optional[str] = None
    sub_category: Optional[str] = None
    level: Optional[str] = None
    size_system: Optional[str] = None
    size_range: Optional[str] = None
    weight_kg: Optional[float] = Field(default=None, ge=0, le=100)
    package_dims: Optional[str] = None
    return_rate: Optional[float] = Field(default=None, ge=0, le=1)
    cert_type: Optional[str] = None
    cert_no: Optional[str] = None
    notes: Optional[str] = None


class QuoteBody(BaseModel):
    """既支持"选商品"（只传 product_id + 活动参数），也支持"手动试算"。"""

    product_id: Optional[int] = None
    sku: str = ""
    price: Optional[float] = Field(default=None, ge=0)
    qty: int = Field(default=2, ge=1, le=99)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    weight_kg: Optional[float] = Field(default=None, ge=0, le=100)
    dims: str = ""
    return_rate: Optional[float] = Field(default=None, ge=0, le=1)
    tier_rate: float = Field(default=0.8, gt=0, le=1)
    coupon: float = Field(default=0.0, ge=0)
    commission: float = Field(default=0.06, ge=0, lt=1)
    ship_fee: float = Field(default=19.9, ge=0)
    policy: Optional[str] = None


class MaxDiscountBody(QuoteBody):
    target_margin: float = Field(default=5.0, gt=-100, lt=100)


class EventAuditBody(BaseModel):
    qty: int = Field(default=2, ge=1, le=99)
    tier_rate: float = Field(default=0.8, gt=0, le=1)
    coupon: float = Field(default=0.0, ge=0)
    floor_pct: float = Field(default=5.0, gt=-100, lt=100)
    policy: Optional[str] = None


class ContentBody(BaseModel):
    product_id: int
    event_key: Optional[str] = None
    form: str = "checklist"
    platform: str = "xiaohongshu"
    save: bool = True


class ComplianceBody(BaseModel):
    text: str
    product_id: Optional[int] = None


# --------------------------------------------------------------------- 工具
def _product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product


def _build_input(db: Session, body: QuoteBody) -> tuple[pricing.PricingInput, Dict[str, Any]]:
    """把「商品档案 + 请求覆盖值」组装成计价引擎入参。"""
    product = _product(db, body.product_id) if body.product_id else None
    profile: Dict[str, Any] = {}
    if product is not None:
        profile = sports.get_profile(db, product)

    price = body.price if body.price is not None else getattr(product, "price", None)
    cost = body.unit_cost if body.unit_cost is not None else getattr(product, "cost_price", None)
    if price is None:
        raise HTTPException(status_code=400, detail="缺少标价：请传 price 或选择带价格的商品")
    if cost is None:
        raise HTTPException(status_code=400, detail="缺少进货成本：请传 unit_cost（或用「商品成本」接口补全）")

    weight = body.weight_kg
    if weight is None and profile:
        weight = profile.get("weight_kg")
    dims = body.dims or (profile.get("package_dims") or "")

    inp = pricing.PricingInput(
        sku=body.sku or (getattr(product, "name", "") if product else ""),
        price=float(price),
        qty=int(body.qty),
        unit_cost=float(cost),
        freight_intl=0.0,
        freight_dom=FIXED_FREIGHT,  # 先放固定值，sports_pricing.quote 会按重量覆盖
        freight_cover=0.0,
        tier_rate=float(body.tier_rate),
        coupon=float(body.coupon),
        commission=float(body.commission),
        commission_base="gmv_after_coupon",
        ship_fee=float(body.ship_fee),
    )
    meta = {
        "product": product,
        "profile": profile,
        "weight_kg": weight if weight is not None else None,
        "dims": dims,
        "return_rate": body.return_rate if body.return_rate is not None else (
            profile.get("return_rate") if profile else None),
        "policy": body.policy,
    }
    return inp, meta


# --------------------------------------------------------------------- 知识层
@router.get("/taxonomy")
def get_taxonomy() -> Dict[str, Any]:
    """运动项目树 + 内容形式 + 人群画像（前端下拉/标签全部由这里驱动）。"""
    tax = sports.taxonomy()
    out_sports = []
    for key, meta in (tax.get("sports") or {}).items():
        out_sports.append({
            "key": key,
            "name": meta.get("name") or key,
            "keywords": meta.get("keywords") or [],
            "categories": meta.get("categories") or [],
            "default_weight_kg": meta.get("default_weight_kg"),
            "default_return_rate": meta.get("default_return_rate"),
            "size_system": meta.get("size_system"),
            "cert_required": bool(meta.get("cert_required")),
            "risk_note": meta.get("risk_note", ""),
        })
    personas = sports.personas()
    return {
        "sports": out_sports,
        "content_forms": [{"key": k, **v} for k, v in sports_content.FORMS.items()],
        "personas": personas.get("personas") or {},
        "size_systems": tax.get("size_systems") or {},
        "defaults": sports.defaults(),
    }


# --------------------------------------------------------------------- 赛事日历
@router.get("/calendar")
def get_calendar(days: int = 60) -> Dict[str, Any]:
    events = sports.list_events(days_ahead=max(1, min(days, 400)))
    return {"today": date.today().isoformat(), "count": len(events), "events": events}


@router.post("/radar/sync")
def sync_radar(days: int = 60, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """把赛事日历写成虚拟热点（source=赛事日历），让现有匹配管线直接看见。"""
    stats = sports.sync_event_hotspots(db, days_ahead=max(1, min(days, 400)))
    return {"ok": True, "days": days, **stats,
            "hint": "热点已就绪，去「热点看板 → AI 匹配」即可看到赛事机会卡"}


@router.get("/radar")
def get_radar(days: int = 60, per_event: int = 3, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """赛事雷达：每个赛事 × 最相关的 N 款商品（含 E 分拆解与建议排期）。"""
    events = sports.radar(db, days_ahead=max(1, min(days, 400)), per_event=max(1, min(per_event, 10)))
    peak = [e for e in events if e["window"] == "peak"]
    prepare = [e for e in events if e["window"] == "prepare"]
    return {"count": len(events), "peak": len(peak), "prepare": len(prepare), "events": events}


@router.get("/calendar/{event_key}/products")
def event_products(event_key: str, per_event: int = 5, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """单个赛事 ↔ 商品：E 分排序，回答"这个赛事该推哪几款"。"""
    for item in sports.radar(db, days_ahead=400, per_event=max(1, min(per_event, 20))):
        if item["key"] == event_key:
            return item
    raise HTTPException(status_code=404, detail="赛事不存在")


# --------------------------------------------------------------------- 运动档案
@router.get("/profile/{product_id}")
def get_profile(product_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    product = _product(db, product_id)
    profile = sports.get_profile(db, product)
    return {
        "product_id": product_id,
        "name": product.name,
        "profile": profile,
        "suggested_sport": sports.guess_sport(product),
        "cost_hint": {
            "unit_cost": product.cost_price,
            "price": product.price,
        },
        "estimated_fields": [k for k in ("weight_kg", "return_rate") if profile.get(f"{k}_estimated")],
    }


@router.put("/profile/{product_id}")
def put_profile(product_id: int, body: ProfileBody,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    _product(db, product_id)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(status_code=400, detail="没有要保存的字段")
    row = sports.save_profile(db, product_id, payload)
    return {"ok": True, "profile": sports.profile_as_dict(row)}


@router.post("/profile/{product_id}/guess")
def guess_profile(product_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """按名称/品类/标签猜运动项目，返回建议值（不落库，由用户确认后再 PUT）。"""
    product = _product(db, product_id)
    sport = sports.guess_sport(product)
    attrs = sports.default_attrs(sport, product.category or "")
    return {"product_id": product_id, "suggested": {**attrs, "sport_label": sports.sport_label(sport)},
            "note": "这是猜的，确认后再保存；不确定就别填，系统会标「估算值」"}


# --------------------------------------------------------------------- 运费与账单
@router.get("/freight")
def get_freight(weight_kg: float = 1.0, dims: str = "", policy: Optional[str] = None) -> Dict[str, Any]:
    """重量运费试算：看"同样一件货，按重量算要多少钱"。"""
    return sports_pricing.freight_fee(weight_kg, dims, policy)


@router.get("/shipping-policy")
def get_shipping_policy() -> Dict[str, Any]:
    cfg = sports.shipping()
    return {"default_policy": cfg.get("default_policy"),
            "policies": cfg.get("policies") or {},
            "return": cfg.get("return") or {}}


@router.post("/quote")
def post_quote(body: QuoteBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """体育口径账单：重量运费 + 通用/体育双口径对照 + 退货期望损失。"""
    inp, meta = _build_input(db, body)
    try:
        result = sports_pricing.quote(
            inp,
            weight_kg=meta["weight_kg"] if meta["weight_kg"] is not None else 1.0,
            dims=meta["dims"],
            return_rate=meta["return_rate"],
            policy_name=meta["policy"],
        )
    except pricing.PricingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    product = meta["product"]
    result["product"] = {"id": getattr(product, "id", None),
                         "name": getattr(product, "name", body.sku),
                         "price": getattr(product, "price", body.price)}
    result["profile"] = {k: meta["profile"].get(k) for k in
                         ("sport", "sport_label", "weight_kg", "weight_estimated",
                          "return_rate", "return_rate_estimated", "size_range")} if meta["profile"] else {}
    return result


@router.post("/max-discount")
def post_max_discount(body: MaxDiscountBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """保本反推：给目标毛利率，反解"最多打几折 / 最多发多少券"，并用正向测算复算。"""
    inp, meta = _build_input(db, body)
    try:
        return sports_pricing.max_discount(
            inp, body.target_margin,
            policy_name=meta["policy"],
            weight_kg=meta["weight_kg"],
            dims=meta["dims"],
        )
    except pricing.PricingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-audit")
def post_event_audit(body: EventAuditBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """赛事季活动体检：同一套活动参数扫全店，按退货后期望利润排序，先看亏得多的。"""
    return sports_pricing.event_audit(
        db, qty=body.qty, tier_rate=body.tier_rate, coupon=body.coupon,
        floor_pct=body.floor_pct, policy_name=body.policy,
    )


# --------------------------------------------------------------------- 内容与排期
@router.post("/content")
def post_content(body: ContentBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """按「赛事 × 商品 × 形式」出稿，同时返回事实校验与体育合规两道闸的结果。"""
    product = _product(db, body.product_id)
    event = None
    if body.event_key:
        event = sports.event_by_key(body.event_key)
        if event is None:
            raise HTTPException(status_code=404, detail="赛事不存在")
        event = next((e for e in sports.list_events(days_ahead=400) if e["key"] == body.event_key), None)

    try:
        draft = sports_content.generate(product, event=event, form_key=body.form,
                                        platform=body.platform, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content_id = None
    if body.save:
        row = Content(
            product_id=product.id,
            platform=draft["platform"],
            content_type=body.form,
            title=draft["title"],
            body=draft["body"],
            tags=draft["tags"],
            cta=draft["cta"],
            audit_result={"fact_check": draft["fact_check"], "compliance": draft["compliance"]},
            status="draft" if draft["can_publish"] else "pending",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        content_id = row.id
    draft["content_id"] = content_id
    return draft


@router.post("/content/preview")
def preview_content(body: ContentBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """只出稿不落库（用户先看再决定存不存）。"""
    body.save = False
    return post_content(body, db)


@router.post("/plans")
def create_plans(event_key: str, product_id: Optional[int] = None,
                 platform: str = "xiaohongshu", db: Session = Depends(get_db)) -> Dict[str, Any]:
    """给一个赛事排内容任务（带日期，倒排：赛前 7 天发什么、赛后发什么）。"""
    try:
        rows = sports.create_plans(db, event_key, product_id=product_id, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "count": len(rows), "plans": [sports.plan_as_dict(r) for r in rows]}


@router.get("/plans")
def list_plans(event_key: Optional[str] = None, status: Optional[str] = None,
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    from ..models import ContentPlan

    query = db.query(ContentPlan)
    if event_key:
        query = query.filter(ContentPlan.event_key == event_key)
    if status:
        query = query.filter(ContentPlan.status == status)
    rows = query.order_by(ContentPlan.planned_date.asc()).all()
    return {"count": len(rows), "plans": [sports.plan_as_dict(r) for r in rows]}


@router.post("/plans/{plan_id}/generate")
def generate_plan(plan_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """把一张任务卡真正变成一条内容草稿（回填 content_id，状态改 generated）。"""
    from ..models import ContentPlan

    plan = db.get(ContentPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="任务卡不存在")
    if plan.product_id is None:
        raise HTTPException(status_code=400, detail="这张任务卡没绑定商品")
    product = _product(db, plan.product_id)
    event = next((e for e in sports.list_events(days_ahead=400) if e["key"] == plan.event_key), None)
    form_key = {"装备清单": "checklist", "训练打卡": "checkin",
                "装备测评": "review", "赛事应援": "cheer"}.get(plan.form or "", "checklist")

    draft = sports_content.generate(product, event=event, form_key=form_key,
                                    platform=plan.platform or "xiaohongshu", db=db)
    row = Content(
        product_id=product.id,
        platform=draft["platform"],
        content_type=form_key,
        title=draft["title"],
        body=draft["body"],
        tags=draft["tags"],
        cta=draft["cta"],
        audit_result={"fact_check": draft["fact_check"], "compliance": draft["compliance"]},
        status="draft" if draft["can_publish"] else "pending",
    )
    db.add(row)
    db.flush()
    plan.content_id = row.id
    plan.status = "generated"
    db.commit()
    db.refresh(row)
    draft["content_id"] = row.id
    draft["plan_id"] = plan_id
    return draft


# --------------------------------------------------------------------- 合规
@router.post("/compliance/check")
def compliance_check(body: ComplianceBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """体育合规体检：把一段文案粘进来，逐条命中规则（认证/防护等级/疗效/极限化等）。"""
    if body.product_id:
        product = _product(db, body.product_id)
        profile = sports.get_profile(db, product)
    else:
        product = Product(name="", category="", tags={}, selling_points=[])
        profile = {"cert_required": False, "risk_note": "", "cert_no": ""}
    return sports.check_compliance(body.text, product, profile)


@router.post("/compliance/check-content/{content_id}")
def compliance_check_saved(content_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.get(Content, content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    product = _product(db, row.product_id) if row.product_id else Product(name="", category="", tags={})
    profile = sports.get_profile(db, product)
    text = "\n".join([row.title or "", row.body or "", row.cta or ""] + list(row.tags or []))
    result = sports.check_compliance(text, product, profile)
    row.audit_result = {**(row.audit_result or {}), "compliance": result}
    db.commit()
    return result
