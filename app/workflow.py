"""后台队列调度、状态流转、产物生成和审批流程。"""
from __future__ import annotations

import queue
import threading
from itertools import count
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .docx_converter import convert_docx_to_pdf
from .docx_processor import process_docx_source
from .models import Artifact, FileRecord, JobEvent, JobStatus, MaterialJob, NotificationEvent, TERMINAL_STATUSES, utcnow
from .model_provider import plan_with_doubao
from .naming import generate_external_name
from .parser import extract_text, parse_questions, qualify_input
from .reviewer import review_html
from .storage import write_artifact
from .templates import render_html, render_pdf_fidelity_html
from .utils import from_json, new_id, to_json
from .visual_review import build_student_blank_plan, review_fidelity_html_visual

# API 进程内共享的队列与工作线程状态。
STEP_TIMEOUT_SECONDS = 500
WORKER_RETRIES = 3
QUEUE_WAIT_TIMEOUT_SECONDS = max(1, settings.queue_poll_seconds)
_JOB_QUEUE: queue.PriorityQueue[tuple[int, int, str | None]] = queue.PriorityQueue()
_QUEUE_LOCK = threading.Lock()
_QUEUED_JOB_IDS: set[str] = set()
_ACTIVE_JOB_IDS: set[str] = set()
_WORKER_THREADS: list[threading.Thread] = []
_WORKERS_STARTED = False
_SHUTDOWN_EVENT = threading.Event()
_JOB_SEQUENCE = count()


def _placeholder_pdf_bytes() -> bytes:
    """为测试模式或优雅降级生成一个单页空白 PDF。"""
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    return buffer.getvalue()


ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.PARSING, JobStatus.CANCELLED},
    JobStatus.PARSING: {JobStatus.PLANNING, JobStatus.PARSE_FAILED, JobStatus.HUMAN_REVIEW_REQUIRED, JobStatus.CANCELLED},
    JobStatus.PARSE_FAILED: {JobStatus.FAILED, JobStatus.HUMAN_REVIEW_REQUIRED},
    JobStatus.PLANNING: {JobStatus.NORMALIZING, JobStatus.PLAN_FAILED, JobStatus.CANCELLED},
    JobStatus.NORMALIZING: {JobStatus.RENDERING, JobStatus.NORMALIZE_FAILED, JobStatus.CANCELLED},
    JobStatus.RENDERING: {JobStatus.RULE_REVIEWING, JobStatus.CONVERT_FAILED, JobStatus.CANCELLED},
    JobStatus.RULE_REVIEWING: {JobStatus.PATCHING, JobStatus.HUMAN_REVIEW_REQUIRED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.PATCHING: {JobStatus.RENDERING, JobStatus.HUMAN_REVIEW_REQUIRED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.HUMAN_REVIEW_REQUIRED: {JobStatus.SUCCEEDED, JobStatus.CANCELLED},
}


# 任务状态流转与通知记录辅助函数。
def add_event(
    db: Session,
    job: MaterialJob,
    event_type: str,
    message: str,
    from_status: str | None = None,
    to_status: str | None = None,
    level: str = "info",
    metadata: dict | None = None,
) -> None:
    """向任务历史中追加一条结构化工作流事件。"""
    db.add(
        JobEvent(
            id=new_id("EVT"),
            job_id=job.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            step=job.current_step,
            attempt=job.render_attempt,
            level=level,
            message=message,
            user_visible=True,
            metadata_json=to_json(metadata or {}),
        )
    )


def transition(db: Session, job: MaterialJob, to_status: JobStatus, step: str | None = None) -> bool:
    """校验并提交一次任务状态流转。"""
    if job.status in TERMINAL_STATUSES and job.status != JobStatus.HUMAN_REVIEW_REQUIRED:
        return False
    allowed = ALLOWED_TRANSITIONS.get(job.status, set())
    if to_status not in allowed and not (job.status == to_status):
        add_event(
            db,
            job,
            "STATE_TRANSITION_INVALID",
            f"state transition invalid: {job.status.value} -> {to_status.value}",
            level="error",
        )
        return False
    before = job.status.value
    job.status = to_status
    job.current_step = step or to_status.value
    job.updated_at = utcnow()
    if to_status in TERMINAL_STATUSES:
        job.finished_at = utcnow()
    add_event(db, job, "STATUS_CHANGED", f"status changed to {to_status.value}", before, to_status.value)
    db.commit()
    return True


def request_cancel(db: Session, job: MaterialJob) -> None:
    """取消任务，并写入对应状态事件和通知记录。"""
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise ValueError("JOB_NOT_CANCELABLE")
    job.cancel_requested = True
    job.status = JobStatus.CANCELLED
    job.user_visible_message = "task has been canceled"
    job.finished_at = utcnow()
    job.updated_at = utcnow()
    add_event(db, job, "JOB_CANCELLED", "cancel requested")
    create_notification(db, job, "JOB_CANCELLED")
    db.commit()


def ensure_not_cancelled(db: Session, job: MaterialJob) -> None:
    """确保not cancelled。"""
    db.refresh(job)
    if job.cancel_requested or job.status == JobStatus.CANCELLED:
        raise RuntimeError("JOB_CANCELLED")


def create_notification(db: Session, job: MaterialJob, event_type: str) -> None:
    """为当前任务状态创建一条去重后的通知记录。"""
    dedupe = f"{job.id}:{job.status.value}:mock:mock-default"
    exists = db.query(NotificationEvent).filter(NotificationEvent.dedupe_key == dedupe).first()
    if exists:
        return
    payload = {
        "title": "material_workflow",
        "job_id": job.id,
        "status": job.status.value,
        "artifact_id": job.current_artifact_id,
        "message": job.user_visible_message,
    }
    db.add(
        NotificationEvent(
            id=new_id("NE"),
            job_id=job.id,
            event_type=event_type,
            target_status=job.status.value,
            dedupe_key=dedupe,
            payload_json=to_json(payload),
            status="SENT",
        )
        )


# 队列生命周期辅助函数。
def start_job(job_id: str) -> None:
    """把新任务压入后台队列。"""
    enqueue_job(job_id)


def runtime_status() -> dict:
    """返回工作线程和队列数量，供运行时诊断使用。"""
    with _QUEUE_LOCK:
        return {
            "worker_count": len(_WORKER_THREADS),
            "configured_worker_count": _effective_worker_count(),
            "queued_jobs": len(_QUEUED_JOB_IDS),
            "active_jobs": len(_ACTIVE_JOB_IDS),
            "queue_size": _JOB_QUEUE.qsize(),
        }


def _effective_worker_count() -> int:
    """处理生效 工作线程 count。"""
    if settings.database_url.startswith("sqlite"):
        return 1
    return settings.worker_count


def ensure_worker_pool_started() -> None:
    """为当前进程启动一次后台工作线程池。"""
    global _WORKERS_STARTED
    with _QUEUE_LOCK:
        if _WORKERS_STARTED:
            return
        _SHUTDOWN_EVENT.clear()
        for index in range(_effective_worker_count()):
            thread = threading.Thread(
                target=_worker_loop,
                name=f"material-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            _WORKER_THREADS.append(thread)
        _WORKERS_STARTED = True


def stop_worker_pool() -> None:
    """发出工作线程关闭信号，并清理内存中的队列记录。"""
    global _WORKERS_STARTED
    with _QUEUE_LOCK:
        if not _WORKERS_STARTED:
            return
        _WORKERS_STARTED = False
        _SHUTDOWN_EVENT.set()
        for _ in _WORKER_THREADS:
            _JOB_QUEUE.put((99, next(_JOB_SEQUENCE), None))
        _QUEUED_JOB_IDS.clear()
        _ACTIVE_JOB_IDS.clear()
        _WORKER_THREADS.clear()


def enqueue_job(job_id: str, recovered: bool = False) -> bool:
    """除非任务已经在队列中或正在执行，否则把它加入队列。"""
    ensure_worker_pool_started()
    with _QUEUE_LOCK:
        if job_id in _QUEUED_JOB_IDS or job_id in _ACTIVE_JOB_IDS:
            return False
        _QUEUED_JOB_IDS.add(job_id)
        priority = 1 if recovered else 0
        _JOB_QUEUE.put((priority, next(_JOB_SEQUENCE), job_id))
        return True


def requeue_incomplete_jobs() -> int:
    """如果开启恢复机制，则在进程启动后把最近未完成任务重新入队。"""
    ensure_worker_pool_started()
    if not settings.recover_incomplete_jobs:
        return 0
    db = SessionLocal()
    try:
        recover_after = utcnow() - timedelta(hours=settings.recover_max_age_hours)
        rows = (
            db.query(MaterialJob.id)
            .filter(
                MaterialJob.status.not_in(tuple(TERMINAL_STATUSES)),
                MaterialJob.updated_at >= recover_after,
            )
            .order_by(MaterialJob.created_at.asc())
            .all()
        )
        recovered = 0
        for row in rows:
            if enqueue_job(row.id, recovered=True):
                recovered += 1
        return recovered
    finally:
        db.close()


# 工作线程执行辅助函数。
def _worker_loop() -> None:
    """处理工作线程 loop。"""
    while not _SHUTDOWN_EVENT.is_set():
        try:
            _, _, job_id = _JOB_QUEUE.get(timeout=QUEUE_WAIT_TIMEOUT_SECONDS)
        except queue.Empty:
            continue
        if job_id is None:
            _JOB_QUEUE.task_done()
            break
        with _QUEUE_LOCK:
            _QUEUED_JOB_IDS.discard(job_id)
            _ACTIVE_JOB_IDS.add(job_id)
        try:
            run_job_with_retries(job_id)
        finally:
            with _QUEUE_LOCK:
                _ACTIVE_JOB_IDS.discard(job_id)
            _JOB_QUEUE.task_done()


def run_job_with_retries(job_id: str) -> None:
    """为任务执行包上一层工作线程级重试和最终失败处理。"""
    for attempt in range(1, WORKER_RETRIES + 1):
        try:
            run_job(job_id)
            return
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == "JOB_CANCELLED":
                return
            db = SessionLocal()
            try:
                job = db.get(MaterialJob, job_id)
                if not job or job.status in TERMINAL_STATUSES:
                    return
                add_event(
                    db,
                    job,
                    "WORKER_ATTEMPT_FAILED",
                    f"worker attempt {attempt} failed",
                    level="error",
                    metadata={"error": str(exc)},
                )
                if attempt >= WORKER_RETRIES:
                    job.status = JobStatus.FAILED
                    job.error_code = "WORKER_RETRY_EXHAUSTED"
                    job.user_visible_message = "processing failed after retries"
                    job.finished_at = utcnow()
                    create_notification(db, job, "JOB_FAILED")
                else:
                    job.status = JobStatus.QUEUED
                    job.current_step = "queued_retry"
                db.commit()
            finally:
                db.close()


def _build_material_plan(source: FileRecord, target: dict, text: str, input_quality: dict, parsed_questions: list[dict]) -> dict:
    """为任务选择初始规划来源，并规范化相关元数据。"""
    parsed_content = {"text": text[:20000], "questions": parsed_questions, "input_quality": input_quality}
    material_type_default = generate_external_name(source.original_name, target, text)["fields"]["material_type"]
    if source.file_type == "docx":
        return {
            "plan": {
                "title": target.get("title") or material_type_default,
                "sections": ["learning_goals", "knowledge_points", "examples", "practice"],
                "questions": parsed_questions,
                "confidence": input_quality["confidence"],
                "render_mode": "word_first_docx",
            },
            "event_type": "DOCX_WORD_FIRST_PLAN",
            "event_message": "word-first docx plan generated",
            "event_level": "info",
            "event_metadata": {"provider": "deterministic"},
        }
    try:
        doubao_plan = plan_with_doubao(parsed_content, target)
        if doubao_plan:
            doubao_plan["questions"] = parsed_questions
            doubao_plan.setdefault("render_mode", "model_rewrite")
            return {
                "plan": doubao_plan,
                "event_type": "MODEL_PLAN_SUCCEEDED",
                "event_message": "model plan generated",
                "event_level": "info",
                "event_metadata": {"provider": "doubao"},
            }
        raise RuntimeError("plan empty")
    except Exception as exc:
        return {
            "plan": {
                "title": target.get("title") or material_type_default,
                "sections": ["learning_goals", "knowledge_points", "examples", "practice"],
                "questions": parsed_questions,
                "confidence": input_quality["confidence"],
                "render_mode": "fallback_template",
            },
            "event_type": "MODEL_PLAN_FALLBACK",
            "event_message": "model plan failed, fallback template",
            "event_level": "warning",
            "event_metadata": {"error": str(exc)[:500]},
        }


# Word-first DOCX 链路专用的收尾处理。
def _finish_word_first_docx(
    db: Session,
    job: MaterialJob,
    source: FileRecord,
    source_path: Path,
    target: dict,
    text: str,
    material_spec: dict,
) -> None:
    """写出 DOCX-first 链路产物，做轻量审核，并把任务停在人工批准阶段。"""
    transition(db, job, JobStatus.RENDERING, "render_word_first_docx")
    ensure_not_cancelled(db, job)
    job.render_attempt += 1
    naming_target = {**target}

    processed = process_docx_source(source_path, job.id, target, text)
    naming_target["resolved_mode"] = processed.report.get("resolved_mode")
    docx_artifact = write_artifact(
        db,
        job,
        "docx",
        "RENDERING",
        "result.docx",
        processed.output_path.read_bytes(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        job.render_attempt,
        naming_target,
        text,
        naming_name=source.original_name,
    )
    pdf_conversion_error: str | None = None
    try:
        pdf_path = convert_docx_to_pdf(processed.output_path, job.id)
        write_artifact(
            db,
            job,
            "pdf",
            "RENDERING",
            "result.pdf",
            pdf_path.read_bytes(),
            "application/pdf",
            job.render_attempt,
            naming_target,
            text,
            naming_name=source.original_name,
        )
    except Exception as exc:
        pdf_conversion_error = str(exc)[:2000]
        add_event(
            db,
            job,
            "DOCX_PDF_CONVERSION_FAILED",
            "word-first PDF conversion skipped, DOCX delivery remains available",
            level="warning",
            metadata={"error": pdf_conversion_error},
        )
        write_artifact(
            db,
            job,
            "pdf",
            "RENDERING",
            "result.pdf",
            _placeholder_pdf_bytes(),
            "application/pdf",
            job.render_attempt,
            naming_target,
            text,
            naming_name=source.original_name,
        )
    write_artifact(
        db,
        job,
        "html",
        "RENDERING",
        "result.html",
        processed.preview_html.encode("utf-8"),
        "text/html; charset=utf-8",
        job.render_attempt,
        naming_target,
        text,
        naming_name=source.original_name,
    )
    write_artifact(
        db,
        job,
        "docx_processing_report",
        "RENDERING",
        "docx_processing_report.json",
        to_json(processed.report).encode("utf-8"),
        "application/json",
        job.render_attempt,
        target,
        text,
    )
    job.current_artifact_id = docx_artifact.id
    db.commit()

    transition(db, job, JobStatus.RULE_REVIEWING, "rule_review_word_first_docx")
    ensure_not_cancelled(db, job)
    job.review_round += 1
    report = {
        "pass": True,
        "severity": "none",
        "issues": [],
        "standard_profile": "SP_REVIEW_HANDOUT_STUDENT_MATH_DEFAULT",
        "render_mode": "word_first_docx",
        "docx_processing": processed.report,
        "pdf_conversion_error": pdf_conversion_error,
    }
    write_artifact(
        db,
        job,
        "rule_review_report",
        "RULE_REVIEWING",
        "rule_review_report.json",
        to_json(report).encode("utf-8"),
        "application/json",
        job.render_attempt,
        target,
        text,
    )
    job.status = JobStatus.HUMAN_REVIEW_REQUIRED
    job.user_visible_message = "DOCX processed, waiting for human approval"
    job.next_action = "human_approve"
    job.finished_at = utcnow()
    add_event(db, job, "HUMAN_REVIEW_REQUIRED", "word-first DOCX render passed, waiting human approval")
    create_notification(db, job, "JOB_HUMAN_REVIEW_REQUIRED")
    db.commit()


# 主工作流入口。
def run_job(job_id: str) -> None:
    """执行单个排队任务的端到端工作流。"""
    db = SessionLocal()
    try:
        job = db.get(MaterialJob, job_id)
        if not job or job.status in TERMINAL_STATUSES:
            return

        target = from_json(job.target_json, {})
        transition(db, job, JobStatus.PARSING, "input_qualification")
        ensure_not_cancelled(db, job)

        job_file = job.files[0]
        source = db.get(FileRecord, job_file.file_id)
        source_path = Path(source.storage_path)
        text, meta = extract_text(source_path, source.file_type)
        input_quality = qualify_input(source.file_type, source.size_bytes, text, meta)

        fidelity_source_type = source.file_type if source.file_type in {"pdf", "docx"} else None
        if fidelity_source_type and input_quality["decision"] == "needs_human_review":
            input_quality["decision"] = "partially_processable"
            input_quality["reasons"] = list(dict.fromkeys(input_quality["reasons"] + ["fidelity_render_available"]))

        input_quality.update({"job_id": job.id, "source_file_id": source.id})
        write_artifact(
            db,
            job,
            "input_quality",
            "PARSING",
            "input_quality.json",
            to_json(input_quality).encode("utf-8"),
            "application/json",
            1,
            target,
            text,
        )

        parsed_questions = parse_questions(text)
        parsed = {"text_preview": text[:2000], "questions": parsed_questions, "risk": input_quality}
        if input_quality["decision"] == "needs_human_review":
            write_artifact(
                db,
                job,
                "parsed_content",
                "PARSING",
                "parsed_content.json",
                to_json(parsed).encode("utf-8"),
                "application/json",
                1,
                target,
                text,
            )
            job.status = JobStatus.HUMAN_REVIEW_REQUIRED
            job.user_visible_message = "input quality requires human review"
            job.next_action = "review_or_reupload"
            job.finished_at = utcnow()
            add_event(db, job, "HUMAN_REVIEW_REQUIRED", "input blocked, human review needed")
            create_notification(db, job, "JOB_HUMAN_REVIEW_REQUIRED")
            db.commit()
            return

        parsed_content = {"text": text[:20000], "questions": parsed_questions, "input_quality": input_quality}
        write_artifact(
            db,
            job,
            "parsed_content",
            "PARSING",
            "parsed_content.json",
            to_json(parsed_content).encode("utf-8"),
            "application/json",
            1,
            target,
            text,
        )

        transition(db, job, JobStatus.PLANNING, "mock_material_plan")
        ensure_not_cancelled(db, job)

        plan_result = _build_material_plan(source, target, text, input_quality, parsed_questions)
        add_event(
            db,
            job,
            plan_result["event_type"],
            plan_result["event_message"],
            level=plan_result["event_level"],
            metadata=plan_result["event_metadata"],
        )
        material_plan = plan_result["plan"]
        write_artifact(
            db,
            job,
            "material_plan",
            "PLANNING",
            "material_plan.json",
            to_json(material_plan).encode("utf-8"),
            "application/json",
            1,
            target,
            text,
        )

        transition(db, job, JobStatus.NORMALIZING, "normalize_material_spec")
        ensure_not_cancelled(db, job)

        fidelity_pdf_path: Path | None = source_path if fidelity_source_type == "pdf" else None

        blank_plan = {"page_numbers": [], "pages": []}
        if fidelity_pdf_path is not None and target.get("version", "student") == "student":
            try:
                blank_plan = build_student_blank_plan(fidelity_pdf_path)
                if blank_plan["page_numbers"]:
                    add_event(
                        db,
                        job,
                        "STUDENT_BLANK_PLAN_READY",
                        "student blank plan prepared",
                        metadata={"page_numbers": blank_plan["page_numbers"]},
                    )
            except Exception as exc:
                add_event(
                    db,
                    job,
                    "STUDENT_BLANK_PLAN_FAILED",
                    "student blank plan fallback to no extra blank pages",
                    level="warning",
                    metadata={"error": str(exc)[:500]},
                )

        material_spec = {
            "material_type": target.get("material_type", "review_handout"),
            "version": target.get("version", "student"),
            "subject": target.get("subject", "math"),
            "grade": target.get("grade"),
            "title": target.get("title") or material_plan["title"],
            "sections": material_plan["sections"],
            "questions": material_plan["questions"],
            "render_mode": "word_first_docx" if source.file_type == "docx" else ("fidelity_pdf" if fidelity_pdf_path else material_plan.get("render_mode", "template")),
            "source_type": source.file_type,
            "blank_page_numbers": blank_plan["page_numbers"],
            "blank_page_plan": blank_plan["pages"],
        }
        write_artifact(
            db,
            job,
            "material_spec",
            "NORMALIZING",
            "material_spec.json",
            to_json(material_spec).encode("utf-8"),
            "application/json",
            1,
            target,
            text,
        )

        if source.file_type == "docx":
            _finish_word_first_docx(db, job, source, source_path, target, text, material_spec)
            return

        for _ in range(job.max_auto_fix_count + 1):
            render_step = "render_pdf_fidelity" if material_spec.get("render_mode") == "fidelity_pdf" else "render_html"
            transition(db, job, JobStatus.RENDERING, render_step)
            ensure_not_cancelled(db, job)
            job.render_attempt += 1
            naming_target = {**target}
            naming = generate_external_name(source.original_name, naming_target, text, ".html")

            if material_spec.get("render_mode") == "fidelity_pdf" and fidelity_pdf_path is not None:
                try:
                    html = render_pdf_fidelity_html(fidelity_pdf_path, material_spec, naming["display_name"])
                except Exception as exc:
                    add_event(
                        db,
                        job,
                        "FIDELITY_RENDER_FAILED",
                        "fidelity render failed, downgraded to template fallback",
                        level="error",
                        metadata={"error": str(exc)[:500]},
                    )
                    material_spec["render_mode"] = "fallback_template"
                    html = render_html(material_spec, naming["display_name"])
            else:
                html = render_html(material_spec, naming["display_name"])

            result = write_artifact(
                db,
                job,
                "html",
                "RENDERING",
                "result.html",
                html.encode("utf-8"),
                "text/html; charset=utf-8",
                job.render_attempt,
                naming_target,
                text,
                naming_name=source.original_name,
            )
            job.current_artifact_id = result.id
            db.commit()

            transition(db, job, JobStatus.RULE_REVIEWING, "rule_review")
            ensure_not_cancelled(db, job)
            job.review_round += 1
            names = [artifact.name for artifact in job.artifacts] + ["rule_review_report.json", "visual_review_report.json"]
            report = review_html(html, material_spec, names)
            if material_spec.get("render_mode") == "fidelity_pdf":
                visual_report = review_fidelity_html_visual(html, material_spec)
                write_artifact(
                    db,
                    job,
                    "visual_review_report",
                    "RULE_REVIEWING",
                    "visual_review_report.json",
                    to_json(visual_report).encode("utf-8"),
                    "application/json",
                    job.render_attempt,
                    target,
                    text,
                )
                report["issues"].extend(visual_report["issues"])
                high_count = sum(1 for item in report["issues"] if item["severity"] == "high")
                report["pass"] = high_count == 0
                report["severity"] = "high" if high_count else ("warning" if report["issues"] else "none")
                report["visual_review"] = {
                    "pass": visual_report["pass"],
                    "severity": visual_report["severity"],
                }
            write_artifact(
                db,
                job,
                "rule_review_report",
                "RULE_REVIEWING",
                "rule_review_report.json",
                to_json(report).encode("utf-8"),
                "application/json",
                job.render_attempt,
                target,
                text,
            )
            if report["pass"]:
                job.status = JobStatus.HUMAN_REVIEW_REQUIRED
                job.user_visible_message = "render passed, waiting for human approval"
                job.next_action = "human_approve"
                job.finished_at = utcnow()
                add_event(db, job, "HUMAN_REVIEW_REQUIRED", "render passed, waiting human approval")
                create_notification(db, job, "JOB_HUMAN_REVIEW_REQUIRED")
                db.commit()
                return

            fixable = all(issue["code"] in {"RENDER_EMPTY_OUTPUT", "RULE_REQUIRED_ARTIFACT_MISSING"} for issue in report["issues"])
            if fixable and job.auto_fix_count < job.max_auto_fix_count:
                transition(db, job, JobStatus.PATCHING, "basic_auto_fix")
                job.auto_fix_count += 1
                add_event(db, job, "BASIC_AUTO_FIX", "auto-fix attempt")
                db.commit()
                continue

            job.status = JobStatus.FAILED
            job.error_code = report["issues"][0]["code"] if report["issues"] else "RULE_HIGH_SEVERITY_ISSUE"
            job.user_visible_message = "render failed, please reupload or retry"
            job.current_artifact_id = result.id
            job.finished_at = utcnow()
            add_event(db, job, "JOB_FAILED", job.user_visible_message, level="error")
            create_notification(db, job, "JOB_FAILED")
            db.commit()
            return
    finally:
        db.close()


def approve_job(db: Session, job: MaterialJob, reviewer_id: str, final_download_name: str | None = None) -> MaterialJob:
    """把审核通过的产物标记为可下载，并将任务状态推进到 SUCCEEDED。"""
    if job.owner_id != reviewer_id:
        raise ValueError("JOB_PERMISSION_DENIED")
    if job.status != JobStatus.HUMAN_REVIEW_REQUIRED:
        raise ValueError("JOB_NOT_REVIEWABLE")
    current = db.get(Artifact, job.current_artifact_id) if job.current_artifact_id else None
    if not current:
        raise ValueError("ARTIFACT_NOT_FOUND")
    downloadable_types = {"docx", "pdf", "html", "pdf_preview", "preview_pdf", "png_preview"}
    for artifact in job.artifacts:
        if artifact.id == current.id or artifact.artifact_type in downloadable_types:
            artifact.approved_for_download = True
            artifact.retention_until = datetime.now(timezone.utc) + timedelta(days=30)
    if final_download_name:
        current.download_name = final_download_name
        current.display_name = final_download_name
    job.latest_successful_artifact_id = current.id
    job.status = JobStatus.SUCCEEDED
    job.user_visible_message = "material approved and available for download"
    job.next_action = "download"
    job.finished_at = utcnow()
    add_event(db, job, "JOB_APPROVED", "material approved", to_status=JobStatus.SUCCEEDED.value)
    create_notification(db, job, "JOB_SUCCEEDED")
    db.commit()
    db.refresh(job)
    return job
