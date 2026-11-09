"""体育内容生成：装备清单 / 训练打卡 / 装备测评 / 赛事应援。

和通用 `content.py` 的分工：
    * 通用模块负责「平台硬限制 + 事实校验 + 通用模板」；
    * 本模块只多一件事——**把赛事日历和运动属性接进文案结构**（日历给的"角度"、档案给的尺码/重量）。
    * 文案里的每个数字仍然只能来自商品资料（由 `content.fact_check` 复核），
      AI 只负责组织语言，不产生数字。

生成策略：
    * 未接大模型 → 用模板（可离线演示、零成本、结果可复现）；
    * 接了大模型 → 把模板当成骨架，交给模型润色（`_llm_polish`），失败就回退模板。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..schemas import ContentGenerate
from . import content, sports

logger = logging.getLogger(__name__)

# 赛事内容四形式：key → (展示名, 结构提示)
FORMS: Dict[str, Dict[str, str]] = {
    "checklist": {"name": "装备清单", "angle": "按预算/水平给清单，帮用户做取舍"},
    "checkin": {"name": "训练打卡", "angle": "真实训练记录，带装备使用细节"},
    "review": {"name": "装备测评", "angle": "一个具体痛点 + 实测结论"},
    "cheer": {"name": "赛事应援", "angle": "借赛事情绪，落到「你也能上场」"},
}


def form_meta(key: str) -> Dict[str, str]:
    return FORMS.get(key or "") or {"name": key or "装备清单", "angle": "按赛事节奏出内容"}


def _as_list(value, limit: Optional[int] = None) -> List[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        items = [f"{k}：{v}" for k, v in value.items() if v]
    elif isinstance(value, (list, tuple)):
        items = [str(x) for x in value if str(x).strip()]
    else:
        items = [str(value)] if str(value).strip() else []
    return items[:limit] if limit else items


def _clip(text: str, limit: Optional[int]) -> str:
    text = (text or "").strip()
    if not limit or len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _title(product, form: Dict[str, str], event: Optional[Dict[str, Any]]) -> str:
    name = (product.name or "").strip()
    if event:
        short = (event.get("name") or "").split("（")[0].strip()
        return f"{short}｜{name}{form['name'][:2]}"
    return f"{name}｜{form['name']}"


def _body(product, profile: Dict[str, Any], form_key: str,
          event: Optional[Dict[str, Any]]) -> str:
    points = _as_list(product.selling_points, 3)
    size = profile.get("size_range") or profile.get("size_system") or ""
    price = product.price
    lines: List[str] = []

    if event:
        # 只写赛事名，不写日期：日期数字会被事实校验判成"资料里没有的数字"
        lines.append(f"赛程临近：{event.get('name', '')}。")
        plan_angle = form_meta(form_key)["angle"]
        lines.append(f"这条内容的角度：{plan_angle}。")
    else:
        lines.append(f"这条内容的角度：{form_meta(form_key)['angle']}。")

    if form_key == "checklist":
        lines.append("一份能直接照着买的清单：")
        for point in points:
            lines.append(f"· {point}")
        if size:
            lines.append(f"尺码/规格：{size}（下单前按这个对一遍）")
        if price:
            lines.append(f"标价 {price:g} 元，按你的预算决定要不要一步到位。")
    elif form_key == "checkin":
        lines.append("今天的训练记录：")
        for point in points:
            lines.append(f"- {point}")
        lines.append("训练完的感受写在评论区，方便后面的人参考。")
        if size:
            lines.append(f"我这次用的是：{size}")
    elif form_key == "review":
        lines.append("先说结论，再说过程：")
        for point in points:
            lines.append(f"- {point}")
        lines.append("不足也写一句，别只夸——用户信的是边界，不是形容词。")
        if price:
            lines.append(f"价格 {price:g} 元，值不值你自己判断。")
    else:  # cheer
        lines.append("赛场上那股劲儿，其实你也能有：")
        for point in points:
            lines.append(f"- {point}")
        lines.append("不一定要上场，但可以先从一件顺手的装备开始。")

    return "\n".join(lines)


def _tags(product, profile: Dict[str, Any], form_key: str) -> List[str]:
    tags: List[str] = []
    tags.append(str(profile.get("sport_label") or "").strip())
    tags.append(str(product.category or "").strip())
    tags.extend(_as_list((product.tags or {}).get("功能"), 2))
    if form_key in ("checkin", "cheer"):
        tags.append("运动日常")
    else:
        tags.append(form_meta(form_key)["name"])
    seen, out = set(), []
    for tag in tags:
        tag = tag.strip().lstrip("#")
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _cta(form_key: str, platform: str) -> str:
    if form_key == "checklist":
        return "想要清单原图的评论区扣「清单」"
    if form_key == "checkin":
        return "你今天练了什么？评论区打卡"
    if form_key == "review":
        return "想看哪一项的细节，评论区说，我补测"
    return "这个赛季，你准备从哪件开始？"


def _polish_with_llm(draft: ContentGenerate, product, form_key: str,
                     event: Optional[Dict[str, Any]]) -> ContentGenerate:
    """有 Key 时让模型润色；任何异常都回退模板（生成不能因为 AI 挂了就不可用）。"""
    from ..llm import llm

    if llm.is_mock:
        return draft
    prompt = (
        "把下面这份运动装备商家的小红书草稿润色得更自然、更像真人分享，"
        "不要新增任何数字、价格、认证或功效表述，不要超过原文信息量。\n"
        f"商品：{product.name}\n标题：{draft.title}\n正文：\n{draft.body}\n"
        f"标签：{'、'.join(draft.tags)}\n结尾引导语：{draft.cta}"
    )
    try:
        polished = llm.complete_json(
            "你是运动装备商家的小红书文案编辑，只做语言润色，不新增事实与数字。",
            prompt,
            ContentGenerate,
            temperature=0.6,
        )
    except Exception as exc:  # noqa: BLE001 - AI 失败不该阻断出稿
        logger.warning("赛事内容润色失败，回退模板：%s", exc)
        return draft
    return ContentGenerate(
        title=(polished.title or draft.title).strip(),
        body=(polished.body or draft.body).strip(),
        tags=[str(x).strip().lstrip("#") for x in (polished.tags or draft.tags) if str(x).strip()] or draft.tags,
        cta=(polished.cta or draft.cta).strip(),
        script=draft.script,
    )


def generate(product, profile: Optional[Dict[str, Any]] = None,
             event: Optional[Dict[str, Any]] = None, form_key: str = "checklist",
             platform: str = "xiaohongshu", db=None) -> Dict[str, Any]:
    """生成一条体育内容，并同时跑「事实校验 + 体育合规」两道闸。"""
    profile = profile or sports.get_profile(db, product)
    pcfg = content.get_platform_config(platform)
    form = form_meta(form_key)
    brief = content.build_product_brief(product)

    draft = ContentGenerate(
        title=_title(product, form, event),
        body=_body(product, profile, form_key, event),
        tags=_tags(product, profile, form_key),
        cta=_cta(form_key, platform),
        script=[],
    )
    draft = _polish_with_llm(draft, product, form_key, event)

    title_limit = pcfg.get("title_max_length") or pcfg.get("title_max")
    tag_limit = pcfg.get("tags_max") or pcfg.get("tag_limit")
    draft.title = _clip(draft.title, title_limit)
    draft.tags = draft.tags[:tag_limit] if tag_limit else draft.tags

    text = "\n".join([draft.title, draft.body, draft.cta] + draft.tags)
    fact = content.fact_check(draft, product, brief)
    compliance = sports.check_compliance(text, product, profile)

    return {
        "platform": platform,
        "platform_name": pcfg.get("name") or platform,
        "form": form_key,
        "form_name": form["name"],
        "event": (event or {}).get("name") if event else "",
        "event_date": (event or {}).get("date") if event else "",
        "title": draft.title,
        "body": draft.body,
        "tags": draft.tags,
        "cta": draft.cta,
        "limits": {"title_max": title_limit, "tags_max": tag_limit,
                   "title_len": len(draft.title), "tags_count": len(draft.tags)},
        "fact_check": fact,
        "compliance": compliance,
        "can_publish": bool(fact["passed"] and compliance["passed"]),
        "warnings": [i["msg"] for i in fact["issues"]] + [h["label"] for h in compliance["hits"]],
    }


def generate_for_event(event: Dict[str, Any], product, db=None,
                       platform: str = "xiaohongshu") -> List[Dict[str, Any]]:
    """一个赛事把「四形式」全出齐——对应赛事雷达里的一张赛事卡。"""
    profile = sports.get_profile(db, product)
    out: List[Dict[str, Any]] = []
    for key in ("checklist", "checkin", "review", "cheer"):
        out.append(generate(product, profile, event, key, platform, db=db))
    return out
