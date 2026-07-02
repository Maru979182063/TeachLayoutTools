"""HTML rendering helpers for template output and PDF fidelity preview output."""

from __future__ import annotations

import base64
import html
import re
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from jinja2 import Template
from markupsafe import Markup
from PIL import Image, ImageDraw


def logo_data_url() -> str:
    """Load the embedded logo image used by generated HTML pages."""
    logo = Path(__file__).resolve().parent / "static" / "assets" / "component-library" / "background-logo.png"
    if not logo.exists():
        return ""
    data = base64.b64encode(logo.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def format_math_text(text: str) -> str:
    """Escape plain text and apply a few lightweight math-friendly HTML tweaks."""
    escaped = html.escape(text)
    escaped = re.sub(r"([a-zA-Z])(\d)", r"\\1<sup>\\2</sup>", escaped)
    escaped = re.sub(r"\s{5,}", " ", escaped)
    return escaped


def format_stem(stem: str) -> Markup:
    """Format one question stem and lay out multiple-choice options when detected."""
    text = stem.strip()
    option_match = re.search(r"(A\.|B\.|C\.|D\.)", text, flags=re.S)
    if not option_match:
        return Markup(format_math_text(text))

    stem_part = text[: option_match.start()].strip()
    option_part = text[option_match.start() :].strip()
    raw_options = re.findall(r"([A-D][\.：:][^A-D]*)(?=\s+[A-D][\.：:]|$)", option_part, flags=re.S)
    if len(raw_options) < 2:
        return Markup(format_math_text(text))

    options_html = "".join(f"<span>{format_math_text(opt.strip())}</span>" for opt in raw_options[:4])
    return Markup(f"{format_math_text(stem_part)}<div class=\"options\">{options_html}</div>")


HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <title>{{ title }}</title>
  <style>
    @page { size: A4; margin: 22mm 18mm 18mm; }
    body {
      font-family: \"SimSun\", \"STSong\", \"Microsoft YaHei\", serif;
      margin: 0 auto;
      color: #000;
      max-width: 790px;
      line-height: 1.75;
      font-size: 16px;
      background: #fff;
    }
    body::before {
      content: \"\";
      position: fixed;
      left: 20px;
      top: 18px;
      width: 130px;
      height: 42px;
      background: url(\"{{ logo_url }}\") left center / contain no-repeat;
      opacity: 1;
      pointer-events: none;
    }
    header {
      border-top: 2px solid #000;
      margin: 54px 0 20px;
      padding-top: 26px;
      text-align: center;
    }
    h1 { font-size: 27px; margin: 0; font-weight: 700; letter-spacing: 0; }
    .meta { display: none; }
    .section-heading {
      margin: 18px 0 8px;
      font-size: 17px;
      font-weight: 700;
      break-after: avoid;
    }
    .question {
      margin: 10px 0 14px;
      line-height: 1.78;
    }
    .q-line { display: flex; align-items: flex-start; gap: 8px; }
    .q-number { flex: 0 0 auto; min-width: 24px; }
    .q-stem { white-space: pre-wrap; flex: 1; }
    .blank { display: inline-block; min-width: 96px; border-bottom: 1px solid #000; height: 16px; vertical-align: -2px; }
    .options {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 18px;
      margin-top: 8px;
      white-space: normal;
    }
    .options span { white-space: nowrap; }
    footer { margin-top: 32px; text-align: center; font-size: 12px; color: #000; }
    @media screen { body { padding: 26px 60px 40px; box-shadow: 0 0 0 1px #eee; } }
  </style>
</head>
<body>
  <header>
    <h1>{{ title }}</h1>
    <div class=\"meta\">{{ grade }} / {{ subject }} / {{ version_label }}</div>
  </header>
  <main>
    {% for q in questions %}
    {% if q.section and q.section != last_section.value %}
    {% set _ = last_section.update({\"value\": q.section}) %}
    <div class=\"section-heading\">{{ q.section }}</div>
    {% endif %}
    <div class=\"question\">
      <div class=\"q-line\">
        <span class=\"q-number\">{{ q.number }}.</span>
        <span class=\"q-stem\">{{ q.html_stem }}</span>
      </div>
    </div>
    {% endfor %}
  </main>
  <footer>1</footer>
</body>
</html>"""
)


FIDELITY_TEMPLATE = Template(
    """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <title>{{ title }}</title>
  <style>
    @page { size: A4; margin: 0; }
    html, body { margin: 0; padding: 0; background: #eef1f5; }
    body { font-family: \"Microsoft YaHei\", \"Noto Sans CJK SC\", sans-serif; }
    .logo {
      width: 84px;
      height: 28px;
      background: url(\"{{ logo_url }}\") left center / contain no-repeat;
      opacity: 0.82;
    }
    .logo-mark {
      position: absolute;
      top: 12px;
      left: 14px;
      width: 72px;
      height: 24px;
      background: url(\"{{ logo_url }}\") left center / contain no-repeat;
      opacity: 0.8;
      pointer-events: none;
    }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 10px 16px;
      border-bottom: 1px solid #d0d5dd;
      background: rgba(255,255,255,.94);
      backdrop-filter: blur(8px);
      color: #182230;
      font-size: 13px;
    }
    .toolbar strong { font-size: 14px; }
    .pages {
      display: grid;
      gap: 18px;
      justify-items: center;
      padding: 18px;
    }
    .page {
      width: min(100%, 794px);
      background: #fff;
      box-shadow: 0 1px 3px rgba(16,24,40,.12), 0 12px 28px rgba(16,24,40,.12);
      page-break-after: always;
      position: relative;
    }
    .page img { display: block; width: 100%; height: auto; }
    .answer-sheet {
      box-sizing: border-box;
      padding: 26mm 18mm 18mm;
      min-height: 1122px;
    }
    .answer-sheet h3 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      color: #101828;
    }
    .answer-sheet p {
      margin: 8px 0 0;
      color: #475467;
      font-size: 13px;
    }
    .answer-lines {
      margin-top: 18px;
      display: grid;
      gap: 12px;
    }
    .answer-line {
      min-height: 30px;
      border-bottom: 1px dashed #cbd5e1;
    }
    .page-number {
      position: absolute;
      right: 10px;
      bottom: 8px;
      color: rgba(24,34,48,.42);
      font-size: 11px;
    }
    @media print {
      body { background: #fff; }
      .toolbar { display: none; }
      .pages { display: block; padding: 0; }
      .page {
        width: 210mm;
        box-shadow: none;
        page-break-after: always;
      }
    }
  </style>
</head>
<body>
  <div class=\"toolbar\">
    <span class=\"logo\" aria-hidden=\"true\"></span>
    <strong>{{ title }}</strong>
    <span>{{ toolbar_note }}</span>
  </div>
  <main class=\"pages\">
    {% for page in pages %}
    <section class=\"page\">
      <img src=\"{{ page.src }}\" alt=\"第 {{ page.number }} 页\">
      <span class=\"logo-mark\" aria-hidden=\"true\"></span>
      <span class=\"page-number\">{{ page.number }}</span>
    </section>
    {% if page.blank_sheet %}
    <section class=\"page answer-sheet\" data-source-page=\"{{ page.number }}\" data-lines=\"{{ page.blank_sheet.line_count }}\">
      <h3>第 {{ page.number }} 页对应作答留白</h3>
      <p>{{ page.blank_sheet.reason }}</p>
      <div class=\"answer-lines\">
        {% for _ in range(page.blank_sheet.line_count) %}
        <div class=\"answer-line\"></div>
        {% endfor %}
      </div>
      <span class=\"page-number\">答题留白</span>
    </section>
    {% endif %}
    {% endfor %}
  </main>
</body>
</html>"""
)


def render_html(material_spec: dict, generated_name: str) -> str:
    """Render the template-based HTML output used for non-fidelity flows."""
    questions = []
    for question in material_spec.get("questions", []):
        copied = dict(question)
        copied["html_stem"] = format_stem(copied.get("stem") or "")
        questions.append(copied)
    return HTML_TEMPLATE.render(
        title=material_spec.get("title") or generated_name,
        grade=material_spec.get("grade") or "未知年级",
        subject=material_spec.get("subject") or "未知学科",
        version=material_spec.get("version") or "student",
        version_label="学生版" if material_spec.get("version") == "student" else "解析版",
        questions=questions,
        generated_name=generated_name,
        student_mode=material_spec.get("version") == "student",
        logo_url=logo_data_url(),
        last_section={"value": ""},
    )


def render_pdf_fidelity_html(pdf_path: Path, material_spec: dict, generated_name: str) -> str:
    """Render a PDF fidelity preview that reuses page images instead of rewriting content."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    blank_page_map = {item["page_number"]: item for item in material_spec.get("blank_page_plan", [])}
    pages = []
    for index in range(len(pdf)):
        page = pdf[index]
        bitmap = page.render(scale=1.75).to_pil().convert("RGB")
        width, height = bitmap.size
        header_pad = max(72, int(height * 0.055))
        top_brand_height = max(84, int(height * 0.065))
        cleaned = bitmap.copy()
        draw = ImageDraw.Draw(cleaned)
        draw.rectangle((0, 0, width, top_brand_height), fill=(255, 255, 255))
        canvas = Image.new("RGB", (width, height + header_pad), (255, 255, 255))
        canvas.paste(cleaned, (0, header_pad))
        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=82, optimize=True)
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        pages.append(
            {
                "number": index + 1,
                "src": f"data:image/jpeg;base64,{data}",
                "blank_sheet": blank_page_map.get(index + 1) if material_spec.get("version") == "student" else None,
            }
        )
    blank_count = len(blank_page_map) if material_spec.get("version") == "student" else 0
    return FIDELITY_TEMPLATE.render(
        title=material_spec.get("title") or generated_name,
        generated_name=generated_name,
        pages=pages,
        page_count=len(pages),
        student_mode=material_spec.get("version") == "student",
        logo_url=logo_data_url(),
        toolbar_note=(
            f"学生版保真预览 / 原页 {len(pages)} 页 / 定向留白 {blank_count} 页 / 待人工审核"
            if material_spec.get("version") == "student"
            else f"教师版保真预览 / {len(pages)} 页 / 待人工审核"
        ),
    )
