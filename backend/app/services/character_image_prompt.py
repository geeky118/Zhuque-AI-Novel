"""角色形象提示词构造工具。"""
from __future__ import annotations

import json
import re
from typing import Optional


MALE_IMAGE_SUFFIX = (
    "国漫风，非写实，薄纱写真风格，成年人外观画面干净，背景简洁，"
    "全身图，不要半身图，保持同一小说世界观的统一视觉风格，"
    "适合后续漫画分镜延展，无文字，无水印，无logo。"
)
FEMALE_IMAGE_SUFFIX = (
    "国漫风，非写实，薄纱写真风格，成年人外观画面干净，背景简洁，"
    "全身图，不要半身图，保持同一小说世界观的统一视觉风格，"
    "适合后续漫画分镜延展，无文字，无水印，无logo。"
)
ORGANIZATION_IMAGE_SUFFIX = (
    "国漫风，非写实，组织设定图风格，画面干净，背景简洁，"
    "以组织核心视觉识别为主体，突出总部、据点、成员群像、标志性建筑或代表性场景，"
    "保持同一小说世界观的统一视觉风格，适合后续漫画分镜延展，"
    "无文字，无水印，无logo。"
)


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip(" ，。；;")


def parse_json_text(value: Optional[str]) -> str:
    cleaned = normalize_text(value)
    if not cleaned:
        return ""

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned

    if isinstance(parsed, list):
        return "、".join(normalize_text(str(item)) for item in parsed if normalize_text(str(item)))
    if isinstance(parsed, dict):
        return "；".join(
            f"{normalize_text(str(key))}：{normalize_text(str(item))}"
            for key, item in parsed.items()
            if normalize_text(str(key)) and normalize_text(str(item))
        )
    return cleaned


def sanitize_prompt_text(value: Optional[str]) -> str:
    sanitized = normalize_text(value)
    replacements = [
        (r"(未成年|少女|少年|萝莉|正太|小女孩|小男孩|学生妹|学生装)", "年轻成年人"),
        (r"(裸露|裸体|色情|情色|诱惑|爆乳|乳沟|内衣外露|走光|撩人)", "服装完整得体"),
        (r"(血腥|断肢|残肢|尸体|屠杀|虐杀|爆头)", "不呈现血腥元素"),
        (r"(枪械|步枪|手枪|匕首|刀刃|武器威胁|持枪|持刀)", "不突出攻击性道具"),
        (r"(毒品|违法交易|黑市|走私|犯罪现场)", "不涉及违法交易主题"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", sanitized).strip(" ，。；;")


def is_female_gender(value: Optional[str]) -> bool:
    gender_lower = normalize_text(value).lower()
    return any(kw in gender_lower for kw in ("女", "female", "woman", "girl", "她"))


def pick_image_suffix(gender: Optional[str]) -> str:
    return FEMALE_IMAGE_SUFFIX if is_female_gender(gender) else MALE_IMAGE_SUFFIX


def build_character_image_prompt(
    *,
    title: str | None,
    name: str | None,
    gender: str | None = None,
    age: str | None = None,
    appearance: str | None = None,
    is_organization: bool = False,
    organization_type: str | None = None,
    organization_purpose: str | None = None,
    comic_style_instruction: str | None = None,
) -> str:
    cleaned_title = normalize_text(title) or "当前小说"
    cleaned_name = normalize_text(name) or "未命名角色"

    if is_organization:
        cleaned_appearance = sanitize_prompt_text(appearance)
        parts = [
            f"【绘制任务】为小说《{cleaned_title}》中的组织「{cleaned_name}」绘制一张组织视觉设定图，重点表达组织气质、据点氛围和视觉识别，不要画成单人写真。",
            f"【统一漫画风格】{normalize_text(comic_style_instruction)}" if normalize_text(comic_style_instruction) else "",
            f"组织类型：{sanitize_prompt_text(organization_type)}。" if normalize_text(organization_type) else "",
            f"组织目标：{sanitize_prompt_text(organization_purpose)}。" if normalize_text(organization_purpose) else "",
            f"外在表现：{cleaned_appearance}。" if cleaned_appearance else "",
            f"【固定视觉要求】{ORGANIZATION_IMAGE_SUFFIX}",
        ]
        return " ".join(part for part in parts if part).strip()

    cleaned_gender = sanitize_prompt_text(gender)
    cleaned_age = sanitize_prompt_text(age)
    cleaned_appearance = sanitize_prompt_text(appearance)

    parts = [
        f"为小说《{cleaned_title}》中的角色「{cleaned_name}」绘制一张角色形象设定图。",
        f"统一漫画风格：{normalize_text(comic_style_instruction)}。" if normalize_text(comic_style_instruction) else "",
        f"性别气质：{cleaned_gender}。" if cleaned_gender else "",
        f"年龄参考：{cleaned_age}，但视觉表现保持成年人设定。" if cleaned_age else "",
        f"外貌特征：{cleaned_appearance}。" if cleaned_appearance else "",
        pick_image_suffix(gender),
    ]
    return " ".join(part for part in parts if part).strip()
