# 설치 / 마이그레이션 가이드

새 호스트(미니PC 등)로 이전 시 순서. 요약 버전은 README의 Quick Start 참조.

## 1. 사전 준비

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

## 2. 코드 받기

```bash
git clone https://github.com/fireworms/trading-system.git
cd trading-system
```

## 3. Python 가상환경 + 패키지 설치

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. PostgreSQL DB 생성

```bash
sudo -u postgres psql -c "CREATE USER trading_user WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE trading_db OWNER trading_user;"
```

## 5. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 값을 채운다.

```env
DATABASE_URL=postgresql://trading_user:yourpassword@localhost/trading_db
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
GEMINI_API_KEY=<Gemini API 키>
TELEGRAM_BOT_TOKEN=<텔레그램 봇 토큰>
DART_API_KEY=<선택 — 관심종목 공시 어댑터>
NAVER_CLIENT_ID=<선택 — 관심종목 뉴스 어댑터>
NAVER_CLIENT_SECRET=<선택>
```

> KIS API 키는 `.env`에 넣지 않는다 → 7단계에서 DB에 Fernet 암호화로 등록

## 6. DB 마이그레이션

```bash
alembic upgrade head
```

## 7. 초기 데이터 seeding

```bash
# 프롬프트 버전 등록 (v1.0)
python -m scripts.seed_prompt_versions

# KIS API 키를 DB에 등록 (실행 전 .env에 KIS 키 임시 입력 필요)
# .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 임시 작성 후:
python -m scripts.seed_broker_account
# 등록 완료 후 .env에서 KIS 키 삭제 (주석 처리)
```

## 8. 관리자 계정 생성

```bash
# 서버 실행 후 API로 생성
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@example.com","password":"<비밀번호>","role":"SUPER_ADMIN"}'
```

## 9. 텔레그램 chat_id 등록

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

## 10. 서버 실행

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

## 11. 동작 확인

```bash
# 헬스체크
curl http://localhost:8000/health

# KIS API 연동 테스트
source .venv/bin/activate
python -m tests.test_kis_connection
```

## 서버 상시 실행 (systemd)

```bash
# /etc/systemd/system/trading-backend.service
[Unit]
Description=Trading System Backend (uvicorn)
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
sudo systemctl enable trading-backend
sudo systemctl start trading-backend
```

프론트엔드도 동일 패턴으로 `trading-frontend.service` 등록 (`npm run dev` 또는 `npm start`).
