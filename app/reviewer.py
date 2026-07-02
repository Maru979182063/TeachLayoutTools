"""基于规则的 HTML 审核辅助函数，用于产物批准前检查。"""
from __future__ import annotations

import re


STANDARD_PROFILE = {
    "profile_id": "SP_REVIEW_HANDOUT_STUDENT_MATH_DEFAULT",
    "material_type": "review_handout",
    "version": "student",
    "subject": "math",
    "grade": "default",
    "required_sections": ["learning_goals", "knowledge_points", "examples", "practice"],
    "forbidden_visible_fields": ["answer", "analysis"],
    "required_artifacts": [
        "input_quality.json",
        "parsed_content.json",
        "material_plan.json",
        "material_spec.json",
        "result.html",
        "rule_review_report.json",
    ],
}


def normalize_visible_text(html: str) -> str:
    """把 HTML 标记剥离成可比较的可见文本，供规则检查使用。"""
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def review_html(html: str, material_spec: dict, existing_artifact_names: list[str]) -> dict:
    """对渲染后的 HTML 执行规则检查，并返回结构化问题列表。"""
    issues = []
    visible = normalize_visible_text(html)
    source_type = material_spec.get("source_type")
    render_mode = material_spec.get("render_mode")

    if not html.strip():
        issues.append({"code": "RENDER_EMPTY_OUTPUT", "severity": "high", "message": "HTML 输出为空"})

    for required in STANDARD_PROFILE["required_artifacts"]:
        if required == "rule_review_report.json":
            continue
        if required not in existing_artifact_names:
            issues.append(
                {
                    "code": "RULE_REQUIRED_ARTIFACT_MISSING",
                    "severity": "high",
                    "message": f"缺少 {required}",
                }
            )

    if source_type in {"pdf", "docx"} and render_mode != "fidelity_pdf":
        issues.append(
            {
                "code": "RULE_VISUAL_FALLBACK_USED",
                "severity": "high",
                "message": "PDF/DOCX 未走保真链路，当前结果不可交付",
            }
        )
    if source_type in {"pdf", "docx"} and 'class="page"' not in html:
        issues.append(
            {
                "code": "RULE_FIDELITY_PAGE_MISSING",
                "severity": "high",
                "message": "保真预览页缺失",
            }
        )
    if "data:image/png;base64" not in html:
        issues.append(
            {
                "code": "RULE_LOGO_MISSING",
                "severity": "high",
                "message": "结果中未发现 logo 覆盖",
            }
        )
    if "Student mode: answer and analysis should be masked before delivery." in html:
        issues.append(
            {
                "code": "RULE_INTRUSIVE_ENGLISH_HINT",
                "severity": "high",
                "message": "结果中仍存在压在题面上的英文提示",
            }
        )

    if material_spec.get("version") == "student":
        expected_blank_pages = len(material_spec.get("blank_page_plan") or [])
        if expected_blank_pages and 'class="page answer-sheet"' not in html:
            issues.append(
                {
                    "code": "RULE_STUDENT_BLANK_PAGE_MISSING",
                    "severity": "high",
                    "message": "学生版定向留白页缺失",
                }
            )
        if 'data-field="answer"' in html or 'data-field="analysis"' in html:
            issues.append({"code": "RULE_ANSWER_LEAK", "severity": "high", "message": "学生版包含答案字段标记"})
        for question in material_spec.get("questions", []):
            for field, code in [("answer", "RULE_ANSWER_LEAK"), ("analysis", "RULE_ANALYSIS_LEAK")]:
                value = (question.get(field) or "").strip()
                if len(value) > 2 and value in visible:
                    issues.append(
                        {
                            "code": code,
                            "severity": "high",
                            "message": f"学生版可见文本泄露 {field}",
                        }
                    )

    high_count = sum(1 for item in issues if item["severity"] == "high")
    return {
        "pass": high_count == 0,
        "severity": "high" if high_count else ("warning" if issues else "none"),
        "issues": issues,
        "standard_profile": STANDARD_PROFILE["profile_id"],
    }
