"""
에이전트 자기 코드 변경 감지 — 재시작 필요 배너용 (자동 재시작은 하지 않는다).

웹 에이전트가 자기 코드(agent_main.py, services/agent/*)를 고쳐도 이 프로세스는 기동 시점의
코드로 계속 돈다. 지연 import가 섞이면 한 프로세스 안에 신구 코드가 공존한다(2026-09-08 청산
23,990회 실패와 같은 구조). 그래서 기동 시점 mtime을 스냅샷으로 두고, 바뀐 파일이 있으면
UI에 "터미널에서 재시작 필요"를 띄운다.

자동 셀프 재시작(exam-app에 구현됨)은 미채택 — 폰에서 에이전트 코드를 고치는 일이 드물고,
백그라운드 작업(백테스트·KRX 적재)을 잘라먹을 위험 대비 이득이 작다. 빈도 판단용으로
감지 이력을 CHANGE_LOG에 남긴다: 웹 턴 중 감지가 잦아지면 그때 자동 재시작을 검토한다.

공용 모듈(app/core/*, app/api/deps.py)은 감시하지 않는다 — 매매 작업 중 자주 바뀌어
배너가 상시 켜지면 신호가 아니다.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHANGE_LOG = Path.home() / ".cache" / "trading-agent" / "code_changes.log"


def _watched() -> dict[str, float]:
    files = [PROJECT_ROOT / "app" / "agent_main.py", PROJECT_ROOT / "requirements.txt",
             *sorted((PROJECT_ROOT / "app" / "services" / "agent").glob("*.py"))]
    out: dict[str, float] = {}
    for f in files:
        try:
            out[str(f.relative_to(PROJECT_ROOT))] = f.stat().st_mtime
        except OSError:
            pass
    return out


_BOOT = _watched()          # import 시점 = 프로세스 기동 시점
_logged: frozenset[str] = frozenset()


def changed_files() -> list[str]:
    """기동 이후 바뀌거나 새로 생기거나 사라진 감시 파일 (상대 경로)."""
    now = _watched()
    return sorted({k for k in now.keys() | _BOOT.keys() if now.get(k) != _BOOT.get(k)})


def record_if_new(files: list[str], session_id: str | None) -> None:
    """새로 감지된 변경 집합만 이력에 한 줄 남긴다 (턴마다 중복 기록 안 함)."""
    global _logged
    key = frozenset(files)
    if not files or key == _logged:
        return
    _logged = key
    try:
        CHANGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CHANGE_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{session_id or '-'}\t{','.join(files)}\n")
    except OSError as e:
        logger.warning("code change log write failed: %s", e)
