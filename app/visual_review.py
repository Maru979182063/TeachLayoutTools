"""面向保真渲染和学生留白页规划的视觉检查辅助函数。"""
from __future__ import annotations

import base64
import re
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader


SUBJECTIVE_PATTERNS = [
    r"解答题",
    r"计算题",
    r"证明题",
    r"作图题",
    r"探究题",
    r"应用题",
    r"综合题",
    r"综合与实践",
    r"阅读理解",
    r"实践探究",
    r"压轴题",
    r"问答题",
    r"请解答",
]

BRAND_PATTERNS = [
    "学科网",
    "www.zxxk.com",
    "zxxk.com",
    "上好每一堂课",
]


def _normalize_text(text: str) -> str:
    """规范化text。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _estimate_blank_lines(text: str) -> int:
    """估算留白 行。"""
    sub_parts = len(re.findall(r"[（(]\d+[)）]", text))
    small_parts = len(re.findall(r"\n?\d+[\.．、]", text))
    proof_bonus = 2 if re.search(r"证明|说明理由|写出过程", text) else 0
    reading_bonus = 2 if re.search(r"阅读与思考|实践探究|综合与实践", text) else 0
    score_values = [int(item) for item in re.findall(r"(\d+)\s*分", text)]
    score_bonus = 0
    if any(score >= 10 for score in score_values):
        score_bonus += 2
    elif any(score >= 8 for score in score_values):
        score_bonus += 1
    if len([score for score in score_values if score >= 8]) >= 2:
        score_bonus += 1
    long_bonus = 2 if len(text) >= 240 else 1 if len(text) >= 150 else 0
    base = 10 + min(sub_parts, 4) * 2 + min(small_parts, 3) + proof_bonus + reading_bonus + score_bonus + long_bonus
    return max(12, min(20, base))


def _classify_subjective_page(text: str) -> tuple[bool, str, int]:
    """判定subjective page。"""
    normalized = _normalize_text(text)
    if not normalized:
        return False, "", 0

    score = 0
    reasons: list[str] = []
    for pattern in SUBJECTIVE_PATTERNS:
        if re.search(pattern, normalized):
            score += 2
            reasons.append(pattern)

    if len(re.findall(r"[（(]\d+[)）]", normalized)) >= 2:
        score += 1
        reasons.append("多小问")
    if re.search(r"解方程|化简|求值|求解|求.*的值|求.*长度|求.*面积|求.*概率", normalized):
        score += 1
        reasons.append("过程题")
    if re.search(r"A[\.．、].*B[\.．、].*C[\.．、].*D[\.．、]", normalized):
        score -= 2
    if re.search(r"填空题", normalized):
        score -= 1

    if score < 2:
        return False, "", 0

    reason = "、".join(dict.fromkeys(reasons)) or "主观题留白"
    return True, reason, _estimate_blank_lines(normalized)


def build_student_blank_plan(pdf_path: Path) -> dict:
    """检查 PDF 页面，并规划学生额外答题留白页该插在什么位置。"""
    reader = PdfReader(str(pdf_path))
    pages = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        needs_blank, reason, line_count = _classify_subjective_page(text)
        if not needs_blank:
            continue
        pages.append(
            {
                "page_number": index + 1,
                "reason": reason,
                "line_count": line_count,
            }
        )
    return {
        "page_numbers": [item["page_number"] for item in pages],
        "pages": pages,
    }


def _extract_page_images(html: str) -> list[Image.Image]:
    """提取page images。"""
    images = []
    for match in re.finditer(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", html):
        raw = base64.b64decode(match.group(1))
        images.append(Image.open(BytesIO(raw)).convert("RGB"))
    return images


def _top_band_dark_ratio(image: Image.Image) -> float:
    """处理top band dark ratio。"""
    width, height = image.size
    top_band = image.crop((0, 0, width, max(48, int(height * 0.09))))
    dark = 0
    total = top_band.size[0] * top_band.size[1]
    for r, g, b in top_band.getdata():
        if r < 232 or g < 232 or b < 232:
            dark += 1
    return dark / total if total else 0.0


def review_fidelity_html_visual(html: str, material_spec: dict) -> dict:
    """检查保真 HTML 中是否残留来源品牌痕迹，以及是否缺少留白页。"""
    issues = []
    lower_html = html.lower()

    for keyword in BRAND_PATTERNS:
        if keyword.lower() in lower_html:
            issues.append(
                {
                    "code": "VISUAL_SOURCE_BRAND_REMAINING",
                    "severity": "high",
                    "message": f"结果中仍存在来源品牌痕迹: {keyword}",
                }
            )

    images = _extract_page_images(html)
    if not images:
        issues.append(
            {
                "code": "VISUAL_PAGE_IMAGE_MISSING",
                "severity": "high",
                "message": "保真页图像未生成",
            }
        )
    else:
        suspicious_pages = []
        for index, image in enumerate(images, start=1):
            if _top_band_dark_ratio(image) > 0.018:
                suspicious_pages.append(index)
        if suspicious_pages:
            issues.append(
                {
                    "code": "VISUAL_HEADER_NOT_CLEAN",
                    "severity": "high",
                    "message": f"页眉区域疑似残留旧品牌或原始头部内容: 第 {', '.join(map(str, suspicious_pages[:8]))} 页",
                }
            )

    expected_blank_pages = len(material_spec.get("blank_page_plan") or [])
    actual_blank_pages = html.count('class="page answer-sheet"')
    if material_spec.get("version") == "student" and expected_blank_pages and actual_blank_pages < expected_blank_pages:
        issues.append(
            {
                "code": "VISUAL_STUDENT_BLANK_MISSING",
                "severity": "high",
                "message": f"学生版定向留白页不足，期望 {expected_blank_pages} 页，实际 {actual_blank_pages} 页",
            }
        )

    high_count = sum(1 for item in issues if item["severity"] == "high")
    return {
        "pass": high_count == 0,
        "severity": "high" if high_count else ("warning" if issues else "none"),
        "issues": issues,
    }
