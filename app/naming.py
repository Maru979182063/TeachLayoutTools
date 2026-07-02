"""把文件线索和抽取文本整理成对人友好的交付文件名的辅助函数。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import safe_filename


GRADE_PATTERNS = [
    "一年级",
    "二年级",
    "三年级",
    "四年级",
    "五年级",
    "六年级",
    "七年级",
    "八年级",
    "九年级",
    "初一",
    "初二",
    "初三",
    "高中",
    "高一",
    "高二",
    "高三",
]

SUBJECT_PATTERNS = [
    "数学",
    "语文",
    "英语",
    "物理",
    "化学",
    "生物学",
    "生物",
    "历史",
    "地理",
    "道德与法治",
    "政治",
]

SUBJECT_DISPLAY = {
    "math": "数学",
    "chinese": "语文",
    "english": "英语",
    "physics": "物理",
    "chemistry": "化学",
    "biology": "生物",
    "history": "历史",
    "geography": "地理",
    "politics": "政治",
    "数学": "数学",
    "语文": "语文",
    "英语": "英语",
    "物理": "物理",
    "化学": "化学",
    "生物": "生物",
    "生物学": "生物",
    "历史": "历史",
    "地理": "地理",
    "道德与法治": "政治",
    "政治": "政治",
}

TEXTBOOK_PATTERNS = [
    "人教版",
    "北师大版",
    "苏教版",
    "沪科版",
    "沪教版",
    "湘教版",
    "冀教版",
    "浙教版",
    "鲁科版",
]

VERSION_STRIP_RE = re.compile(
    r"[（(](?:学生版|考试版|原卷版|原卷|解析版|参考答案|全解全析|教师版|答案版|讲义版)[^)）]{0,20}[)）]"
)
VERSION_TAIL_RE = re.compile(r"(学生版|考试版|原卷版|原卷|解析版|参考答案|全解全析|教师版|答案版|讲义版)$")
LEADING_NOISE_RE = re.compile(r"^(精品解析[:：]|精品解析|FILE_[A-Za-z0-9_]+|0\d+[_\- ]*)")
CHAPTER_TITLE_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十]+\s*[章节]")
KNOWLEDGE_TITLE_RE = re.compile(r"知识|考点|串记|梳理|体系|清单|讲义|学案|方法总结|知识链接")
EXERCISE_TITLE_RE = re.compile(r"易错|专题|专项|小题|讲练测|训练|练习|题组|题单|刷题|必刷")
EXAM_TITLE_RE = re.compile(r"真题|试卷|测试卷|考试|月考|期中|期末|开学考|收心测|冲刺包|过关卷|模拟卷|联考")
EXAM_BODY_RE = re.compile(r"本试卷共|注意事项|考试结束后|答题卡|选择题")


def clean_name_part(value: str | None) -> str | None:
    """处理clean 名称 part。"""
    if value is None:
        return None
    cleaned = safe_filename(str(value)).strip()
    return cleaned or None


def infer_field(candidates: list[str], text: str) -> str | None:
    """推断field。"""
    for item in candidates:
        if item and item in text:
            return item
    return None


def infer_term(text: str) -> str | None:
    """推断学期。"""
    if "上学期" in text or "上册" in text:
        return "上学期"
    if "下学期" in text or "下册" in text:
        return "下学期"
    if "期末" in text:
        return "期末"
    if "期中" in text:
        return "期中"
    if "高考" in text:
        return "高考"
    if "中考" in text:
        return "中考"
    return None


def normalize_grade(value: str | None) -> str | None:
    """规范化年级。"""
    if not value:
        return None
    match = re.search(r"(\d+年级|[一二三四五六七八九]年级|初[一二三]|高[一二三]|高中|初中)", value)
    return match.group(1) if match else value


def _normalize_text(*parts: str) -> str:
    """规范化text。"""
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


def _display_subject(subject: str | None, evidence: str) -> str | None:
    """处理显示 学科。"""
    if subject:
        return SUBJECT_DISPLAY.get(str(subject).strip(), str(subject).strip())
    inferred = infer_field(SUBJECT_PATTERNS, evidence)
    if not inferred:
        return None
    return SUBJECT_DISPLAY.get(inferred, inferred)


def _infer_stage(evidence: str, grade: str | None) -> str | None:
    """推断stage。"""
    if grade in {"高一", "高二", "高三", "高中"}:
        return "高中"
    if grade in {"初一", "初二", "初三", "初中"}:
        return "初中"
    if any(token in evidence for token in ["高考", "高一", "高二", "高三", "高中"]):
        return "高中"
    if any(token in evidence for token in ["中考", "初一", "初二", "初三", "初中"]):
        return "初中"
    return None


def _strip_version_suffix(base_name: str) -> str:
    """处理strip 版本 suffix。"""
    cleaned = VERSION_STRIP_RE.sub("", base_name)
    cleaned = VERSION_TAIL_RE.sub("", cleaned)
    return cleaned.strip(" _-·")


def _strip_source_noise(base_name: str) -> str:
    """处理strip source noise。"""
    cleaned = LEADING_NOISE_RE.sub("", base_name.strip())
    cleaned = cleaned.replace("（网络 收集版）", "")
    cleaned = cleaned.replace("(网络 收集版)", "")
    cleaned = cleaned.replace("网络 收集版", "")
    cleaned = cleaned.replace("（更新ing）", "")
    cleaned = re.sub(r"[ _]+", " ", cleaned)
    cleaned = cleaned.strip(" -_")
    return cleaned


def _normalize_material_title(title: str) -> str:
    """规范化material title。"""
    cleaned = _strip_source_noise(_strip_version_suffix(title))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    swap_match = re.match(r"^(?P<prefix>高考考前必记易错要点.*?)(?P<suffix>20\d{2}年高考.*?讲练测)$", cleaned)
    if swap_match:
        cleaned = f"{swap_match.group('suffix')}·{swap_match.group('prefix')}"
    return cleaned.strip(" ·")


def infer_material_category(target: dict[str, Any], evidence: str, ext: str) -> str:
    """推断material category。"""
    explicit = clean_name_part(target.get("material_category"))
    if explicit:
        return explicit

    mode = str(target.get("resolved_mode") or target.get("mode") or "")
    title = str(target.get("title") or "")
    text = _normalize_text(title, evidence)

    if ext == ".zip":
        if KNOWLEDGE_TITLE_RE.search(text):
            return "知识类"
        if EXERCISE_TITLE_RE.search(text):
            return "习题类"
        if EXAM_TITLE_RE.search(text):
            return "试卷类"
        return "资料包"

    if mode == "chapter_handout_keep":
        if EXERCISE_TITLE_RE.search(text) and not CHAPTER_TITLE_RE.search(title):
            return "习题类"
        return "知识类"

    if EXAM_TITLE_RE.search(text) or EXAM_BODY_RE.search(evidence):
        return "试卷类"
    if KNOWLEDGE_TITLE_RE.search(text):
        return "知识类"
    if EXERCISE_TITLE_RE.search(text):
        return "习题类"
    return "知识类"


def _infer_version_label(target: dict[str, Any], category: str, title: str, evidence: str) -> str | None:
    """推断版本 label。"""
    mode = str(target.get("resolved_mode") or target.get("mode") or "")
    requested_version = str(target.get("version") or "")
    text = _normalize_text(title, evidence)
    subject_hint = str(target.get("subject") or "")
    is_biology = subject_hint in {"biology", "生物"} or "生物" in text

    if category == "试卷类":
        if any(token in text for token in ["解析版", "参考答案", "全解全析", "教师版", "答案版"]) or requested_version == "teacher" or mode == "teacher_keep":
            return "教师版" if is_biology else "解析版"
        if any(token in text for token in ["学生版", "考试版", "原卷版", "原卷"]) or requested_version == "student":
            return "学生版"

    if category == "知识类":
        if mode == "chapter_handout_keep":
            if KNOWLEDGE_TITLE_RE.search(title):
                return None
            return "讲义版"

    if category == "习题类":
        if any(token in text for token in ["解析版", "教师版", "答案版"]) or requested_version == "teacher":
            return "教师版" if is_biology else "解析版"
        if any(token in text for token in ["学生版", "考试版", "原卷版", "原卷"]):
            return "学生版"
    return None


def _inject_subject_prefix(title: str, subject_display: str | None, grade: str | None, stage: str | None, category: str) -> str:
    """处理inject 学科 prefix。"""
    if not subject_display or subject_display in title:
        return title
    if category != "知识类":
        return title
    if CHAPTER_TITLE_RE.search(title) or KNOWLEDGE_TITLE_RE.search(title):
        prefix = grade or stage or ""
        return f"{prefix}{subject_display}{title}"
    return title


def infer_material_type(original_name: str, text: str, ext: str) -> str:
    """推断material type。"""
    title = _normalize_material_title(Path(original_name).stem)
    if title:
        return title
    if ext == ".zip":
        return "资料包"
    if "真题" in text:
        return "真题资料"
    if "讲练测" in text:
        return "讲练测"
    if "易错" in text:
        return "易错专题"
    return "复习资料"


def generate_external_name(
    original_name: str,
    target: dict[str, Any],
    extracted_text: str = "",
    artifact_ext: str | None = None,
) -> dict[str, Any]:
    """处理generate 外部 名称。"""
    ext = artifact_ext or Path(original_name).suffix.lower() or ".html"
    title_hint = clean_name_part(target.get("material_title") or target.get("title")) or Path(original_name).stem
    evidence = _normalize_text(
        original_name,
        title_hint,
        str(target.get("grade") or ""),
        str(target.get("subject") or ""),
        extracted_text[:4000],
    )

    grade = normalize_grade(target.get("grade")) or infer_field(GRADE_PATTERNS, evidence)
    stage = _infer_stage(evidence, grade)
    subject_display = _display_subject(target.get("subject"), evidence)
    textbook = target.get("textbook_version") or infer_field(TEXTBOOK_PATTERNS, evidence)
    term = target.get("term") or infer_term(evidence)
    material_category = infer_material_category(target, evidence, ext)

    title = _normalize_material_title(title_hint or infer_material_type(original_name, evidence, ext))
    if not title:
        title = infer_material_type(original_name, evidence, ext)
    title = _inject_subject_prefix(title, subject_display, grade, stage, material_category)

    version_label = _infer_version_label(target, material_category, title, evidence)
    if version_label:
        title = _strip_version_suffix(title)
        if version_label not in title:
            title = f"{title}（{version_label}）"

    if ext == ".zip" and not title.endswith("资料包") and material_category == "资料包":
        title = f"{title}资料包"

    filename = f"{safe_filename(title)}{ext}"

    confidence = 0.55
    sources = ["title"]
    for key, value in {
        "grade": grade,
        "subject": subject_display,
        "term": term,
        "textbook": textbook,
        "material_category": material_category,
        "version_label": version_label,
    }.items():
        if value:
            confidence += 0.06
            sources.append(key)
    confidence = min(confidence, 0.96)

    warnings: list[str] = []
    if material_category not in {"试卷类", "知识类", "习题类", "资料包"}:
        warnings.append("资料类型未稳定命中，建议人工复核")

    return {
        "display_name": filename,
        "download_name": filename,
        "confidence": round(confidence, 2),
        "sources": sources,
        "warnings": warnings,
        "fields": {
            "grade": grade,
            "stage": stage,
            "subject": subject_display,
            "term": term,
            "textbook": textbook,
            "material_type": title,
            "material_category": material_category,
            "version_label": version_label,
        },
    }
