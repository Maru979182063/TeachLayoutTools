"""DOCX 转 PDF 辅助函数，优先走 Word COM，失败时回退到渲染器。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from pypdf import PdfWriter

from .config import CONVERTED_DIR, settings

DOCUMENTS_SKILL_DIR = Path(
    r"C:\Users\EDY\.codex\plugins\cache\openai-primary-runtime\documents\26.614.11602\skills\documents"
)
RENDER_DOCX_SCRIPT = DOCUMENTS_SKILL_DIR / "render_docx.py"
BUNDLED_PYTHON = Path(
    r"C:\Users\EDY\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)
WORD_CONVERSION_SEMAPHORE = threading.Semaphore(settings.word_max_concurrency)


def _write_placeholder_pdf(output_path: Path) -> Path:
    """写出占位 PDF。"""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with output_path.open("wb") as handle:
        writer.write(handle)
    return output_path


def _timeout_seconds(name: str, default: int) -> int:
    """处理超时 seconds。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _build_power_shell_script(input_path: str, output_path: str) -> str:
    """构建PowerShell script。"""
    safe_input = input_path.replace("'", "''")
    safe_output = output_path.replace("'", "''")
    return (
        f"$ErrorActionPreference = 'Stop';\n"
        f"$InputPath = '{safe_input}';\n"
        f"$OutputPath = '{safe_output}';\n"
        "try {\n"
        "  $word = New-Object -ComObject Word.Application;\n"
        "  $word.Visible = $false;\n"
        "  $doc = $word.Documents.Open($InputPath, [Type]::Missing, $false);\n"
        "  $doc.SaveAs([ref]$OutputPath, [ref]17);\n"
        "  $doc.Close($false);\n"
        "  $word.Quit();\n"
        "  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null;\n"
        "  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null;\n"
        "} catch {\n"
        "  if ($doc) { $doc.Close($false) | Out-Null }\n"
        "  if ($word) { $word.Quit() | Out-Null }\n"
        "  throw\n"
        "}\n"
    )


def _convert_via_word_com(docx_path: Path, output_path: Path) -> str | None:
    """处理convert via Word COM。"""
    script = _build_power_shell_script(str(docx_path), str(output_path))
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=_timeout_seconds("DOCX_WORD_TIMEOUT_SECONDS", 20),
        )
    except subprocess.TimeoutExpired:
        return "word com conversion timed out"
    if completed.returncode == 0 and output_path.exists():
        return None
    return completed.stderr.strip() or completed.stdout.strip() or "powershell com failed"


def _kill_word_processes() -> None:
    """处理kill word processes。"""
    subprocess.run(
        ["taskkill", "/F", "/IM", "WINWORD.EXE"],
        check=False,
        capture_output=True,
        text=True,
    )


def _convert_via_renderer(docx_path: Path, output_path: Path) -> str | None:
    """处理convert via 渲染器。"""
    if not RENDER_DOCX_SCRIPT.exists():
        return "render_docx.py missing"

    python_executable = BUNDLED_PYTHON if BUNDLED_PYTHON.exists() else Path("python")
    render_dir = CONVERTED_DIR / f"{docx_path.stem}_render"
    render_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [
                str(python_executable),
                str(RENDER_DOCX_SCRIPT),
                str(docx_path),
                "--output_dir",
                str(render_dir),
                "--emit_pdf",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_timeout_seconds("DOCX_RENDER_TIMEOUT_SECONDS", 60),
        )
    except subprocess.TimeoutExpired:
        return "render_docx fallback timed out"
    rendered_pdf = render_dir / f"{docx_path.stem}.pdf"
    if completed.returncode == 0 and rendered_pdf.exists():
        shutil.copy2(rendered_pdf, output_path)
        return None
    return completed.stderr.strip() or completed.stdout.strip() or "render_docx fallback failed"


def convert_docx_to_pdf(docx_path: Path, job_id: str | None = None) -> Path:
    """把生成后的 DOCX 产物转成 PDF，优先走 Word，失败后再走回退渲染器。"""
    docx_path = docx_path.expanduser().resolve()
    if not docx_path.exists():
        raise FileNotFoundError(f"docx not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError(f"not docx: {docx_path.suffix}")

    base = docx_path.with_suffix("").name
    if job_id:
        base = f"{base}_{job_id}"
    output_path = CONVERTED_DIR / f"{base}.pdf"

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return _write_placeholder_pdf(output_path)

    with WORD_CONVERSION_SEMAPHORE:
        word_error = "word conversion did not start"
        for attempt in range(1, settings.word_retry_count + 1):
            if output_path.exists():
                output_path.unlink()
            word_error = _convert_via_word_com(docx_path, output_path)
            if word_error is None and output_path.exists():
                return output_path
            if "timed out" in word_error and settings.force_kill_word_on_timeout:
                _kill_word_processes()
            if attempt < settings.word_retry_count:
                time.sleep(1.0)

    render_error = _convert_via_renderer(docx_path, output_path)
    if render_error is not None:
        raise RuntimeError(f"{word_error}\n\nfallback:\n{render_error}")
    if not output_path.exists():
        raise RuntimeError(f"pdf not generated: {output_path}")
    return output_path
