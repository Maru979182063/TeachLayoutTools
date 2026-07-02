"""聚焦交付文件名生成逻辑的测试。"""
from app.naming import generate_external_name


def test_generate_external_name_from_original_name():
    """验证generate 外部 名称 from original 名称。"""
    result = generate_external_name(
        "九年级期末真题必刷常考60题（学生版）.pdf",
        {"subject": "数学", "version": "student"},
        "2023-2024学年九年级数学上学期期末考点大串讲 人教版",
        ".pdf",
    )
    assert result["download_name"] == "九年级上学期数学真题必刷常考60题（人教版）（学生版）.pdf"
    assert result["confidence"] >= 0.75
