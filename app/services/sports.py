"""体育领域知识层：品类树、赛事日历、合规红线、人群画像、商品运动档案。

设计边界（很重要）：
    * 本模块只做"读配置 + 结构化数据"，**不算钱**——钱一律由 pricing 引擎算；
    * 所有知识都来自 `config/sports_*.yaml`，改运营知识不改代码；
    * 赛事以"虚拟热点"的形式进入现有匹配管线（source="赛事日历"，
      source_url="event://<key>"），这样匹配、机会卡、内容生成全部复用，
      不需要另写一套流程。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..config import load_yaml
from ..models import ContentPlan, Hotspot, Product, SportProfile

EVENT_SOURCE = "赛事日历"
EVENT_PREFIX = "event://"
# 日历里除了比赛，还有大促和季节点——文案要用对词，不能一律叫「比赛」
KIND_LABELS = {"match": "比赛", "promo": "大促", "season": "节点"}


# ---------------------------------------------------------------- 配置加载
@lru_cache(maxsize=1)
def taxonomy() -> Dict[str, Any]:
    return load_yaml("sports_taxonomy.yaml") or {}


@lru_cache(maxsize=1)
def calendar() -> Dict[str, Any]:
    return load_yaml("sports_calendar.yaml") or {}


@lru_cache(maxsize=1)
def compliance() -> Dict[str, Any]:
    return load_yaml("sports_compliance.yaml") or {}


@lru_cache(maxsize=1)
def shipping() -> Dict[str, Any]:
    return load_yaml("sports_shipping.yaml") or {}


@lru_cache(maxsize=1)
def personas() -> Dict[str, Any]:
    return load_yaml("sports_personas.yaml") or {}


def reset_cache() -> None:
    """改完 YAML 之后调用，让"重载配置"接口立即生效。"""
    for fn in (taxonomy, calendar, compliance, shipping, personas):
        fn.cache_clear()


def sports() -> Dict[str, Dict[str, Any]]:
    return (taxonomy().get("sports") or {}) if taxonomy() else {}


def sport_meta(key: str) -> Dict[str, Any]:
    return sports().get(key or "") or {}


def sport_label(key: str) -> str:
    return sport_meta(key).get("name") or (key or "通用")


def defaults() -> Dict[str, Any]:
    return taxonomy().get("defaults") or {}


# ---------------------------------------------------------------- 商品归类
def _text_of(product: Product) -> str:
    parts = [product.name or "", product.category or "", product.brand or ""]
    parts += [str(v) for v in (product.specs or {}).values()]
    parts += [str(x) for x in (product.selling_points or [])]
    tags = getattr(product, "tags", None) or {}
    for value in tags.values():
        if isinstance(value, list):
            parts.extend(str(x) for x in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def guess_sport(product: Product) -> str:
    """猜商品属于哪个运动项目：先查品类映射，再用关键词打分兜底。"""
    category_map = taxonomy().get("category_map") or {}
    category = (product.category or "").strip()
    if category in category_map:
        return category_map[category]
    text = _text_of(product)
    best, best_hits = "", 0
    for key, meta in sports().items():
        hits = sum(1 for kw in (meta.get("keywords") or []) if kw and kw in text)
        if hits > best_hits:
            best, best_hits = key, hits
    return best


def default_attrs(sport: str, category: str = "") -> Dict[str, Any]:
    """品类默认属性：缺重量/退货率时用它，并在界面上标注"估算值"。"""
    meta = sport_meta(sport)
    base = defaults()
    return {
        "sport": sport,
        "weight_kg": meta.get("default_weight_kg") or base.get("weight_kg", 1.0),
        "return_rate": meta.get("default_return_rate") or base.get("return_rate", 0.15),
        "size_system": meta.get("size_system") or base.get("size_system", "均码"),
        "cert_required": bool(meta.get("cert_required")),
        "risk_note": meta.get("risk_note", ""),
    }


def _is_estimated(value: Optional[float], fallback: Any) -> bool:
    return value is None or abs(float(value) - float(fallback or 0)) < 1e-9


def get_profile(db: Optional[Session], product: Product) -> Dict[str, Any]:
    """取商品运动档案（不存在则用品类默认值补一份"估算档案"）。"""
    row = None
    if db is not None and getattr(product, "id", None):
        row = db.query(SportProfile).filter(SportProfile.product_id == product.id).first()
    sport = (row.sport if row is not None and row.sport else "") or guess_sport(product)
    fallback = default_attrs(sport, product.category or "")
    weight = row.weight_kg if row is not None and row.weight_kg is not None else fallback["weight_kg"]
    rate = row.return_rate if row is not None and row.return_rate is not None else fallback["return_rate"]
    return {
        "product_id": getattr(product, "id", None),
        "sport": sport,
        "sport_label": sport_label(sport),
        "sub_category": (row.sub_category if row is not None else "") or "",
        "level": (row.level if row is not None else "") or "beginner",
        "size_system": (row.size_system if row is not None else "") or fallback["size_system"],
        "size_range": (row.size_range if row is not None else "") or "",
        "weight_kg": round(float(weight), 3),
        "weight_estimated": row is None or row.weight_kg is None,
        "package_dims": (row.package_dims if row is not None else "") or "",
        "return_rate": round(float(rate), 3),
        "return_rate_estimated": row is None or row.return_rate is None,
        "cert_type": (row.cert_type if row is not None else "") or "",
        "cert_no": (row.cert_no if row is not None else "") or "",
        "cert_required": fallback["cert_required"],
        "risk_note": fallback["risk_note"],
        "notes": (row.notes if row is not None else "") or "",
    }


def save_profile(db: Session, product_id: int, payload: Dict[str, Any]) -> SportProfile:
    row = db.query(SportProfile).filter(SportProfile.product_id == product_id).first()
    if row is None:
        row = SportProfile(product_id=product_id)
        db.add(row)
    fields = ("sport", "sub_category", "level", "size_system", "size_range",
              "weight_kg", "package_dims", "return_rate", "cert_type", "cert_no", "notes")
    for key in fields:
        if key in payload:
            setattr(row, key, payload[key])
    db.commit()
    db.refresh(row)
    return row


def profile_as_dict(row: SportProfile) -> Dict[str, Any]:
    return {
        "product_id": row.product_id,
        "sport": row.sport,
        "sub_category": row.sub_category,
        "level": row.level,
        "size_system": row.size_system,
        "size_range": row.size_range,
        "weight_kg": row.weight_kg,
        "package_dims": row.package_dims,
        "return_rate": row.return_rate,
        "cert_type": row.cert_type,
        "cert_no": row.cert_no,
        "notes": row.notes,
    }


# ---------------------------------------------------------------- 赛事日历
def _parse_day(raw: str) -> Optional[date]:
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def list_events(days_ahead: int = 60, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """未来 days_ahead 天内的赛事，带倒计时、窗口状态与排期日期。"""
    today = today or date.today()
    out: List[Dict[str, Any]] = []
    for item in (calendar().get("events") or []):
        day = _parse_day(item.get("date"))
        if day is None:
            continue
        countdown = (day - today).days
        if countdown < -3 or countdown > days_ahead:
            continue
        prepare = int(item.get("prepare_days") or 21)
        peak = int(item.get("peak_days") or 7)
        if countdown <= peak and countdown >= -3:
            window = "peak"
        elif countdown <= prepare:
            window = "prepare"
        else:
            window = "later"
        plans = []
        for plan in item.get("content_plan") or []:
            offset = int(plan.get("offset_days") or 0)
            plans.append({
                "planned_date": (day + timedelta(days=offset)).isoformat(),
                "offset_days": offset,
                "form": plan.get("form", ""),
                "angle": plan.get("angle", ""),
                "days_left": offset + countdown,
            })
        out.append({
            "key": item.get("key"),
            "name": item.get("name"),
            "date": day.isoformat(),
            "sport": item.get("sport", ""),
            "sport_label": "全店" if item.get("sport") == "all" else sport_label(item.get("sport", "")),
            "keywords": item.get("keywords") or [],
            "countdown": countdown,
            "window": window,
            "kind": item.get("kind") or "match",
            "kind_label": KIND_LABELS.get(item.get("kind") or "match", "比赛"),
            "risk": item.get("risk", ""),
            "content_plan": plans,
        })
    out.sort(key=lambda e: e["countdown"])
    return out


def event_by_key(key: str) -> Optional[Dict[str, Any]]:
    for item in (calendar().get("events") or []):
        if item.get("key") == key:
            return item
    return None


def event_key_of(hotspot: Hotspot) -> str:
    """虚拟热点的 source_url 形如 event://bm2026；不是虚拟热点时返回空串。"""
    url = getattr(hotspot, "source_url", "") or ""
    if url.startswith(EVENT_PREFIX):
        return url[len(EVENT_PREFIX):]
    return ""


def event_fit_score(product: Product, event: Dict[str, Any],
                    profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """E 分：赛事契合度（0~100），四项可解释的加分。

    项目匹配 0~60（同项目 60 / 全店活动 35 / 品类在项目清单里 45 / 猜不中 0）
    窗口期   0~40（爆发期 40 / 备战期 25 / 已结束 0 / 还早 10）
    人群词   0~15（目标人群与赛事关键词的重合比例）
    """
    profile = profile or get_profile(None, product)
    sport = profile.get("sport") or guess_sport(product)
    event_sport = event.get("sport") or ""
    reasons: List[str] = []

    if event_sport == "all":
        project = 35
        reasons.append("全店活动，无项目门槛")
    elif event_sport and sport == event_sport:
        project = 60
        reasons.append(f"同属「{sport_label(sport)}」项目")
    else:
        categories = sport_meta(event_sport).get("categories") or []
        if (product.category or "") in categories:
            project = 45
            reasons.append(f"品类「{product.category}」属于{sport_label(event_sport)}常用装备")
        else:
            project = 0
            reasons.append("项目不相关")

    day = _parse_day(event.get("date"))
    countdown = (day - date.today()).days if day else 999
    prepare = int(event.get("prepare_days") or 21)
    peak = int(event.get("peak_days") or 7)
    kind_label = KIND_LABELS.get(event.get("kind") or "match", "比赛")
    event_name = event.get("name") or kind_label
    if -3 <= countdown <= peak:
        window, window_reason = 40, f"距「{event_name}」还有 {countdown} 天，正处内容爆发期"
    elif countdown <= prepare:
        window, window_reason = 25, f"距「{event_name}」还有 {countdown} 天，处于备战期，适合种草"
    elif countdown <= 90:
        window, window_reason = 10, f"距「{event_name}」还有 {countdown} 天，可提前布局"
    else:
        window, window_reason = 0, f"「{event_name}」还太远"

    audience_text = " ".join(str(v) for v in (product.target_audience or {}).values())
    keywords = [kw for kw in (event.get("keywords") or []) if kw]
    hits = sum(1 for kw in keywords if kw in _text_of(product) or kw in audience_text)
    audience = min(15, hits * 5)

    score = min(100, project + window + audience)
    return {
        "e": round(score, 1),
        "project": project,
        "window": window,
        "audience": audience,
        "reasons": reasons + [window_reason] + ([f"人群词命中 {hits} 个"] if hits else []),
    }


# ---------------------------------------------------------------- 虚拟热点
def sync_event_hotspots(db: Session, days_ahead: int = 60) -> Dict[str, int]:
    """把日历变成真实 Hotspot 行，让匹配管线"看见"未来的赛事。

    幂等：按 source_url 定位，重复调用只刷新标题/热度，不新增重复行。
    """
    created = updated = 0
    for event in list_events(days_ahead=days_ahead):
        url = EVENT_PREFIX + str(event["key"])
        row = db.query(Hotspot).filter(Hotspot.source_url == url).first()
        countdown = event["countdown"]
        # 越临近热度越高；已结束的慢慢降温
        hot = 92.0 if countdown <= 3 else max(55.0, 92.0 - abs(countdown) * 0.7)
        title = f"{event['name']}（{event['date']}，还有 {countdown} 天）" if countdown >= 0 \
            else f"{event['name']}（{event['date']}，刚结束）"
        trend = "rising" if countdown <= 14 else "stable"
        category = "运动健身" if event["sport"] in ("running", "fitness") else {
            "badminton": "运动健身", "football": "运动健身", "basketball": "运动健身",
            "cycling": "户外", "snow": "户外", "outdoor": "户外", "all": "大促",
        }.get(event["sport"], "运动健身")
        description = "；".join(plan["angle"] for plan in event["content_plan"])[:400] or event["name"]
        if row is None:
            row = Hotspot(
                title=title,
                description=description,
                source=EVENT_SOURCE,
                source_url=url,
                category=category,
                hot_score=round(hot, 1),
                trend=trend,
                keywords=list(event["keywords"]),
                heat_history=[{"ts": datetime.utcnow().isoformat(), "score": round(hot, 1)}],
                is_sensitive=False,
            )
            db.add(row)
            created += 1
        else:
            row.title = title
            row.description = description
            row.category = category
            row.hot_score = round(hot, 1)
            row.trend = trend
            row.keywords = list(event["keywords"])
            row.last_updated = datetime.utcnow()
            updated += 1
    db.commit()
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------- 赛事雷达
def radar(db: Optional[Session], days_ahead: int = 60, per_event: int = 3) -> List[Dict[str, Any]]:
    """赛事雷达：每个赛事 × 最相关的 N 款商品，带 E 分与建议排期。"""
    products = db.query(Product).all() if db is not None else []
    out: List[Dict[str, Any]] = []
    for event in list_events(days_ahead=days_ahead):
        scored = []
        for product in products:
            profile = get_profile(db, product)
            fit = event_fit_score(product, event, profile)
            if fit["e"] <= 0:
                continue
            scored.append({
                "product_id": product.id,
                "product_name": product.name,
                "category": product.category,
                "price": product.price,
                "cost_price": product.cost_price,
                "sport": profile["sport"],
                "sport_label": profile["sport_label"],
                "level": profile["level"],
                "e": fit["e"],
                "e_detail": {"project": fit["project"], "window": fit["window"],
                             "audience": fit["audience"]},
                "reasons": fit["reasons"],
                "weight_kg": profile["weight_kg"],
                "weight_estimated": profile["weight_estimated"],
            })
        scored.sort(key=lambda item: item["e"], reverse=True)
        event["products"] = scored[:per_event]
        event["product_count"] = len(scored)
        out.append(event)
    return out


def create_plans(db: Session, event_key: str, product_id: Optional[int] = None,
                 platform: str = "xiaohongshu") -> List[ContentPlan]:
    """为一个赛事生成"带日期的内容任务卡"，幂等（同赛事+同日期不重复）。"""
    event = None
    for item in list_events(days_ahead=400):
        if item["key"] == event_key:
            event = item
            break
    if event is None:
        raise ValueError(f"没有这个赛事：{event_key}")

    if product_id is None:
        ranked = radar(db, days_ahead=400, per_event=1)
        for item in ranked:
            if item["key"] == event_key and item["products"]:
                product_id = item["products"][0]["product_id"]
                break

    rows: List[ContentPlan] = []
    for plan in event["content_plan"]:
        exists = (db.query(ContentPlan)
                  .filter(ContentPlan.event_key == event_key,
                          ContentPlan.planned_date == plan["planned_date"],
                          ContentPlan.product_id == product_id)
                  .first())
        if exists is not None:
            rows.append(exists)
            continue
        row = ContentPlan(
            event_key=event_key,
            event_name=event["name"],
            product_id=product_id,
            planned_date=plan["planned_date"],
            offset_days=plan["offset_days"],
            form=plan["form"],
            angle=plan["angle"],
            platform=platform,
            status="planned",
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def plan_as_dict(row: ContentPlan) -> Dict[str, Any]:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "event_name": row.event_name,
        "product_id": row.product_id,
        "planned_date": row.planned_date,
        "offset_days": row.offset_days,
        "form": row.form,
        "angle": row.angle,
        "platform": row.platform,
        "status": row.status,
        "content_id": row.content_id,
    }


# ---------------------------------------------------------------- 合规检查
def check_compliance(text: str, product: Product, profile: Dict[str, Any]) -> Dict[str, Any]:
    """体育合规体检：逐条规则扫文本，返回命中项与改写建议（命中即"不通过"）。"""
    cfg = compliance()
    hits: List[Dict[str, Any]] = []
    lowered = text or ""
    for rule in cfg.get("rules") or []:
        # 条件规则：商品已登记认证编号时，"认证/防护等级"这类表述是合规的
        if rule.get("requires_cert_no") and profile.get("cert_no"):
            continue
        matched = [term for term in (rule.get("terms") or []) if term and term in lowered]
        if not matched:
            continue
        hits.append({
            "rule": rule.get("key"),
            "label": rule.get("label"),
            "matched": matched,
            "suggest": rule.get("suggest", ""),
            "disclaimers": rule.get("disclaimers") or [],
        })

    notes: List[str] = []
    cert_categories = cfg.get("cert_required_categories") or []
    needs_cert = bool(profile.get("cert_required")) or (product.category or "") in cert_categories
    if needs_cert and not profile.get("cert_no"):
        notes.append("该品类需要认证编号，但商品档案里没有填，内容中不得出现「认证/防护等级」表述")
    if needs_cert and profile.get("cert_no"):
        notes.append(f"认证编号 {profile['cert_no']} 已登记，可在文案中引用")
    if profile.get("risk_note"):
        notes.append(f"项目提醒：{profile['risk_note']}")

    disclaimer = cfg.get("general_disclaimer", "")
    return {
        "passed": not hits,
        "hits": hits,
        "notes": notes,
        "disclaimer": disclaimer,
        "summary": "通过" if not hits else f"命中 {len(hits)} 条合规规则，需改写",
    }
