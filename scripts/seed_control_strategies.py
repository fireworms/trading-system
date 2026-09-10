"""
대조군 전략 3개 시드 + [TEST] 실적 카탈리스트 비활성화 (2026-09-03).

배경: 랜덤 벤치마크의 청산 규칙 버그를 고치고 나니 AI 우위가 사실상 0으로 수렴했다
(KOSPI 대형주 스윙 AI -0.29% vs 랜덤 -0.20%). 랜덤은 너무 약한 대조군이었으므로,
"AI가 단순 규칙보다 나은가"를 직접 겨냥하는 대조군이 필요하다.

  ① [TEST] 규칙 모멘텀 (AI 대조군)
     KOSPI 대형주 스윙의 파라미터·유니버스를 **완전히 복제**하고 selection_mode만
     rule_breakout으로 변경 → 변수는 "AI 사용 여부" 하나. Gemini 호출 0회.
     지시서의 '거래대금 상위 100위' 조건은 제외 — KOSPI200 유니버스에선 거의 항상
     통과라 무효 필터이고(전체시장용 유동성 스크린), 조건이 늘면 변수만 늘어난다.

  ② [TEST] KOSPI 대형주 스윙 (주기1일)
     표본 축적 가속용. hold/target/stop은 원본 그대로 두고 run_interval_days만 1일.
     보유기간을 5일로 줄이는 안은 채택하지 않았다 — 왕복 비용이 고정이라 폭을 절반으로
     줄이면 세후 본전 승률이 36%→39%로 올라가고, 5일 안에 +3%는 20일 안에 +6%와
     다른 능력이라 원 전략 검증이 아니라 별개 전략 측정이 된다.

  ③ [TEST] 과매도 반등 (규칙)
     기존 전략이 전부 모멘텀 방향이라 같은 국면에서 동시에 죽는 문제 완화용.
     RSI≤35 / 거래대금 전일 1.5배 — 지시서의 RSI30·2배는 KOSPI200에서 몇 주씩
     0건이라 표본이 안 쌓인다. 손절 3.5%는 3%보다 넓다 (하락 추세 종목은 진입 직후
     추가 하락이 흔해 좁은 손절이 구조적으로 불리). target 7%는 일평균 0.7%/일
     상한(_validate_strategy)에 걸리는 최댓값.

전부 구독(user_strategies) 없이 관찰 모드 — 활성 전략 row만으로 verifier가 채점한다.
멱등: 이미 있으면 건너뛴다.

실행: .venv/bin/python -m scripts.seed_control_strategies
"""
import sys
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.strategy import Strategy
from app.models.user import User

RULE_MOMENTUM = "[TEST] 규칙 모멘텀 (AI 대조군)"
FAST_CYCLE    = "[TEST] KOSPI 대형주 스윙 (주기1일)"
OVERSOLD      = "[TEST] 과매도 반등 (규칙)"
DEACTIVATE    = "[TEST] 실적 카탈리스트"


def _create(db, admin, name: str, **kw) -> Strategy | None:
    if db.query(Strategy).filter(Strategy.name == name).first():
        print(f"  이미 존재 — 건너뜀: {name}")
        return None
    s = Strategy(created_by=admin.user_id if admin else None, name=name, is_active=True, **kw)
    db.add(s)
    db.flush()
    print(f"  생성: {name}")
    print(f"    hold={s.hold_days} target={s.target_pct} stop={s.stop_loss_pct} "
          f"pick={s.pick_count} interval={s.run_interval_days} "
          f"filter={s.candidate_filter}/{s.candidate_market} mode={s.selection_mode}")
    return s


def main():
    db = SessionLocal()
    try:
        base = db.query(Strategy).filter(Strategy.name == "KOSPI 대형주 스윙").first()
        if base is None:
            print("ERROR: 기준 전략 'KOSPI 대형주 스윙'이 없습니다.")
            sys.exit(1)
        admin = db.query(User).first()

        print("① 규칙 모멘텀 (AI 대조군) — 기준 전략 파라미터 완전 복제, 선정만 규칙")
        _create(
            db, admin, RULE_MOMENTUM,
            description=(
                f"{base.name}의 AI 대조군. 파라미터·유니버스 동일, 선정만 규칙 기반"
                "(20일 신고가 돌파 + 종가>MA5). Gemini 호출 없음 — "
                "AI가 단순 규칙 대비 값을 하는지 판정용. 관찰 모드."
            ),
            hold_days=base.hold_days,
            target_pct=base.target_pct,
            stop_loss_pct=base.stop_loss_pct,
            pick_count=base.pick_count,
            run_interval_days=base.run_interval_days,
            candidate_filter=base.candidate_filter,
            candidate_market=base.candidate_market,
            use_trailing_stop=base.use_trailing_stop,
            selection_mode="rule_breakout",
        )

        print("② 주기 1일 복제본 — 표본 축적 가속 (파라미터는 원본 유지)")
        _create(
            db, admin, FAST_CYCLE,
            description=(
                f"{base.name} 복제본, run_interval_days만 1일. 측정 대상은 원 전략 그대로이고 "
                "회전율·비용 구조도 변하지 않는다 (보유 20일 유지). 표본 축적 가속용 관찰 모드."
            ),
            hold_days=base.hold_days,
            target_pct=base.target_pct,
            stop_loss_pct=base.stop_loss_pct,
            pick_count=base.pick_count,
            run_interval_days=1,
            candidate_filter=base.candidate_filter,
            candidate_market=base.candidate_market,
            use_trailing_stop=base.use_trailing_stop,
            selection_mode=base.selection_mode,
        )

        print("③ 과매도 반등 (규칙) — 방향 반대라 기존 전략들과 상관 분산")
        _create(
            db, admin, OVERSOLD,
            description=(
                "규칙 기반 역방향 전략 (RSI≤35 + 거래대금 전일 1.5배). Gemini 호출 없음. "
                "기존 전략이 전부 모멘텀 방향이라 같은 국면에서 동시에 죽는 문제 완화용. 관찰 모드."
            ),
            hold_days=10,
            target_pct=Decimal("7.0"),
            stop_loss_pct=Decimal("3.5"),
            pick_count=3,
            run_interval_days=3,
            candidate_filter="largecap",
            candidate_market="KOSPI",
            use_trailing_stop=base.use_trailing_stop,
            selection_mode="rule_oversold",
        )

        print(f"④ 비활성화: {DEACTIVATE}")
        target = db.query(Strategy).filter(Strategy.name == DEACTIVATE).first()
        if target is None:
            print("  대상 없음 — 건너뜀")
        elif not target.is_active:
            print("  이미 비활성 — 건너뜀")
        else:
            # 삭제 금지: recommendation_runs가 ondelete=CASCADE라 지우면 과거 데이터가 전부 날아간다
            target.is_active = False
            print("  is_active=False (과거 run·추천·검증은 그대로 보존)")

        db.commit()
        print("\n완료 — 구독 없음(관찰 모드), 다음 08:30 분석 잡부터 실행됨")
    finally:
        db.close()


if __name__ == "__main__":
    main()
