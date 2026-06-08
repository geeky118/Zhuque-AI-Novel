from __future__ import annotations

from app.services.txt_parser_service import txt_parser_service


def test_clean_text_removes_inline_ad_fragment_and_preserves_sentence_tail() -> None:
    raw = "描金绣银。[更新快，站页面清爽，广告少，无弹窗，最喜欢这种站了，一定要好评j可就在大宅的角落里。"

    cleaned = txt_parser_service.clean_text(raw)

    assert cleaned == "描金绣银。可就在大宅的角落里。"


def test_clean_text_removes_common_source_ads_without_dropping_normal_plot() -> None:
    raw = "\n".join(
        [
            "叶铭挂出了广告牌子，内容是出售铭纹符。",
            "手机请访问：m.feizw.",
            "一秒记住【笔♂趣→阁.】，精彩无弹窗免费阅读！",
            "[]",
            "他继续坚持七七四十九天。[.520.超多好看j",
            "棉_._.花_._.糖提醒您：看本书最新章节请到520。",
        ]
    )

    cleaned = txt_parser_service.clean_text(raw)

    assert "叶铭挂出了广告牌子" in cleaned
    assert "m.feizw" not in cleaned
    assert "笔♂趣" not in cleaned
    assert "[]" not in cleaned
    assert "超多好看" not in cleaned
    assert "棉_._.花" not in cleaned


def test_split_chapters_prefers_line_headings_over_numeric_ad_fragments() -> None:
    raw = """
《示例小说》

第一章 冰水炼体

天元大陆，燕国。[520520.更新快，站页面清爽，广告少，无弹窗，最喜欢这种站了，一定要好评j
他站在冰水里练拳。

第二章 神灵宝衣

520.超多好看j
他跟着老乞丐离开。

第三章 终章

故事暂告一段落。
"""

    cleaned = txt_parser_service.clean_text(raw)
    chapters = txt_parser_service.split_chapters(cleaned)

    assert [chapter["title"] for chapter in chapters] == ["冰水炼体", "神灵宝衣", "终章"]
    assert "520" not in "\n".join(chapter["content"] for chapter in chapters)


def test_split_chapters_does_not_cut_body_line_that_starts_with_volume_text() -> None:
    raw = """
第一章 开端

第一卷读完之后，他才意识到书里的伏笔另有深意。

第二章 继续

故事继续推进。
"""

    chapters = txt_parser_service.split_chapters(txt_parser_service.clean_text(raw))

    assert [chapter["title"] for chapter in chapters] == ["开端", "继续"]
    assert "第一卷读完之后" in chapters[0]["content"]


def test_decode_bytes_supports_utf16le_bom() -> None:
    raw = "第一章 开端\n正文内容".encode("utf-16")

    text, encoding = txt_parser_service.decode_bytes(raw)

    assert encoding == "utf-16"
    assert "第一章 开端" in text
