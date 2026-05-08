#!/bin/bash
set -e

echo "=== Trading System Setup ==="

# 가상환경 생성
python3 -m venv .venv
source .venv/bin/activate

# pip 업그레이드 및 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt

# DB 마이그레이션
alembic revision --autogenerate -m "initial schema"
alembic upgrade head

echo "=== Setup complete ==="
echo "Run: source .venv/bin/activate && uvicorn app.main:app --reload"
