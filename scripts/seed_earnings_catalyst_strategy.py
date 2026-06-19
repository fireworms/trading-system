"""
실적 카탈리스트 테스트 전략 추가 (관찰 모드 A/B).

기존 모멘텀 전략 1개의 파라미터(보유기간/목표/손절/종목풀)를 그대로 복제하고
selection_mode만 'earnings_catalyst'로 바꾼 전략을 추가한다.
→ 파라미터·종목풀이 동일하므로 검증 데이터 차이는 '선정 로직'만 반영(깨끗한 A/B).

구독(user_strategies) 안 만듦 — 08:30 분석 잡은 활성 전략 전체를 실행하고
verifier가 auto_trade 무관하게 추천을 채점하므로, 활성 전략 row만으로 관찰 데이터가 쌓인다.

실행: .venv/bin/python -m scripts.seed_earnings_catalyst_strategy
"""
import sys
sys.path.insert(0, ".")

from app.core.database import SessionLocal
from app.models.strategy import Strategy
from app.models.user import User

NEW_NAME = "[TEST] 실적 카탈리스트"


def main():
    db = SessionLocal()
    try:
        if db.query(Strategy).filter(Strategy.name == NEW_NAME).first():
            print(f"이미 존재: {NEW_NAME}")
            return

        # 템플릿: 대형주 스윙 우선 (실적 카탈리스트는 대형주에서 풍부 + PEAD 드리프트가 수주 단위라
        # 보유기간 긴 스윙과 시간축이 맞음. 단타 7일 전략에 target 20% 같은 비현실 파라미터 회피).
        base = db.query(Strategy).filter(
            Strategy.is_active == True,  # noqa: E712
            Strategy.selection_mode == "momentum",
        )
        template = (
            base.filter(Strategy.name.like("%대형주%")).first()
            or base.filter(Strategy.candidate_filter == "largecap").order_by(Strategy.hold_days.desc()).first()
            or base.order_by(Strategy.hold_days.desc()).first()
        )
        if template is None:
            print("ERROR: 복제할 활성 momentum 전략이 없습니다. 먼저 기본 전략을 만드세요.")
            sys.exit(1)

        admin = db.query(User).first()
        new = Strategy(
            created_by=admin.user_id if admin else None,
            name=NEW_NAME,
            description=(
                f"{template.name} 파라미터 복제 + 실적 카탈리스트 우선 선정. "
                "모멘텀 전략과 A/B 비교용 (관찰 모드)."
            ),
            hold_days=template.hold_days,
            target_pct=template.target_pct,
            stop_loss_pct=template.stop_loss_pct,
            min_probability=template.min_probability,
            pick_count=template.pick_count,
            run_interval_days=template.run_interval_days,
            candidate_filter=template.candidate_filter,
            candidate_market=template.candidate_market,
            use_trailing_stop=template.use_trailing_stop,
            selection_mode="earnings_catalyst",
            is_active=True,
        )
        db.add(new)
        db.commit()
        db.refresh(new)
        print(f"생성 완료: {new.name} ({new.strategy_id})")
        print(f"  템플릿={template.name} | hold={new.hold_days} "
              f"target={new.target_pct} stop={new.stop_loss_pct} "
              f"filter={new.candidate_filter}/{new.candidate_market} pick={new.pick_count}")
        print("  selection_mode=earnings_catalyst | 구독 없음(관찰), 다음 분석 잡에서 실행됨")
    finally:
        db.close()


if __name__ == "__main__":
    main()
