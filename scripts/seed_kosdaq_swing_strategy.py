"""
KOSDAQ150 대형주 스윙 테스트 전략 추가 (관찰 모드).

배경: 2026-08-05 점검에서 'largecap × hold 20 × momentum' 셀(KOSPI 대형주 스윙)만
플러스(n=24, 승률 41.7%, 평균 +0.75%). 이 엣지가 KOSPI 특수인지 대형주 스윙
모멘텀 일반인지 구분하기 위해 시장 축만 KOSDAQ150으로 바꾼 관찰 전략을 추가한다.

파라미터는 원본 6/3 복제가 아닌 8/4 — KOSDAQ150 일변동성이 KOSPI200 대비
1.3~1.5배 높아 동일 손절폭은 변동성 조정 기준 과민 손절이 됨 (엣지 부재와
손절 과민을 구분하기 위한 vol-adjust). R/R 2:1 유지, 일평균 0.4%/일.

구독(user_strategies) 안 만듦 — 활성 전략 row만으로 verifier 관찰 데이터가 쌓인다.

실행: .venv/bin/python -m scripts.seed_kosdaq_swing_strategy
"""
import sys
sys.path.insert(0, ".")

from app.core.database import SessionLocal
from app.models.strategy import Strategy
from app.models.user import User

NEW_NAME = "[TEST] KOSDAQ150 대형주 스윙"


def main():
    db = SessionLocal()
    try:
        if db.query(Strategy).filter(Strategy.name == NEW_NAME).first():
            print(f"이미 존재: {NEW_NAME}")
            return

        template = db.query(Strategy).filter(
            Strategy.is_active == True,  # noqa: E712
            Strategy.selection_mode == "momentum",
            Strategy.candidate_filter == "largecap",
        ).first()
        if template is None:
            print("ERROR: 복제 기준이 될 활성 largecap momentum 전략이 없습니다.")
            sys.exit(1)

        admin = db.query(User).first()
        new = Strategy(
            created_by=admin.user_id if admin else None,
            name=NEW_NAME,
            description=(
                f"{template.name}의 시장 축 변형 (KOSDAQ150). "
                "대형주 스윙 모멘텀 엣지의 시장 일반화 테스트 (관찰 모드). "
                "target/stop 8/4는 KOSDAQ 변동성 조정 (원본 6/3 대비 R/R 동일)."
            ),
            hold_days=template.hold_days,
            target_pct=8.0,
            stop_loss_pct=4.0,
            min_probability=template.min_probability,
            pick_count=template.pick_count,
            run_interval_days=template.run_interval_days,
            candidate_filter="largecap",
            candidate_market="KOSDAQ",
            use_trailing_stop=template.use_trailing_stop,
            selection_mode="momentum",
            is_active=True,
        )
        db.add(new)
        db.commit()
        db.refresh(new)
        print(f"생성 완료: {new.name} ({new.strategy_id})")
        print(f"  hold={new.hold_days} target={new.target_pct} stop={new.stop_loss_pct} "
              f"filter={new.candidate_filter}/{new.candidate_market} pick={new.pick_count}")
        print("  구독 없음(관찰), 다음 08:30 분석 잡에서 실행됨")
    finally:
        db.close()


if __name__ == "__main__":
    main()
