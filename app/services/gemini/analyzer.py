"""
Gemini 4단계 AI 분석 파이프라인.

단계별 모델 역할 분리 (무료 티어 RPD 기준):
  Stage 1 (매크로/그라운딩) : gemini-2.5-flash           RPD ~20  — Google Search 그라운딩
  Stage 2 (역사적 패턴)     : gemini-3-flash-preview      RPD ~20  → 3.1-flash-lite fallback
  Stage 3 (산업 분석)       : gemini-3.1-flash-lite        RPD 500  → 2.5-flash-lite fallback
  Stage 4 (종목 선정, 핵심) : gemini-3-flash-preview      RPD ~20  → 3.1-flash-lite → 2.5-flash-lite
  JSON 정제 (파싱 실패 시)  : gemma-4-31b-it              RPD 1500
"""
import json
import re
import logging
from datetime import date
from decimal import Decimal
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.services.gemini.prompts import (
    STAGE1_MACRO, STAGE2_HISTORICAL, STAGE3_INDUSTRY, STAGE4_PICKS,
    _FILTER_GUIDANCE, BUY_CONFIRM,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 단계별 모델 체인
# ------------------------------------------------------------------ #
_CHAIN_STAGE1 = ["gemini-2.5-flash"]                                      # 검색 그라운딩 전용
_CHAIN_STAGE2 = ["gemini-3-flash-preview", "gemini-3.1-flash-lite"]       # 역사 분석, 고품질
_CHAIN_STAGE3 = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]        # 산업 분석, RPD 절약
_CHAIN_STAGE4 = ["gemini-3-flash-preview", "gemini-3.1-flash-lite",
                 "gemini-2.5-flash-lite"]                                  # 종목 선정, 최고 품질
_MODEL_GEMMA4  = "gemma-4-31b-it"                                          # JSON 정제 전용

_SEARCH_CONFIG = types.GenerateContentConfig(
    tools=[types.Tool(google_search=types.GoogleSearch())]
)
_CHAIN_BACKTEST = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]


@dataclass
class MacroResult:
    macro_summary: str
    key_factors: list[str]
    market_theme: str
    risk_factors: list[str]
    sector_outlook: dict
    model_used: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class HistoricalResult:
    historical_matches: list[dict]
    model_used: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class IndustryResult:
    past_winners: list[dict]
    past_losers: list[dict]
    sector_mapping: dict
    expected_beneficiary: str
    model_used: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class PickResult:
    picks: list[dict]
    excluded_reason: str
    model_used: str = ""
    raw: dict = field(default_factory=dict)


class GeminiAnalyzer:
    """단계별 모델 역할이 분리된 4단계 AI 분석 파이프라인."""

    def __init__(self):
        self._client = genai.Client(api_key=get_settings().gemini_api_key)

    # ------------------------------------------------------------------ #
    # 내부 헬퍼
    # ------------------------------------------------------------------ #

    def _call_model(self, prompt: str, model_name: str) -> str:
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        return response.text

    def _call_model_with_search(self, prompt: str, model_name: str) -> str:
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=_SEARCH_CONFIG,
        )
        return response.text

    def _call_with_fallback(self, prompt: str, chain: list[str]) -> tuple[str, str]:
        """
        체인 순서대로 시도. (응답 텍스트, 사용된 모델명) 반환.
        전부 실패 시 어드민에게 에러 알림 후 RuntimeError.
        """
        last_err = None
        for model_name in chain:
            try:
                text = self._call_model(prompt, model_name)
                if model_name != chain[0]:
                    logger.info("Stage fallback succeeded: %s", model_name)
                return text, model_name
            except Exception as e:
                logger.warning("Model %s failed: %s", model_name, e)
                last_err = e

        from app.services.telegram.notifier import notify_admins_error
        notify_admins_error(
            "Gemini API 전체 실패",
            f"체인: {chain}\n마지막 오류: {last_err}",
        )
        raise RuntimeError(f"All models failed. Last: {last_err}")

    def _parse_json(self, text: str) -> dict:
        """응답에서 JSON 추출. 실패 시 Gemma 4로 정제 재시도."""
        cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, retrying with Gemma 4 repair")
            try:
                repair_prompt = (
                    "아래 텍스트에서 JSON 부분만 추출해 올바른 JSON으로 반환하세요. "
                    "다른 설명 없이 JSON만 반환하세요.\n\n" + text[:3000]
                )
                repaired = self._call_model(repair_prompt, _MODEL_GEMMA4)
                repaired_clean = re.sub(r"```(?:json)?\s*", "", repaired).replace("```", "").strip()
                return json.loads(repaired_clean)
            except Exception as e:
                logger.error("Gemma 4 JSON repair also failed: %s", e)
                raise

    # ------------------------------------------------------------------ #
    # 4단계 파이프라인
    # ------------------------------------------------------------------ #

    def stage1_macro(self, today: date | None = None) -> MacroResult:
        """1단계: 현재 매크로 상황 파악 (Google Search 그라운딩)."""
        prompt = STAGE1_MACRO.format(today=str(today or date.today()))
        model = _CHAIN_STAGE1[0]
        try:
            text = self._call_model_with_search(prompt, model)
        except Exception as e:
            logger.warning("Stage1 grounding failed (%s), fallback to Stage2 chain", e)
            text, model = self._call_with_fallback(prompt, _CHAIN_STAGE2)

        data = self._parse_json(text)
        return MacroResult(
            macro_summary=data.get("macro_summary", ""),
            key_factors=data.get("key_factors", []),
            market_theme=data.get("market_theme", ""),
            risk_factors=data.get("risk_factors", []),
            sector_outlook=data.get("sector_outlook", {}),
            model_used=model,
            raw=data,
        )

    def stage2_historical(self, macro: MacroResult) -> HistoricalResult:
        """2단계: 역사적 유사 시기 탐색 (고품질 추론 모델)."""
        prompt = STAGE2_HISTORICAL.format(
            macro_summary=macro.macro_summary,
            key_factors=", ".join(macro.key_factors),
            market_theme=macro.market_theme,
        )
        text, model = self._call_with_fallback(prompt, _CHAIN_STAGE2)
        data = self._parse_json(text)
        return HistoricalResult(
            historical_matches=data.get("historical_matches", []),
            model_used=model,
            raw=data,
        )

    def stage3_industry(self, macro: MacroResult, historical: HistoricalResult) -> IndustryResult:
        """3단계: 산업 흐름 분석 (Lite 모델로 RPD 절약)."""
        prompt = STAGE3_INDUSTRY.format(
            macro_summary=macro.macro_summary,
            market_theme=macro.market_theme,
            historical_matches=json.dumps(historical.historical_matches, ensure_ascii=False, indent=2),
        )
        text, model = self._call_with_fallback(prompt, _CHAIN_STAGE3)
        data = self._parse_json(text)
        return IndustryResult(
            past_winners=data.get("past_winners", []),
            past_losers=data.get("past_losers", []),
            sector_mapping=data.get("sector_mapping", {}),
            expected_beneficiary=data.get("expected_beneficiary", ""),
            model_used=model,
            raw=data,
        )

    def stage4_picks(
        self,
        macro: MacroResult,
        industry: IndustryResult,
        stocks_data: list[dict],
        hold_days: int,
        target_pct: Decimal,
        stop_loss_pct: Decimal,
        min_probability: Decimal,
        pick_count: int,
        candidate_filter: str = "mixed",
    ) -> PickResult:
        """4단계: 종목 선정 (최고 품질 모델 우선)."""
        guidance = _FILTER_GUIDANCE.get(candidate_filter, _FILTER_GUIDANCE["mixed"])
        prompt = STAGE4_PICKS.format(
            macro_summary=macro.macro_summary,
            market_theme=macro.market_theme,
            expected_beneficiary=industry.expected_beneficiary,
            stocks_data=json.dumps(stocks_data, ensure_ascii=False, indent=2),
            hold_days=hold_days,
            target_pct=float(target_pct),
            stop_loss_pct=float(stop_loss_pct),
            min_probability=float(min_probability),
            pick_count=pick_count,
            filter_guidance=guidance,
        )
        text, model = self._call_with_fallback(prompt, _CHAIN_STAGE4)
        data = self._parse_json(text)
        return PickResult(
            picks=data.get("picks", []),
            excluded_reason=data.get("excluded_reason", ""),
            model_used=model,
            raw=data,
        )

    def stage4_picks_backtest(
        self,
        stocks_data: list[dict],
        hold_days: int,
        target_pct: Decimal,
        stop_loss_pct: Decimal,
        min_probability: Decimal,
        pick_count: int,
        candidate_filter: str,
        backtest_date,
    ) -> PickResult:
        """백테스트용 Stage4. Lite 모델만 사용, 매크로 컨텍스트는 placeholder."""
        guidance = _FILTER_GUIDANCE.get(candidate_filter, _FILTER_GUIDANCE["mixed"])
        prompt = STAGE4_PICKS.format(
            macro_summary=f"{backtest_date} 기준 과거 데이터 백테스트 시뮬레이션. 기술적 지표 중심으로 판단.",
            market_theme="백테스트",
            expected_beneficiary="기술적 지표 우수 종목",
            stocks_data=json.dumps(stocks_data, ensure_ascii=False, indent=2),
            hold_days=hold_days,
            target_pct=float(target_pct),
            stop_loss_pct=float(stop_loss_pct),
            min_probability=float(min_probability),
            pick_count=pick_count,
            filter_guidance=guidance,
        )
        text, model = self._call_with_fallback(prompt, _CHAIN_BACKTEST)
        data = self._parse_json(text)
        return PickResult(
            picks=data.get("picks", []),
            excluded_reason=data.get("excluded_reason", ""),
            model_used=model,
            raw=data,
        )

    # ------------------------------------------------------------------ #
    # 통합 실행
    # ------------------------------------------------------------------ #

    def run_full_pipeline(
        self,
        strategy,
        candidate_stocks: list[dict],
        today: date | None = None,
    ) -> tuple[MacroResult, HistoricalResult, IndustryResult, PickResult]:
        logger.info("=== AI Pipeline Start: strategy=%s ===", strategy.name)

        macro = self.stage1_macro(today)
        logger.info("Stage1 done: theme=%s [%s]", macro.market_theme, macro.model_used)

        historical = self.stage2_historical(macro)
        logger.info("Stage2 done: %d matches [%s]", len(historical.historical_matches), historical.model_used)

        industry = self.stage3_industry(macro, historical)
        logger.info("Stage3 done: beneficiary=%s [%s]", industry.expected_beneficiary[:40], industry.model_used)

        picks = self.stage4_picks(
            macro=macro,
            industry=industry,
            stocks_data=candidate_stocks,
            hold_days=strategy.hold_days,
            target_pct=strategy.target_pct,
            stop_loss_pct=strategy.stop_loss_pct,
            min_probability=strategy.min_probability,
            pick_count=strategy.pick_count,
            candidate_filter=getattr(strategy, "candidate_filter", "mixed"),
        )
        logger.info("Stage4 done: %d picks [%s]", len(picks.picks), picks.model_used)

        return macro, historical, industry, picks

    def confirm_buys(self, stocks: list[dict], market_status: dict) -> dict[str, str]:
        """
        09:20 매수 확인 단계. 장중 데이터를 보고 종목별 buy/skip 결정.
        반환: {stock_code: "buy" | "skip"}
        """
        import json as _json
        _CHAIN_CONFIRM = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]

        kospi = market_status.get("KOSPI", 0)
        kosdaq = market_status.get("KOSDAQ", 0)
        market_str = f"KOSPI {kospi:+.2f}%, KOSDAQ {kosdaq:+.2f}%"

        prompt = BUY_CONFIRM.format(
            market_status=market_str,
            stocks_json=_json.dumps(stocks, ensure_ascii=False, indent=2),
        )

        text, model = self._call_with_fallback(_CHAIN_CONFIRM, prompt)
        logger.info("Buy confirm done [%s]", model)

        try:
            data = self._parse_json(text)
            return {
                d["stock_code"]: d["action"]
                for d in data.get("decisions", [])
                if "stock_code" in d and "action" in d
            }
        except Exception as e:
            logger.error("Buy confirm parse error: %s", e)
            return {s["stock_code"]: "buy" for s in stocks}  # 파싱 실패 시 전부 매수
