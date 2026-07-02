"""Filename-driven target inference used for batch job defaults."""

from __future__ import annotations

from pathlib import Path


SUBJECT_KEYWORDS = [
    ("\u6570\u5b66", "math"),
    ("\u8bed\u6587", "chinese"),
    ("\u82f1\u8bed", "english"),
    ("\u7269\u7406", "physics"),
    ("\u5316\u5b66", "chemistry"),
    ("\u751f\u7269", "biology"),
    ("\u5386\u53f2", "history"),
    ("\u5730\u7406", "geography"),
    ("\u9053\u5fb7\u4e0e\u6cd5\u6cbb", "politics"),
    ("\u653f\u6cbb", "politics"),
]


def _infer_subject(stem: str) -> str:
    """Infer subject."""
    for keyword, subject in SUBJECT_KEYWORDS:
        if keyword in stem:
            return subject
    return "math"


def infer_target_from_filename(filename: str) -> dict:
    """Infer subject, version, and material defaults from the source filename alone."""
    stem = Path(filename).stem
    version = "student"
    if any(token in stem for token in ["\u53c2\u8003\u7b54\u6848", "\u5168\u89e3\u5168\u6790", "\u89e3\u6790\u7248", "\u7b54\u6848\u7248"]):
        version = "teacher"
    if any(token in stem for token in ["\u8003\u8bd5\u7248", "\u5b66\u751f\u7248", "\u539f\u5377\u7248", "\u539f\u5377"]):
        version = "student"

    if any(token in stem for token in ["\u9ad8\u4e00", "\u9ad8\u4e8c", "\u9ad8\u4e09", "\u9ad8\u8003"]):
        grade = "\u9ad8\u4e2d"
    elif any(token in stem for token in ["\u521d\u4e00", "\u521d\u4e8c", "\u521d\u4e09", "\u4e2d\u8003"]):
        grade = "\u521d\u4e2d"
    else:
        grade = "\u901a\u7528"

    if "\u4e0b\u5b66\u671f" in stem:
        term = "\u4e0b\u5b66\u671f"
    elif "\u4e0a\u5b66\u671f" in stem:
        term = "\u4e0a\u5b66\u671f"
    elif "\u671f\u4e2d" in stem:
        term = "\u671f\u4e2d"
    elif "\u671f\u672b" in stem:
        term = "\u671f\u672b"
    elif "\u4e2d\u8003" in stem:
        term = "\u4e2d\u8003"
    elif "\u9ad8\u8003" in stem:
        term = "\u9ad8\u8003"
    else:
        term = "\u4e13\u9898\u8d44\u6599"

    material_type = "review_handout"
    if any(token in stem for token in ["\u5377", "\u771f\u9898", "\u8bd5\u9898", "\u8bd5\u5377", "\u6a21\u62df", "\u51b2\u523a"]):
        material_type = "exam_paper"
    if any(token in stem for token in ["\u8bb2\u7ec3", "\u8bb2\u4e49", "\u5fc5\u8bb0", "\u6613\u9519", "\u77e5\u8bc6", "\u5b66\u6848"]):
        material_type = "review_handout"

    if "\u6cb3\u5317" in stem:
        textbook = "\u6cb3\u5317\u4e13\u7528"
    elif "\u4eba\u6559\u7248" in stem:
        textbook = "\u4eba\u6559\u7248"
    else:
        textbook = "\u901a\u7528"

    return {
        "material_type": material_type,
        "version": version,
        "subject": _infer_subject(stem),
        "grade": grade,
        "term": term,
        "textbook_version": textbook,
        "title": stem,
    }
