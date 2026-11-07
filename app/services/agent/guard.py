"""
웹 에이전트 하드 차단 목록 — 승인 버튼과 무관하게 항상 거부.

승인 UI가 1차 방어지만 폰에서 오탭 한 번이면 끝이다. 되돌릴 수 없는 명령은
사람이 "허용"을 눌러도 실행되지 않게 PreToolUse 훅에서 막는다.
(훅은 settings.json 허용 목록으로 자동 승인되는 도구에도 발동 — can_use_tool은 안 그렇다)

차단 기준: 되돌릴 수 없거나(삭제·강제 push·DB 파괴) 에이전트 자신을 끊는 것(trading-agent 재시작).
터미널에서 하면 되는 일이라 웹에서 막아도 기능 손실이 작다.
"""
from __future__ import annotations

import re

_BASH_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)"), "rm -r/-f는 웹에서 금지 — 터미널에서 직접"),
    (re.compile(r"\bgit\s+push\b.*(--force\b|-f\b|--force-with-lease)"), "강제 push 금지 (공개 레포 히스토리 보호)"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|filter-repo|filter-branch)\b"), "작업 트리/히스토리 파괴 명령 금지"),
    (re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.I), "DB 파괴 명령 금지"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+\s*(;|$|\"|')", re.I), "WHERE 없는 DELETE 금지"),
    (re.compile(r"\bdropdb\b|\balembic\s+downgrade\b"), "DB 삭제/다운그레이드 금지"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask|restart|kill)\s+\S*trading-agent"), "에이전트 서비스 자신을 끊는 명령 금지 — 터미널에서"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask)\b"), "서비스 정지/비활성화 금지 (restart/status만 허용)"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"), "서버 전원 명령 금지"),
    (re.compile(r"\b(mkfs|dd\s+if=|chmod\s+-R\s+777|chown\s+-R)\b"), "시스템 파괴 명령 금지"),
    (re.compile(r"(\.env\b|\.credentials\.json|\.kis_token_cache)"), "비밀 파일 접근 금지"),
]

# Read/Edit/Write 등 파일 도구의 경로 차단 — 비밀 파일
_PATH_DENY = re.compile(r"(^|/)(\.env(\.local)?|\.credentials\.json|\.kis_token_cache\.json)$")


def check_tool(tool_name: str, tool_input: dict) -> str | None:
    """차단 사유를 반환, 통과면 None."""
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        for pat, reason in _BASH_DENY:
            if pat.search(cmd):
                return reason
        return None
    for key in ("file_path", "path", "notebook_path"):
        p = tool_input.get(key)
        if isinstance(p, str) and _PATH_DENY.search(p):
            return "비밀 파일 접근 금지"
    return None
