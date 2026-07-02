# Database and worker settings.
$env:APP_DATABASE_URL = "postgresql+psycopg://postgres:your-password@127.0.0.1:5432/misc_agent"
$env:APP_WORKER_COUNT = "2"
$env:APP_WORD_MAX_CONCURRENCY = "1"

# Shared-access settings for a team-visible deployment.
$env:APP_REQUIRE_ACCESS_KEY = "true"
$env:APP_ACCESS_KEY = "replace-with-a-shared-access-key"
$env:APP_ALLOW_DEMO_USER = "false"

# Runtime storage locations. These stay outside the repository on purpose.
$env:APP_DATA_DIR = "D:\misc_agent_runtime\data"
$env:APP_UPLOAD_DIR = "D:\misc_agent_runtime\uploads"
$env:APP_ARTIFACT_DIR = "D:\misc_agent_runtime\artifacts"
$env:APP_CONVERTED_DIR = "D:\misc_agent_runtime\converted"
$env:APP_TMP_DIR = "D:\misc_agent_runtime\tmp"

# Queue and recovery limits.
$env:APP_MAX_PENDING_JOBS_TOTAL = "200"
$env:APP_MAX_PENDING_JOBS_PER_USER = "30"
$env:APP_MAX_BATCH_FILES = "20"
$env:APP_RECOVER_INCOMPLETE_JOBS = "true"
$env:APP_RECOVER_MAX_AGE_HOURS = "12"
