"""
텔레그램 알림 서비스.
- TELEGRAM_BOT_TOKEN: .env 시스템 공용 봇
- chat_id: 각 유저의 users.telegram_chat_id (메서드 파라미터로 전달)
- BOT_TOKEN 미설정 시 무음 처리
"""
import logging
from decimal import Decimal
from datetime import date

import requests

logger = logging.getLogger(__name__)

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

_STATUS_EMOJI = {
    "TARGET_HIT":  "🎯",
    "STOP_LOSS":   "🛑",
    "EXPIRED":     "⏰",
    "MANUAL_EXIT": "🔧",
}


class TelegramNotifier:
    """봇 토큰 보유. 모든 send 메서드에 chat_id를 명시적으로 전달한다."""

    def __init__(self, token: str):
        self._token = token
        self._url   = _SEND_URL.format(token=token)

    def _send(self, chat_id: str, text: str) -> None:
        try:
            resp = requests.post(
                self._url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if not resp.ok:
                logger.warning("Telegram send failed (chat_id=%s): %s %s",
                               chat_id, resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Telegram error (chat_id=%s): %s", chat_id, e)

    # ------------------------------------------------------------------ #
    # 종목 추천 완료
    # ------------------------------------------------------------------ #

    def notify_recommendations(
        self,
        chat_id: str,
        strategy_name: str,
        run_date: date,
        market_theme: str,
        picks: list[dict],
    ) -> None:
        lines = [
            f"📊 <b>[{strategy_name}] AI 종목 추천</b>",
            f"📅 {run_date}",
            "─" * 24,
        ]
        for p in picks:
            prob   = p.get("ai_probability", "")
            target = f"{int(p['target_price']):,}" if p.get("target_price") else "-"
            stop   = f"{int(p['stop_loss_price']):,}" if p.get("stop_loss_price") else "-"
            reason = (p.get("ai_reason") or "")[:60]
            lines += [
                f"\n<b>{p.get('rank','')}. {p.get('stock_name','')} ({p.get('stock_code','')})</b>",
                f"   확률 {prob}% | 목표가 {target} | 손절가 {stop}",
                f"   <i>{reason}</i>",
            ]
        lines += ["\n" + "─" * 24, f"📈 테마: {market_theme[:60]}"]
        self._send(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------ #
    # 포지션 종료
    # ------------------------------------------------------------------ #

    def notify_position_closed(
        self,
        chat_id: str,
        stock_code: str,
        stock_name: str,
        status: str,
        entry_price: Decimal,
        exit_price: Decimal,
        pnl_pct: Decimal,
        strategy_name: str,
    ) -> None:
        emoji = _STATUS_EMOJI.get(status, "📌")
        label = {"TARGET_HIT": "목표가 도달", "STOP_LOSS": "손절",
                 "EXPIRED": "보유기간 만료", "MANUAL_EXIT": "수동 종료"}.get(status, status)
        sign  = "+" if pnl_pct >= 0 else ""
        text  = (
            f"{emoji} <b>{label}: {stock_name} ({stock_code})</b>\n"
            f"전략: {strategy_name}\n"
            f"매수가: {int(entry_price):,} → 매도가: {int(exit_price):,}\n"
            f"수익률: <b>{sign}{float(pnl_pct):.2f}%</b>"
        )
        self._send(chat_id, text)

    # ------------------------------------------------------------------ #
    # 에러
    # ------------------------------------------------------------------ #

    def notify_error(self, chat_id: str, title: str, detail: str) -> None:
        self._send(chat_id, f"🚨 <b>[ERROR] {title}</b>\n<code>{detail[:800]}</code>")


# ------------------------------------------------------------------ #
# 싱글턴 팩토리
# ------------------------------------------------------------------ #

_notifier: TelegramNotifier | None = None


def get_notifier() -> TelegramNotifier | None:
    """BOT_TOKEN 설정 시 TelegramNotifier 반환, 미설정 시 None."""
    global _notifier
    if _notifier is not None:
        return _notifier

    from app.core.config import get_settings
    token = get_settings().telegram_bot_token
    if token:
        _notifier = TelegramNotifier(token)
    return _notifier


# ------------------------------------------------------------------ #
# DB 헬퍼
# ------------------------------------------------------------------ #

def get_admin_chat_ids(db) -> list[str]:
    """ADMIN / SUPER_ADMIN 중 telegram_chat_id가 설정된 유저 목록."""
    from sqlalchemy import select
    from app.models.user import User, UserRole
    rows = db.scalars(
        select(User.telegram_chat_id)
        .where(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))
        .where(User.telegram_chat_id.isnot(None))
        .where(User.is_active == True)  # noqa: E712
    ).all()
    return [r for r in rows if r]


def notify_admins_error(title: str, detail: str) -> None:
    """에러를 모든 어드민에게 전송 (내부에서 DB 세션 생성)."""
    notifier = get_notifier()
    if not notifier:
        return
    try:
        from app.core.database import SessionLocal
        with SessionLocal() as db:
            for chat_id in get_admin_chat_ids(db):
                notifier.notify_error(chat_id, title, detail)
    except Exception as e:
        logger.warning("notify_admins_error failed: %s", e)
