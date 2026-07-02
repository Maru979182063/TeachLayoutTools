"""Focused tests for filename-based target inference."""

from app.targeting import infer_target_from_filename


def test_infer_exam_and_teacher_versions():
    """Verify infer exam and teacher versions."""
    exam = infer_target_from_filename("2026年中考数学临考冲刺卷（河北专用）（考试版）.docx")
    answer = infer_target_from_filename("2026年中考数学临考冲刺卷（河北专用）（参考答案）.docx")
    topic = infer_target_from_filename("绝对值精选题24道.docx")

    assert exam["version"] == "student"
    assert exam["grade"] == "九年级"
    assert exam["textbook_version"] == "河北专用"
    assert answer["version"] == "teacher"
    assert topic["grade"] == "七年级"
    assert topic["term"] == "专题训练"
