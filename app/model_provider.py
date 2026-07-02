"""运行时模型配置和 Doubao 请求辅助函数，用于可选的规划步骤。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

import httpx

from .database import DATA_DIR


DOUBAO_BASE_URL = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "doubao-seed-2-0-pro-260215")
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
RUNTIME_MODEL_CONFIG_PATH = DATA_DIR / "runtime_model_config.json"


def _load_runtime_model_config() -> dict[str, Any]:
    """加载运行时 模型 配置。"""
    if not RUNTIME_MODEL_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(RUNTIME_MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_runtime_model_config(config: dict[str, Any]) -> None:
    """保存运行时 模型 配置。"""
    RUNTIME_MODEL_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolved_model_config() -> dict[str, Any]:
    """处理resolved 模型 配置。"""
    runtime = _load_runtime_model_config()
    api_key = runtime.get("api_key") or DOUBAO_API_KEY
    model = runtime.get("model") or DOUBAO_MODEL
    base_url = (runtime.get("base_url") or DOUBAO_BASE_URL).rstrip("/")
    enabled_override = runtime.get("enabled")
    enabled = bool(api_key) if enabled_override is None else bool(enabled_override and api_key)
    return {
        "provider": "doubao",
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "enabled": enabled,
        "runtime_override": bool(runtime),
    }


def update_runtime_model_config(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """保存可选 Doubao 规划器的运行时覆盖配置。"""
    config = _load_runtime_model_config()
    if api_key is not None:
        config["api_key"] = api_key.strip()
    if model is not None:
        config["model"] = model.strip()
    if base_url is not None:
        config["base_url"] = base_url.strip().rstrip("/")
    if enabled is not None:
        config["enabled"] = bool(enabled)
    _save_runtime_model_config(config)
    return model_config_status()


def model_config_status() -> dict[str, Any]:
    """返回当前生效的模型配置，同时不暴露密钥。"""
    resolved = _resolved_model_config()
    return {
        "provider": resolved["provider"],
        "base_url": resolved["base_url"],
        "model": resolved["model"],
        "enabled": resolved["enabled"],
        "has_api_key": bool(resolved["api_key"]),
        "runtime_override": resolved["runtime_override"],
    }


def _extract_json(text: str) -> dict[str, Any]:
    """提取JSON。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _powershell_escape(value: str) -> str:
    """处理powershell escape。"""
    return value.replace("'", "''")


def _post_chat_via_powershell(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """处理post chat via powershell。"""
    safe_url = _powershell_escape(url)
    safe_auth = _powershell_escape(f"Bearer {api_key}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        payload_path = handle.name
    try:
        safe_path = _powershell_escape(payload_path)
        script = (
            "$ErrorActionPreference='Stop'; "
            "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            f"$headers=@{{ Authorization='{safe_auth}' }}; "
            f"$body=[System.IO.File]::ReadAllText('{safe_path}', [System.Text.Encoding]::UTF8); "
            f"$resp=Invoke-WebRequest -Uri '{safe_url}' -Method POST -Headers $headers "
            "-ContentType 'application/json' -Body $body -UseBasicParsing; "
            "$resp.Content"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "doubao_request_failed")
        return json.loads(completed.stdout)
    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass


def _post_chat_via_curl(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """处理post chat via curl。"""
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl_not_available")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        payload_path = handle.name
    try:
        completed = subprocess.run(
            [
                curl_path,
                "--silent",
                "--show-error",
                "--fail",
                "--connect-timeout",
                "20",
                "--max-time",
                "120",
                "-X",
                "POST",
                url,
                "-H",
                f"Authorization: Bearer {api_key}",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                f"@{payload_path}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "doubao_request_failed")
        return json.loads(completed.stdout)
    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass


def _post_chat_via_httpx(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """处理post chat via httpx。"""
    with httpx.Client(timeout=120, http2=False, trust_env=False) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def _post_chat(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """处理post chat。"""
    primary_error = None
    try:
        return _post_chat_via_httpx(url, api_key, payload)
    except Exception as exc:
        primary_error = exc
    try:
        return _post_chat_via_curl(url, api_key, payload)
    except Exception as exc:
        if primary_error is None:
            primary_error = exc
    try:
        return _post_chat_via_powershell(url, api_key, payload)
    except Exception as exc:
        raise RuntimeError(f"httpx_failed: {primary_error}; curl_failed_or_skipped: {exc}") from exc


def plan_with_doubao(parsed_content: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    """向 Doubao 请求轻量材料规划，并把返回结构规范化。"""
    config = _resolved_model_config()
    if not config["enabled"]:
        return None

    question_count = len(parsed_content.get("questions", []))
    question_type_counts: dict[str, int] = {}
    for question in parsed_content.get("questions", []):
        qtype = str(question.get("type") or "unknown")
        question_type_counts[qtype] = question_type_counts.get(qtype, 0) + 1

    title_hint = target.get("title") or "Material"
    if any(ord(char) > 127 for char in title_hint):
        title_hint = "Material"

    prompt = "\n".join(
        [
            "Return one JSON object only.",
            "Task: create a lightweight curriculum material plan.",
            f"Version: {target.get('version', 'student')}",
            f"Subject: {target.get('subject', 'math')}",
            f"Material type: {target.get('material_type', 'review_handout')}",
            f"Question count: {question_count}",
            f"Question type counts: {json.dumps(question_type_counts, ensure_ascii=True)}",
            f"Preferred title: {title_hint}",
            'Required keys: "title", "sections", "confidence", "questions".',
            'sections must be exactly ["learning_goals","knowledge_points","examples","practice"].',
            'questions must be an empty array.',
            "confidence must be a number between 0 and 1.",
            "Do not include answers, analysis, markdown, html, or explanations.",
        ]
    )
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": "You are a controlled curriculum-material planner. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 320,
        "response_format": {"type": "json_object"},
    }
    response_json = _post_chat(f"{config['base_url']}/chat/completions", config["api_key"], payload)
    content = response_json["choices"][0]["message"]["content"]
    result = _extract_json(content)
    if not isinstance(result, dict):
        raise ValueError("PLAN_SCHEMA_INVALID")
    if not isinstance(result.get("questions"), list):
        result["questions"] = []
    if not isinstance(result.get("title"), str) or not result.get("title", "").strip():
        result["title"] = target.get("title") or "未命名资料"
    result.setdefault("sections", ["learning_goals", "knowledge_points", "examples", "practice"])
    result.setdefault("confidence", 0.75)
    return result
