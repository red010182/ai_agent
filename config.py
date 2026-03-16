import os
from dotenv import load_dotenv

load_dotenv()

# 內部 LLM
LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY: str = os.environ["LLM_API_KEY"]
LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-chat")

# Embedding（None → 使用本地 sentence-transformers）
EMBEDDING_BASE_URL: str | None = os.environ.get("EMBEDDING_BASE_URL")
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")

# Qdrant
QDRANT_HOST: str = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT: int = int(os.environ.get("QDRANT_PORT", "6333"))

# 資料庫（唯讀帳號）
DB_DSN: str = os.environ.get("DB_DSN", "postgresql://user:password@internal-db:5432/fab_db")

# SOP 目錄
SOP_DIR: str = "./sop"

# Vector Search 信心度閾值
CONFIDENCE_THRESHOLD: float = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.70"))

# SQL Audit Log
AUDIT_LOG_FILE: str = os.environ.get("AUDIT_LOG_FILE", "./audit.log")
