"""项目漫画风格配置。"""
from __future__ import annotations

from typing import Any


DEFAULT_COMIC_STYLE = "guoman_refined"

COMIC_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "guoman_refined": {
        "label": "精修国漫",
        "prompt": (
            "精修国漫画风，线稿干净，动漫渲染精致，电影感光影，服饰细节优雅，"
            "色彩体系统一，呈现高端连载漫画质感"
        ),
    },
    "guoman_ink": {
        "label": "水墨国风",
        "prompt": (
            "水墨国漫画风，笔触纹理克制，色彩含蓄，仙侠氛围浓厚，"
            "人物剪影优雅，同时保持现代漫画的清晰可读性"
        ),
    },
    "japanese_cel": {
        "label": "日漫赛璐璐",
        "prompt": (
            "日漫赛璐璐漫画风，轮廓线清晰，阴影平涂干净，表情表现力强，"
            "色彩明亮且统一，具备成熟漫画分格感"
        ),
    },
    "korean_webtoon": {
        "label": "韩漫条漫",
        "prompt": (
            "韩漫条漫画风，数码绘制顺滑，角色设计时尚，渐变柔和，"
            "背景干净，符合竖向阅读漫画审美"
        ),
    },
    "dark_fantasy": {
        "label": "暗黑奇幻",
        "prompt": (
            "暗黑奇幻漫画风，明暗对比强烈，服饰华丽，阴影深沉，"
            "色彩氛围压抑，具备厚涂质感但保持漫画可读性"
        ),
    },
    "american_comic": {
        "label": "美漫厚涂",
        "prompt": (
            "美式图像小说画风，墨线有力量，构图动态强，人体结构扎实，"
            "色彩戏剧化，呈现厚涂漫画完成度"
        ),
    },
    "photoreal_cinematic": {
        "label": "真人写实",
        "prompt": (
            "真人写实电影风格，真实人类面部比例，自然皮肤质感，真实服装材质，"
            "现实场景，电影级光影，浅景深，高级电影剧照构图，"
            "不要动漫感、卡通感、插画感或漫画线稿"
        ),
    },
}


def normalize_comic_style(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned if cleaned in COMIC_STYLE_PRESETS else DEFAULT_COMIC_STYLE


def get_comic_style_label(value: str | None) -> str:
    key = normalize_comic_style(value)
    return COMIC_STYLE_PRESETS[key]["label"]


def get_comic_style_prompt(value: str | None) -> str:
    key = normalize_comic_style(value)
    return COMIC_STYLE_PRESETS[key]["prompt"]


def build_comic_style_instruction(style: str | None, custom_prompt: str | None = None) -> str:
    custom = " ".join(str(custom_prompt or "").split()).strip()
    prompt = get_comic_style_prompt(style)
    if custom:
        prompt = f"{prompt}。额外固定风格约束：{custom}"
    label = get_comic_style_label(style)
    return (
        f"统一项目漫画风格：{label}。{prompt}。"
        "每一张角色设定图、组织视觉图、角色视觉圣经和漫画页面都必须沿用这套视觉风格。"
        "在整个项目中保持线条质量、渲染方式、光照、色彩、脸部比例、服装细节和镜头语言一致。"
    )


def comic_style_options_payload() -> list[dict[str, Any]]:
    return [
        {"value": key, "label": value["label"], "prompt": value["prompt"]}
        for key, value in COMIC_STYLE_PRESETS.items()
    ]
