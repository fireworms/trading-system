# Trading System

KIS(한국투자증권) API + Gemini AI 기반 자동매매 시스템

## 기술 스택

| 영역 | 기술 |
|---|---|
| Backend | Python 3.11+ / FastAPI |
| DB | PostgreSQL + SQLAlchemy + Alembic |
| AI | Gemini API (google-generativeai) |
| 증권 | KIS OpenAPI (httpx 네이티브) |
| 스케줄러 | APScheduler |
| Frontend | Next.js 16 + Tailwind CSS |
| 알림 | Telegram Bot API |

---

## 미니PC 마이그레이션 순서

### 1. 사전 준비

미니PC에 아래 소프트웨어를 설치한다.

```bash
# Python 3.11+
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip

# PostgreSQL
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql && sudo systemctl start postgresql

# Node.js 20+ (프론트엔드)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Git
sudo apt install -y git
```

### 2. 코드 받기

```bash
# GitHub 사용 시
git clone https://github.com/<username>/trading_system.git
cd trading_system

# GitHub 없이 직접 복사 시
# scp 또는 USB로 프로젝트 폴더 복사 후 이동
```

### 3. Python 가상환경 + 패키지 설치

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. PostgreSQL DB 생성

```bash
sudo -u postgres psql -c "CREATE USER trading_user WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE trading_db OWNER trading_user;"
```

### 5. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 값을 채운다.

```env
DATABASE_URL=postgresql://trading_user:yourpassword@localhost/trading_db
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
GEMINI_API_KEY=<Gemini API 키>
TELEGRAM_BOT_TOKEN=<텔레그램 봇 토큰>
```

> KIS API 키는 `.env`에 넣지 않는다 → 6단계에서 DB에 등록

### 6. DB 마이그레이션

```bash
alembic upgrade head
```

### 7. 초기 데이터 seeding

```bash
# 프롬프트 버전 등록 (v1.0)
python -m scripts.seed_prompt_versions

# KIS API 키를 DB에 등록 (실행 전 .env에 KIS 키 임시 입력 필요)
# .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 임시 작성 후:
python -m scripts.seed_broker_account
# 등록 완료 후 .env에서 KIS 키 삭제 (주석 처리)
```

### 8. 관리자 계정 생성

```bash
# 서버 실행 후 API로 생성
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@example.com","password":"<비밀번호>","role":"SUPER_ADMIN"}'
```

### 9. 텔레그램 chat_id 등록

```bash
# 로그인 → 토큰 획득
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/users/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<비밀번호>"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# chat_id 등록
curl -X PATCH http://localhost:8000/api/v1/users/me/telegram \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"telegram_chat_id":"<본인 chat_id>"}'
```

### 10. 서버 실행

```bash
# 백엔드 (포트 8000)
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 프론트엔드 (포트 3000)
cd frontend
npm install
npm run build
npm start
```

### 11. 동작 확인

```bash
# 헬스체크
curl http://localhost:8000/health

# KIS API 연동 테스트
source .venv/bin/activate
python -m tests.test_kis_connection
```

---

## 서버 상시 실행 (systemd)

```bash
# /etc/systemd/system/trading.service
[Unit]
Description=Trading System API
After=network.target postgresql.service

[Service]
User=<username>
WorkingDirectory=/path/to/trading_system
ExecStart=/path/to/trading_system/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading
sudo systemctl start trading
```

---

## 주요 디렉토리 구조

```
trading_system/
├── app/
│   ├── api/          # FastAPI 라우터
│   ├── core/         # 설정, DB, 보안
│   ├── models/       # SQLAlchemy 모델
│   ├── schemas/      # Pydantic 스키마
│   └── services/     # KIS, Gemini, Telegram, Trading
├── frontend/         # Next.js 대시보드
├── migrations/       # Alembic 마이그레이션
├── scripts/          # 초기 데이터 seeding
├── tests/            # 연동 테스트 스크립트
├── .env.example      # 환경변수 템플릿
└── requirements.txt
```

---

## GitHub 연결 (나중에)

```bash
git remote add origin https://github.com/<username>/trading_system.git
git push -u origin main
```
