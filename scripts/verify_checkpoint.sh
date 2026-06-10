#!/usr/bin/env bash
# 모멘텀(A) 복원 전후 검증 비교 점검 스크립트
#
# 용도: 2026-06-02 Stage4 모멘텀 복원 이후 라이브 추천의 검증 결과를
#       구 역발상 프롬프트 시기와 비교한다. (6/12부터 post-6/2 검증 시작)
#
# 해석 주의:
#   - 2026-06-04 ~ 06-05 KOSPI 폭락장(-6%/-10%, 3일 누적 약 -14%)이 있었음.
#     이 구간에 보유기간이 걸친 픽의 FAIL은 프롬프트 품질이 아니라 시장 효과일 수 있음.
#     → 섹션 4의 crash_overlap 표시와 함께 해석할 것.
#   - pre-6/2 라이브 102건 승률 11.8%는 구 역발상 프롬프트 기준 (참고용 베이스라인).
#
# 실행: bash scripts/verify_checkpoint.sh
set -euo pipefail

DB="${DB:-trading_db}"
CUTOFF="2026-06-02"
CRASH_START="2026-06-04"
CRASH_END="2026-06-05"

q() { psql -d "$DB" -P pager=off -c "$1"; }

echo "################ 1. pre/post-$CUTOFF 전체 비교 ################"
q "
SELECT CASE WHEN ru.run_date >= '$CUTOFF' THEN 'post (모멘텀 A)' ELSE 'pre (구 역발상)' END AS period,
       count(*) AS verified,
       count(*) FILTER (WHERE v.result='SUCCESS') AS wins,
       round(100.0 * count(*) FILTER (WHERE v.result='SUCCESS') / count(*), 1) AS win_rate,
       round(avg(v.pnl_pct)::numeric, 2) AS avg_pnl,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY v.pnl_pct))::numeric, 2) AS med_pnl
FROM verifications v
JOIN recommendations r ON v.rec_id = r.rec_id
JOIN recommendation_runs ru ON r.run_id = ru.run_id
GROUP BY 1 ORDER BY 1 DESC;"

echo "################ 2. post-$CUTOFF 전략별 ################"
q "
SELECT s.name, ru.run_date,
       count(*) AS verified,
       count(*) FILTER (WHERE v.result='SUCCESS') AS wins,
       round(avg(v.pnl_pct)::numeric, 2) AS avg_pnl
FROM verifications v
JOIN recommendations r ON v.rec_id = r.rec_id
JOIN recommendation_runs ru ON r.run_id = ru.run_id
JOIN strategies s ON ru.strategy_id = s.strategy_id
WHERE ru.run_date >= '$CUTOFF'
GROUP BY s.name, ru.run_date ORDER BY ru.run_date, s.name;"

echo "################ 3. 섹터 분포 (픽 기준, 검증 무관) ################"
q "
SELECT CASE WHEN ru.run_date >= '$CUTOFF' THEN 'post' ELSE 'pre' END AS period,
       coalesce(sm.sector, '(미등록)') AS sector,
       count(*) AS picks
FROM recommendations r
JOIN recommendation_runs ru ON r.run_id = ru.run_id
LEFT JOIN stock_master sm ON r.stock_code = sm.stock_code
GROUP BY 1, 2
HAVING count(*) >= 2
ORDER BY 1 DESC, 3 DESC;"

echo "################ 4. post-$CUTOFF 검증 상세 (폭락장 겹침 표시) ################"
q "
SELECT ru.run_date, s.name AS strategy, r.stock_name, v.result,
       round(v.pnl_pct::numeric, 2) AS pnl,
       CASE WHEN ru.run_date <= '$CRASH_END'
            THEN 'CRASH겹침(6/4-5 폭락)' ELSE '' END AS crash_overlap
FROM verifications v
JOIN recommendations r ON v.rec_id = r.rec_id
JOIN recommendation_runs ru ON r.run_id = ru.run_id
JOIN strategies s ON ru.strategy_id = s.strategy_id
WHERE ru.run_date >= '$CUTOFF'
ORDER BY ru.run_date, s.name, v.pnl_pct;"

echo "################ 5. 매크로 정합 참고 (run 시점 vs 다음날 지수) ################"
q "
SELECT ru.run_date, s.name AS strategy,
       round(ru.kospi_change_1d::numeric, 2) AS kospi_1d,
       round(ru.kosdaq_change_1d::numeric, 2) AS kosdaq_1d,
       ru.stage4_skipped
FROM recommendation_runs ru
JOIN strategies s ON ru.strategy_id = s.strategy_id
WHERE ru.run_date >= '$CUTOFF'
ORDER BY ru.run_date, s.name;"

echo "################ 6. 검증 대기 현황 ################"
q "
SELECT ru.run_date, s.name AS strategy, s.hold_days,
       ru.run_date + s.hold_days AS verifiable_from,
       count(r.rec_id) AS picks,
       count(v.verify_id) AS verified
FROM recommendation_runs ru
JOIN strategies s ON ru.strategy_id = s.strategy_id
LEFT JOIN recommendations r ON r.run_id = ru.run_id
LEFT JOIN verifications v ON v.rec_id = r.rec_id
WHERE ru.run_date >= '$CUTOFF'
GROUP BY ru.run_date, s.name, s.hold_days
ORDER BY verifiable_from;"
