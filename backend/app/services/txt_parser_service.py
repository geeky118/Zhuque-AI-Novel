"""TXT 解析服务：编码识别、文本清洗与章节切分"""
from __future__ import annotations

import re
from typing import Optional

from app.logger import get_logger

logger = get_logger(__name__)


class TxtParserService:
    """TXT 解析服务（规则优先）"""

    STRONG_CHAPTER_PATTERNS = [
        re.compile(r"^正文\s+第[一二三四五六七八九十百千万零〇两\d]+[章节回节].*$"),
        re.compile(r"^第\s*[一二三四五六七八九十百千万零〇两\d]+\s*[章节回节].*$"),
        re.compile(r"^chapter\s*\d+.*$", re.IGNORECASE),
        re.compile(r"^chap\.\s*\d+.*$", re.IGNORECASE),
    ]

    CHAPTER_MARKER_PATTERN = re.compile(
        r"(?m)^[ \t　]*(?:正文\s*)?(?:第\s*([一二三四五六七八九十百千万零〇两\d]{1,16})\s*[章节回节]|(\d{1,5})\s*章|(\d{1,5})[\.、])\s*[-—:：、.．）)】\]]*\s*"
    )
    MARKER_NOISE_TOKENS = (
        "更新到",
        "内容简介",
        "內容簡介",
        "简介",
        "簡介",
        "作者",
        "愛下電子書Txt版閱讀",
        "更多電子書",
        "support@",
        "ixdzs",
        "E-mail:",
    )

    def decode_bytes(self, content: bytes) -> tuple[str, str]:
        """
        尝试解码 TXT 字节流

        Returns:
            (text, encoding)
        """
        encodings = ["utf-8", "utf-8-sig", "gb18030", "gbk", "big5"]
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            encodings = ["utf-16", *encodings]
        elif len(content) >= 4:
            sample = content[:4096]
            odd_nulls = sample[1::2].count(0)
            even_nulls = sample[0::2].count(0)
            if odd_nulls > len(sample) // 6:
                encodings = ["utf-16-le", *encodings]
            elif even_nulls > len(sample) // 6:
                encodings = ["utf-16-be", *encodings]
        for enc in encodings:
            try:
                return content.decode(enc), enc
            except UnicodeDecodeError:
                continue

        # 最后兜底：不抛错，尽量读出内容
        logger.warning("TXT 编码自动识别失败，使用 utf-8(ignore) 兜底")
        return content.decode("utf-8", errors="ignore"), "utf-8(ignore)"

    def clean_text(self, text: str) -> str:
        """基础清洗：换行归一、去除异常空白、压缩多余空行，并剔除常见平台导出噪音。"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
        normalized = normalized.replace("\u3000", "  ")
        normalized = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", normalized)
        lines: list[str] = []
        noise_patterns = [
            re.compile(r"^\s*本书(?:来自|下载自|由).{0,40}$"),
            re.compile(r"^\s*(?:更多|最新|全文|无错|最快)章节.{0,60}(?:请|访问|搜索|百度|关注).{0,100}$"),
            re.compile(r"^\s*(?:请收藏|求收藏|求推荐|求月票|加入书架|点击推荐|投推荐票).{0,100}$"),
            re.compile(r"^\s*(?:手机用户|电脑用户|书友群|读者群|QQ群|微信).{0,120}$", re.IGNORECASE),
            re.compile(r"^\s*(?:www\.|https?://|下载地址[:：]|最新地址[:：]|备用网址[:：]).{0,180}$", re.IGNORECASE),
            re.compile(r"^\s*(?:广告|推广|声明|免责声明|温馨提示)[:：].{0,160}$", re.IGNORECASE),
            re.compile(r"^\s*请记住(?:本书)?(?:首发)?(?:域名|网址).{0,160}$", re.IGNORECASE),
            re.compile(r"^\s*(?:本章未完|章节报错|内容加载失败|转码失败).{0,120}$"),
            re.compile(r"^\s*={4,}\s*$"),
            re.compile(r"^\s*-{4,}\s*$"),
            re.compile(r"^\s*[·•●◆◇★☆＊*#_~～…—-]{6,}\s*$"),
        ]

        for raw_line in normalized.split("\n"):
            line = self._strip_inline_ad_fragments(raw_line.rstrip())
            stripped = line.strip()
            if stripped and any(pattern.match(stripped) for pattern in noise_patterns):
                continue
            lines.append(line)

        normalized = "\n".join(lines)
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
        return normalized.strip()

    def _strip_inline_ad_fragments(self, line: str) -> str:
        """移除混在正文行内的书源广告、网址和推广片段，尽量保留广告前后的正文。"""
        if not line:
            return line

        cleaned = re.sub(r"https?://[^\s，。！？；：、）】\])>]+", "", line, flags=re.IGNORECASE)
        cleaned = re.sub(r"www\.[^\s，。！？；：、）】\])>]+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?<![A-Za-z0-9])(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|cc|top|xyz|vip|info|me|io|co|tv|la|site|wang|app|shop|club|ink|fun|icu)(?:/[^\s，。！？；：、）】\])>]*)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[\[【（(]\s*[\]】）)]", "", cleaned)
        cleaned = re.sub(
            r"[\[【（(][^\n]{0,180}?(?:520(?:520|\.520)?|超多好看|更新快|广告少|页面清爽|无弹窗|最新章节|免费阅读|棉[\W_]*花[\W_]*糖|笔[\W_]*趣[\W_]*阁|m\.feizw)[^\n]{0,180}?(?:[jJ]|[\]】）)])",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        if re.match(r"^\s*一秒记住.{0,80}(?:笔[\W_]*趣|无弹窗|免费阅读).*$", cleaned, flags=re.IGNORECASE):
            return ""
        if re.match(r"^\s*手机请访问[:：]?.*$", cleaned, flags=re.IGNORECASE):
            return ""

        bracket_pairs = (("【", "】"), ("[", "]"), ("［", "］"), ("(", ")"), ("（", "）"), ("<", ">"), ("《", "》"))
        for left, right in bracket_pairs:
            cleaned = re.sub(
                re.escape(left) + r"([^" + re.escape(left + right) + r"]{0,180})" + re.escape(right),
                lambda match: "" if self._is_inline_ad_text(match.group(1)) else match.group(0),
                cleaned,
            )
            cleaned = re.sub(
                re.escape(left) + r"([^" + re.escape(left + right) + r"]{0,180})$",
                lambda match: "" if self._is_inline_ad_text(match.group(1)) else match.group(0),
                cleaned,
            )

        cleaned = re.sub(
            r"(?:520(?:520|\.520)?|超多好看|棉[\W_]*花[\W_]*糖|笔[\W_]*趣[\W_]*阁)[^。！？\n]{0,80}",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[ \t]{2,}", "  ", cleaned)
        cleaned = re.sub(r"\s+([，。！？；：、])", r"\1", cleaned)
        return cleaned.rstrip()

    def _is_inline_ad_text(self, value: str) -> bool:
        text = (value or "").strip()
        if not text:
            return False
        if re.search(r"https?://|www\.|(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|cc|top|xyz|vip|info|site|wang)", text, re.IGNORECASE):
            return True
        return bool(
            re.search(
                r"更新快|更新最快|无弹窗|广告少|页面清爽|最新章节|全文阅读|高速首发|免费阅读|txt下载|TXT下载|下载地址|请访问|请搜索|收藏本站|加入书架|书友群|读者群|QQ群|微信|推广|广告|520(?:520|\.520)?|超多好看|棉[\W_]*花[\W_]*糖|笔[\W_]*趣[\W_]*阁",
                text,
                re.IGNORECASE,
            )
        )

    def split_chapters(self, text: str) -> list[dict]:
        """
        章节切分（规则优先，失败兜底）

        Returns:
            [{title, content, chapter_number}]
        """
        if not text.strip():
            return []

        marker_candidates = self._find_chapter_markers(text)
        selected_markers = self._select_chapter_markers(marker_candidates)

        if not selected_markers:
            lines = text.split("\n")
            heading_indexes: list[int] = []
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue
                if self._is_strong_heading(stripped) or self._is_weak_heading(lines, idx):
                    heading_indexes.append(idx)

            heading_indexes = sorted(set(heading_indexes))
            if not heading_indexes:
                return self._fallback_split(text)

            return self._split_by_line_headings(text, lines, heading_indexes)

        return self._split_by_markers(text, selected_markers)

    def _split_by_line_headings(self, text: str, lines: list[str], heading_indexes: list[int]) -> list[dict]:
        chapters: list[dict] = []
        chapter_no = 1

        first_heading = heading_indexes[0]
        if first_heading > 0:
            preface = "\n".join(lines[:first_heading]).strip()
            if len(preface) >= 500 and not any(token in preface for token in self.MARKER_NOISE_TOKENS):
                chapters.append(
                    {
                        "title": "前言",
                        "content": preface,
                        "chapter_number": chapter_no,
                    }
                )
                chapter_no += 1

        for i, start_idx in enumerate(heading_indexes):
            end_idx = heading_indexes[i + 1] if i + 1 < len(heading_indexes) else len(lines)
            title = lines[start_idx].strip()[:200] or f"第{chapter_no}章"
            body = "\n".join(lines[start_idx + 1 : end_idx]).strip()
            if not body and i + 1 < len(heading_indexes):
                next_line = lines[start_idx + 1].strip() if start_idx + 1 < len(lines) else ""
                body = next_line

            chapters.append(
                {
                    "title": title,
                    "content": body,
                    "chapter_number": chapter_no,
                }
            )
            chapter_no += 1

        filtered = [c for c in chapters if c["title"] or c["content"]]
        if filtered:
            return filtered

        return self._fallback_split(text)

    def _find_chapter_markers(self, text: str) -> list[dict]:
        markers: list[dict] = []
        for match in self.CHAPTER_MARKER_PATTERN.finditer(text):
            number = match.group(1) or match.group(2) or match.group(3)
            if not number:
                continue
            chapter_number = self._chapter_number_to_int(number)
            if chapter_number is None:
                continue

            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            line_text = text[line_start:line_end].strip()

            if not line_text or any(token in line_text for token in self.MARKER_NOISE_TOKENS):
                continue

            title = text[match.end():line_end].strip()
            markers.append(
                {
                    "number": chapter_number,
                    "line_start": line_start,
                    "start": match.start(),
                    "end": match.end(),
                    "line_end": line_end,
                    "title": title,
                    "line_text": line_text,
                }
            )

        return markers

    def _chapter_number_to_int(self, value: str) -> Optional[int]:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return int(normalized)

        digit_map = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        unit_map = {"十": 10, "百": 100, "千": 1000}
        if all(char in digit_map for char in normalized):
            return int("".join(str(digit_map[char]) for char in normalized))

        total = 0
        section = 0
        number = 0
        for char in normalized:
            if char in digit_map:
                number = digit_map[char]
            elif char in unit_map:
                unit = unit_map[char]
                section += (number or 1) * unit
                number = 0
            elif char == "万":
                total += (section + number or 1) * 10000
                section = 0
                number = 0
            else:
                return None

        result = total + section + number
        return result if result > 0 else None

    def _select_chapter_markers(self, markers: list[dict]) -> list[dict]:
        if not markers:
            return []

        selected: list[dict] = []
        seen_lines: set[int] = set()
        for marker in markers:
            line_start = int(marker.get("line_start") or marker["start"])
            if line_start in seen_lines:
                continue
            selected.append(marker)
            seen_lines.add(line_start)
        return selected

    def _strip_chapter_prefix(self, title: str) -> str:
        """移除章节标题前缀，保留真实标题。"""
        normalized = (title or "").strip()
        if not normalized:
            return normalized

        stripped = re.sub(
            r"^第\s*[0-9零一二三四五六七八九十百千万两〇]+\s*[章节回节]\s*[-—:：、.．）)】\]]*\s*",
            "",
            normalized,
        ).strip()
        stripped = re.sub(r"^[-—:：、.．）)】\]]+\s*", "", stripped).strip()

        return stripped or normalized

    def _split_by_markers(self, text: str, markers: list[dict]) -> list[dict]:
        chapters: list[dict] = []
        if not markers:
            return self._fallback_split(text)

        first_marker = markers[0]
        if first_marker["start"] > 1000:
            preface = text[:first_marker["start"]].strip()
            if len(preface) >= 500 and not any(token in preface for token in self.MARKER_NOISE_TOKENS):
                chapters.append(
                    {
                        "title": "前言",
                        "content": preface,
                        "chapter_number": 1,
                    }
                )

        chapter_no = len(chapters) + 1
        for idx, marker in enumerate(markers):
            next_marker_start = markers[idx + 1]["start"] if idx + 1 < len(markers) else len(text)
            body_start = marker["line_end"] + 1 if marker["line_end"] < len(text) and text[marker["line_end"] : marker["line_end"] + 1] == "\n" else marker["line_end"]
            body = text[body_start:next_marker_start].strip()
            title = (marker["title"] or f"第{marker['number']}章").strip()[:200]
            title = self._strip_chapter_prefix(title)[:200] or f"第{marker['number']}章"

            chapters.append(
                {
                    "title": title,
                    "content": body,
                    "chapter_number": chapter_no,
                }
            )
            chapter_no += 1

        filtered = [c for c in chapters if c["title"] or c["content"]]
        if filtered:
            return filtered

        return self._fallback_split(text)

    def _is_strong_heading(self, line: str) -> bool:
        return any(pattern.match(line) for pattern in self.STRONG_CHAPTER_PATTERNS)

    def _is_weak_heading(self, lines: list[str], idx: int) -> bool:
        """
        弱模式：短行 + 前后空行 + 避免普通句子误判
        """
        line = lines[idx].strip()
        if not line:
            return False
        if len(line) > 25:
            return False
        if re.search(r"[，。！？；：,.!?;:]", line):
            return False

        prev_blank = idx == 0 or not lines[idx - 1].strip()
        next_blank = idx == len(lines) - 1 or not lines[idx + 1].strip()
        return prev_blank and next_blank

    def _fallback_split(self, text: str, min_window: int = 3000, max_window: int = 5000) -> list[dict]:
        """
        固定窗口 + 标点边界切分
        """
        chapters: list[dict] = []
        n = len(text)
        start = 0
        chapter_no = 1
        boundary_punctuations = "。！？!?\n"

        while start < n:
            ideal_end = min(start + max_window, n)
            if ideal_end >= n:
                end = n
            else:
                search_from = min(start + min_window, n)
                segment = text[search_from:ideal_end]
                offset = max(segment.rfind(p) for p in boundary_punctuations)
                end = search_from + offset + 1 if offset >= 0 else ideal_end

            chunk = text[start:end].strip()
            if chunk:
                chapters.append(
                    {
                        "title": f"第{chapter_no}章",
                        "content": chunk,
                        "chapter_number": chapter_no,
                    }
                )
                chapter_no += 1

            start = end

        return chapters


txt_parser_service = TxtParserService()
