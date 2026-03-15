# 內部 LLM（開發階段暫用 DeepSeek，上線前改回內部 endpoint）
LLM_BASE_URL: str = "https://api.deepseek.com/v1"
LLM_API_KEY: str = "MY_KEY"
LLM_MODEL: str = "deepseek-chat"

# Embedding（開發階段用本地 sentence-transformers，上線前設回內部 endpoint）
EMBEDDING_BASE_URL: str | None = None
EMBEDDING_MODEL: str = "BAAI/bge-m3"

# Qdrant（本地部署）
QDRANT_HOST: str = "localhost"
QDRANT_PORT: int = 6333

# 資料庫（唯讀帳號）
DB_DSN: str = "postgresql://user:password@internal-db:5432/fab_db"

# SOP 目錄
SOP_DIR: str = "./sop"

# Vector Search 信心度閾值（上線前用真實問題校準）
CONFIDENCE_THRESHOLD: float = 0.70

# SQL Audit Log
AUDIT_LOG_FILE: str = "./audit.log"
