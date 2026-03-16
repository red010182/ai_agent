# 部署說明

## 系統需求

| 元件 | 最低需求 |
|------|----------|
| Docker | 24.0+ |
| Docker Compose | 2.20+ |
| 可用記憶體 | 4GB（不含 LLM / Embedding 服務） |
| 磁碟空間 | 10GB（含 Qdrant 資料） |

---

## 服務架構

```
                    ┌─────────────┐
用戶瀏覽器  ───────▶│   nginx     │ :80
                    │  (frontend) │
                    └──────┬──────┘
                           │ /api proxy
                    ┌──────▼──────┐
                    │   backend   │ :8080
                    │  (FastAPI)  │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐  ┌──────────┐  ┌────────────┐
         │ Qdrant │  │ 內部 LLM │  │ 內部 DB    │
         │ :6333  │  │ (外部)   │  │ (外部)     │
         └────────┘  └──────────┘  └────────────┘
```

---

## 快速啟動

### 1. 準備環境變數

```bash
cp .env.example .env
# 編輯 .env，填入真實的內部服務 endpoint
```

### 2. Build 前端

```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. 啟動所有服務

```bash
docker compose up -d
```

### 4. 確認服務狀態

```bash
docker compose ps
# 三個服務都應該是 Up 狀態

docker compose logs backend --tail=50
# 確認 Qdrant 連線成功、SOP index 建立完成
```

### 5. 開啟瀏覽器

```
http://your-server-ip:80
```

---

## 環境變數說明（`.env`）

```bash
# LLM
LLM_BASE_URL=http://internal-llm:8000/v1
LLM_API_KEY=dummy
LLM_MODEL=qwen-235b-q8

# Embedding
EMBEDDING_BASE_URL=http://internal-embedding:8001/v1
EMBEDDING_MODEL=BAAI/bge-m3

# Qdrant（docker compose 內部網路，不需修改）
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# 資料庫
DB_DSN=postgresql://user:password@internal-db:5432/fab_db

# Agent
CONFIDENCE_THRESHOLD=0.70
AUDIT_LOG_FILE=/app/logs/audit.log
```

> **注意：** `.env` 不可提交到 Git，確認 `.gitignore` 已包含 `.env`

---

## docker-compose.yml 說明

```yaml
services:
  frontend:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - backend

  backend:
    build: .
    ports:
      - "8080:8080"
    env_file: .env
    volumes:
      - ./sop:/app/sop          # SOP 文件（熱更新不需重建 image）
      - ./logs:/app/logs        # audit log
    depends_on:
      - qdrant

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - ./qdrant_data:/qdrant/storage
```

---

## SOP 文件更新

SOP 文件透過 volume 掛載，**更新 markdown 不需要重建 image**，但需要重建 Qdrant index：

```bash
# 1. 把新的 / 修改的 SOP 放到 sop/ 目錄

# 2. 重建 Qdrant index（不重啟服務）
docker compose exec backend python -m agent.vector_search reindex

# 3. 確認 index 筆數
docker compose exec backend python -m agent.vector_search status
```

---

## 常見問題

**Q: backend 啟動時報 Qdrant 連線失敗**
```bash
# 確認 qdrant 服務是否正常
docker compose logs qdrant
# 確認 QDRANT_HOST 設定為 "qdrant"（docker 內部網路名稱），不是 localhost
```

**Q: Vector Search 都進 fallback 模式，score 很低**
```bash
# 確認 index 是否建立
docker compose exec backend python -m agent.vector_search status
# 若 index 為空，重新建立
docker compose exec backend python -m agent.vector_search reindex
```

**Q: LLM 呼叫 timeout**
```bash
# 確認內部 LLM endpoint 可從 backend container 存取
docker compose exec backend curl http://internal-llm:8000/v1/models
```

**Q: 前端打不開或顯示舊版本**
```bash
# 重新 build 前端
cd frontend && npm run build && cd ..
# nginx 不需要重啟，直接讀 dist/
```

---

## 日誌查看

```bash
# 即時查看所有服務日誌
docker compose logs -f

# 只看 backend
docker compose logs -f backend

# audit log（SQL 執行記錄）
tail -f logs/audit.log
```

---

## 更新版本

```bash
# 拉最新程式碼
git pull

# 重新 build 前端
cd frontend && npm run build && cd ..

# 重建 backend image
docker compose build backend

# 滾動重啟（不中斷 qdrant）
docker compose up -d --no-deps backend frontend
```

---

## 備份

需要備份的資料只有兩樣：

```bash
# 1. Qdrant 向量資料
tar -czf qdrant_backup_$(date +%Y%m%d).tar.gz qdrant_data/

# 2. Audit log
tar -czf logs_backup_$(date +%Y%m%d).tar.gz logs/
```

SOP 文件本身應該在 Git 版控，不需要額外備份。