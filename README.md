# Async Material Generation MVP-0

This is the first engineering slice for the controlled asynchronous material generation pipeline.

The current version supports:

- Upload DOCX, PDF, and ZIP source files.
- Create an async material job.
- Extract basic text from DOCX/PDF.
- Generate `input_quality.json`, `parsed_content.json`, `material_plan.json`, `material_spec.json`, `result.html`, and `rule_review_report.json`.
- Generate human-readable externalized filenames from file name, target fields, and extracted content.
- Put generated results into human review by default.
- Approve a reviewed job before downloading final result artifacts.
- Retry basic machine-review failures up to 3 times.
- Keep failed artifacts for 7 days and successful artifacts for 30 days.

## DOCX routing

The Word-first chain now resolves incoming DOCX files into explicit processing modes and records both the chosen mode and matched trigger words in `docx_processing_report.json`.

- `teacher_keep`: preserve teacher content, only clean branding and rebuild header/footer visuals.
- `student_refine`: for original-paper or exam-style student files; adds fill-in spacing, question-block answer space, and pagination tuning without stripping content.
- `teacher_to_student_tail`: for parsed teacher files whose answers are clearly appended in a later answer section; strips the tail answer section, then applies student spacing and pagination.
- `teacher_to_student_inline`: for parsed files where answers and analysis are mixed inline through the paper; strips the inline answer/analysis paragraphs question by question, then applies student spacing and pagination.
- `notes_keep`: reserved only for explicitly keeping handout-style content unchanged.

Student-version default for handout-style sources is no longer `notes_keep`: they now still follow the same student formatting track as math, including spacing and pagination, while preserving whatever content is not identified as answer/analysis.

Current trigger word groups:

- `student_source`: `原卷版` `考试版` `学生版` `原卷`
- `teacher_source`: `解析版` `教师版` `参考答案` `全解全析` `答案版`
- `tail_answer_section`: `参考答案与试题解析` `参考答案` `答案与解析` `试题解析` `参考解析`
- `inline_parse_markers`: `【答案】` `【解析】` `【详解】` `【点睛】` `【解答】` `精品解析`
- `notes_handout`: `讲练测` `易错` `必记` `知识梳理` `方法总结` `考点` `专题` `学案` `导学案` `复习提纲`
- `exam_paper`: `真题` `试卷` `期中` `期末` `月考` `联考` `模拟` `冲刺卷`

## Run

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
$env:APP_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/misc_agent"
$env:APP_WORKER_COUNT="2"
$env:APP_REQUIRE_ACCESS_KEY="true"
$env:APP_ACCESS_KEY="replace-with-your-shared-access-key"
uvicorn app.main:app --reload --port 8000
```

If `APP_DATABASE_URL` is left unset, the app falls back to local SQLite for single-machine debugging. When running on SQLite, the worker pool automatically clamps to `1` to avoid lock storms.

## Runtime settings

- `APP_DATABASE_URL`: PostgreSQL connection string, recommended for any multi-user run.
- `APP_WORKER_COUNT`: total background workers. Start with `2` on PostgreSQL.
- `APP_WORD_MAX_CONCURRENCY`: Word-to-PDF conversion concurrency. Keep at `1` unless dedicated workers are very stable.
- `APP_REQUIRE_ACCESS_KEY`: when `true`, every API request must include `X-Access-Key`.
- `APP_ACCESS_KEY`: shared access key used with `X-Access-Key`.
- `APP_ALLOW_DEMO_USER`: keep `false` for shared use.
- `APP_DATA_DIR`, `APP_UPLOAD_DIR`, `APP_ARTIFACT_DIR`, `APP_CONVERTED_DIR`, `APP_TMP_DIR`: runtime storage directories.
- `APP_MAX_PENDING_JOBS_TOTAL`, `APP_MAX_PENDING_JOBS_PER_USER`, `APP_MAX_BATCH_FILES`: queue protection limits.
- `APP_RECOVER_INCOMPLETE_JOBS`, `APP_RECOVER_MAX_AGE_HOURS`: restart recovery window for recent unfinished jobs only.

## Doubao / Volcengine Ark

The model planner can use Volcengine Ark's OpenAI-compatible chat completions API when environment variables are present.

```powershell
$env:DOUBAO_API_KEY="your-secret-api-key"
$env:DOUBAO_MODEL="doubao-seed-2-0-pro-260215"
$env:DOUBAO_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
uvicorn app.main:app --reload --port 8000
```

Without `DOUBAO_API_KEY`, the workflow automatically falls back to the deterministic mock planner.

## Main endpoints

- `POST /api/files`
- `POST /api/material-jobs`
- `GET /api/material-jobs/{job_id}`
- `GET /api/material-jobs/{job_id}/artifacts`
- `POST /api/material-jobs/{job_id}/review/approve`
- `POST /api/material-jobs/{job_id}/cancel`
- `GET /api/artifacts/{artifact_id}/download`
- `POST /api/admin/cleanup-expired-artifacts`

Use `X-User-Id` for user isolation. In shared deployment, also enable `APP_REQUIRE_ACCESS_KEY` and send `X-Access-Key` with each request.

## 代码结构

- `app/main.py`：FastAPI 接口层，负责上传、鉴权、任务增删查改、产物预览/下载，并把后台工作交给 `workflow.py`。
- `app/workflow.py`：后台队列和工作线程调度核心，负责状态流转、产物生成、审核闸口和批准流程。
- `app/docx_processor.py`：Word-first DOCX 处理链路，负责判定师生版模式、清理来源品牌与答案、重建页眉，并补学生版留白或讲义标签。
- `app/docx_converter.py`：把生成后的 DOCX 转成 PDF，优先走 Word COM，失败时回退到渲染器。
- `app/parser.py`：从 DOCX/PDF 中抽取文本和题目结构，供后续判断能否安全自动处理。
- `app/naming.py`：根据文件名线索、目标字段和抽取文本生成面向用户的产物名称。
- `app/templates.py`：负责模板版输出和 PDF 保真预览输出的 HTML 渲染。
- `app/reviewer.py` 和 `app/visual_review.py`：任务进入人工批准前使用的规则审核和视觉审核辅助函数。
- `app/storage.py`、`app/database.py`、`app/models.py`、`app/schemas.py`、`app/utils.py`、`app/config.py`：持久化、存储、ID、JSON 辅助函数和运行时配置等基础设施。

## 仓库范围

`data/`、`tmp/`、`backups/`、本地虚拟环境、缓存和本地数据库等运行时目录会被 Git 忽略，这样仓库能聚焦在代码和说明文档上，而不是生成产物。
