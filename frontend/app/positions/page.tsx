"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Position, PositionStatus, NewsWatchConfig, Strategy, BrokerAccount, User, getToken } from "@/lib/api";
import Badge from "@/components/Badge";
import StockSearch from "@/components/StockSearch";
import { StockItem } from "@/lib/korean-stocks";
import { usePriceStream } from "@/hooks/usePriceStream";

const STATUS_TABS: { value: PositionStatus | "ALL"; label: string }[] = [
  { value: "ALL",         label: "전체" },
  { value: "HOLDING",     label: "보유중" },
  { value: "TARGET_HIT",  label: "목표달성" },
  { value: "STOP_LOSS",   label: "손절" },
  { value: "EXPIRED",     label: "만료" },
  { value: "MANUAL_EXIT", label: "수동청산" },
];

function pnlColor(pnl: string | null) {
  if (!pnl) return "text-gray-400";
  return parseFloat(pnl) >= 0 ? "text-green-400" : "text-red-400";
}

export default function PositionsPage() {
  const router = useRouter();
  const [me, setMe]               = useState<User | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [tab, setTab]             = useState<PositionStatus | "ALL">("ALL");
  const [loading, setLoading]     = useState(true);

  // 수동 매수 모달
  const [showBuyModal, setShowBuyModal] = useState(false);
  const [buyStockCode, setBuyStockCode] = useState("");
  const [buyAmount, setBuyAmount]       = useState("300000");
  const [buyAccountId, setBuyAccountId] = useState("");
  const [buyStrategyId, setBuyStrategyId] = useState("");
  const [buyLoading, setBuyLoading]     = useState(false);
  const [buyError, setBuyError]         = useState("");
  const [buyPriceInfo, setBuyPriceInfo] = useState<{ current_price: number; open_price: number; change_pct: number } | null>(null);
  const [buyPriceLoading, setBuyPriceLoading] = useState(false);
  const [accounts, setAccounts]         = useState<BrokerAccount[]>([]);
  const [strategies, setStrategies]     = useState<Strategy[]>([]);

  // 뉴스 감시 설정
  const [newsConfig, setNewsConfig]     = useState<NewsWatchConfig | null>(null);
  const [newsInterval, setNewsInterval] = useState("40");
  const [newsLoading, setNewsLoading]   = useState(false);
  const [showNewsPanel, setShowNewsPanel] = useState(false);

  // 계좌 설정 (HTS ID)
  const [showAccountPanel, setShowAccountPanel] = useState(false);
  const [htsId, setHtsId]       = useState("");
  const [htsLoading, setHtsLoading] = useState(false);
  const [htsMsg, setHtsMsg]     = useState("");

  const isAdmin = me?.role === "ADMIN" || me?.role === "SUPER_ADMIN";

  // 보유 종목 실시간 가격
  const holdingCodes = positions
    .filter((p) => p.status === "HOLDING")
    .map((p) => p.stock_code);
  const { prices: livePrices, connected: liveConnected } = usePriceStream(holdingCodes);

  // 장 마감 fallback: REST로 가져온 가격 (WebSocket 없을 때 사용)
  const [restPrices, setRestPrices] = useState<Record<string, { current_price: number; bid_price: number; change_pct: number }>>({});

  // livePrices 우선, 없으면 restPrices fallback
  const displayPrices = { ...restPrices, ...livePrices };

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const [userData, posData] = await Promise.all([api.auth.me(), api.positions.list()]);
      setMe(userData);
      setPositions(posData);
      const [accs, strats] = await Promise.all([
        api.users.listBrokerAccounts(userData.user_id),
        api.strategies.list(),
      ]);
      const activeAccs = accs.filter((a) => a.is_active);
      setAccounts(activeAccs);
      setStrategies(strats);
      if (activeAccs.length > 0) {
        setBuyAccountId(activeAccs[0].account_id);
        setHtsId(activeAccs[0].hts_id ?? "");
      }

      // HOLDING 포지션 REST 가격 조회 (장 마감 후 fallback)
      const holdingPos = posData.filter((p) => p.status === "HOLDING");
      const priceResults = await Promise.allSettled(
        holdingPos.map((p) => api.market.price(p.stock_code))
      );
      const rp: typeof restPrices = {};
      priceResults.forEach((r, i) => {
        if (r.status === "fulfilled") {
          const code = holdingPos[i].stock_code;
          rp[code] = {
            current_price: r.value.current_price,
            bid_price: r.value.current_price,  // REST엔 bid 없으므로 current로 대체
            change_pct: r.value.change_pct,
          };
        }
      });
      setRestPrices(rp);
    } finally {
      setLoading(false);
    }
  }

  async function loadNewsConfig() {
    try {
      const cfg = await api.admin.getNewsWatchConfig();
      setNewsConfig(cfg);
      setNewsInterval(String(cfg.interval_min));
    } catch { /* 권한 없음 */ }
  }

  async function handleClosePosition(positionId: string) {
    if (!confirm("이 포지션을 수동 청산하시겠습니까?")) return;
    try {
      const updated = await api.positions.close(positionId);
      setPositions((prev) => prev.map((p) => p.position_id === updated.position_id ? updated : p));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "청산 실패");
    }
  }

  async function handleCloseAll() {
    const holding = positions.filter((p) => p.status === "HOLDING");
    if (!confirm(`보유 중인 ${holding.length}개 포지션을 전부 청산하시겠습니까?`)) return;
    try {
      const result = await api.positions.closeAll();
      alert(`${result.closed}개 청산 완료`);
      const updated = await api.positions.list();
      setPositions(updated);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "전체 청산 실패");
    }
  }

  async function handleManualBuy() {
    if (!buyStockCode) { setBuyError("종목을 선택하세요"); return; }
    const amount = parseInt(buyAmount.replace(/,/g, ""), 10);
    if (!amount || amount < 10000) { setBuyError("최소 10,000원 이상 입력하세요"); return; }
    setBuyLoading(true); setBuyError("");
    try {
      const pos = await api.positions.manualBuy({
        stock_code: buyStockCode,
        account_id: buyAccountId,
        amount,
        strategy_id: buyStrategyId || undefined,
      });
      setPositions((prev) => [pos, ...prev]);
      setShowBuyModal(false);
      setBuyStockCode("");
    } catch (e: unknown) {
      setBuyError(e instanceof Error ? e.message : "매수 실패");
    } finally {
      setBuyLoading(false);
    }
  }

  async function handleUpdateNewsInterval() {
    const val = parseInt(newsInterval, 10);
    if (val < 30) { alert("최소 30분입니다"); return; }
    setNewsLoading(true);
    try {
      await api.admin.updateNewsWatchInterval(val);
      await loadNewsConfig();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "설정 실패");
    } finally {
      setNewsLoading(false); }
  }

  async function handleSaveHtsId() {
    if (!me || accounts.length === 0) return;
    setHtsLoading(true); setHtsMsg("");
    try {
      const updated = await api.users.updateBrokerAccount(me.user_id, accounts[0].account_id, {
        hts_id: htsId.trim() || null,
      });
      setAccounts((prev) => prev.map((a) => a.account_id === updated.account_id ? updated : a));
      setHtsMsg("저장됐습니다. 서버 재시작 후 체결통보가 활성화됩니다.");
    } catch {
      setHtsMsg("저장 실패");
    } finally {
      setHtsLoading(false);
    }
  }

  async function handleResumeAutoTrade() {
    if (!confirm("자동매매를 재개하시겠습니까?")) return;
    await api.admin.resumeAutoTrade();
    await loadNewsConfig();
  }

  const filtered  = tab === "ALL" ? positions : positions.filter((p) => p.status === tab);
  const holding   = positions.filter((p) => p.status === "HOLDING");
  const closed    = positions.filter((p) => p.status !== "HOLDING");
  const winCount  = closed.filter((p) => p.status === "TARGET_HIT").length;
  const winRate   = closed.length > 0 ? ((winCount / closed.length) * 100).toFixed(1) : null;
  const avgPnl    = closed.length > 0
    ? (closed.reduce((s, p) => s + parseFloat(p.pnl_pct ?? "0"), 0) / closed.length).toFixed(2)
    : null;

  // 뉴스 설정 패널에서 주기 변경 시 예상 사용량 계산
  const previewInterval = parseInt(newsInterval, 10) || 40;
  const previewDaily    = Math.max(1, Math.floor(390 / previewInterval)); // 390분 = 6.5시간

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-gray-400">로딩 중...</div>
  );

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">포지션 현황</h1>
          {holdingCodes.length > 0 && (
            <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${
              liveConnected ? "bg-green-900/50 text-green-400" : "bg-gray-700 text-gray-400"
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${liveConnected ? "bg-green-400 animate-pulse" : "bg-gray-500"}`} />
              {liveConnected ? "LIVE" : "연결 중..."}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          {isAdmin && (
            <>
              <button
                onClick={() => setShowAccountPanel(!showAccountPanel)}
                className="text-sm px-3 py-1.5 rounded-lg border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors"
              >
                계좌 설정
              </button>
              <button
                onClick={() => { setShowNewsPanel(!showNewsPanel); if (!showNewsPanel) loadNewsConfig(); }}
                className="text-sm px-3 py-1.5 rounded-lg border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors"
              >
                뉴스 감시 설정
              </button>
            </>
          )}
          <button
            onClick={() => setShowBuyModal(true)}
            className="text-sm px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          >
            수동 매수
          </button>
          {holding.length > 0 && (
            <button
              onClick={handleCloseAll}
              className="text-sm px-3 py-1.5 rounded-lg bg-red-700 hover:bg-red-600 text-white transition-colors"
            >
              전체 청산
            </button>
          )}
        </div>
      </div>

      {/* 계좌 설정 패널 */}
      {showAccountPanel && isAdmin && accounts.length > 0 && (
        <div className="bg-gray-800 rounded-2xl p-5 mb-4">
          <h3 className="font-semibold mb-4 text-sm">계좌 설정</h3>
          <div className="flex flex-wrap gap-4 items-end">
            <div>
              <label className="text-xs text-gray-400 mb-1 block">
                계좌 ({accounts[0].broker} {accounts[0].account_no})
              </label>
              <div className="text-xs text-gray-500">HTS 아이디 (체결통보 WebSocket용)</div>
            </div>
            <div>
              <label className="text-xs text-gray-400 mb-1 block">HTS 아이디</label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={htsId}
                  onChange={(e) => setHtsId(e.target.value)}
                  placeholder="예: fireworm"
                  className="bg-gray-700 rounded-lg px-3 py-2 text-sm w-36 outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button
                  onClick={handleSaveHtsId}
                  disabled={htsLoading}
                  className="text-xs bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg"
                >
                  {htsLoading ? "저장 중..." : "저장"}
                </button>
              </div>
              {htsMsg && <p className="text-xs text-green-400 mt-1">{htsMsg}</p>}
            </div>
          </div>
        </div>
      )}

      {/* 뉴스 감시 설정 패널 */}
      {showNewsPanel && isAdmin && (
        <div className="bg-gray-800 rounded-2xl p-5 mb-6">
          <h3 className="font-semibold mb-4 text-sm">뉴스 감시 설정</h3>
          {newsConfig ? (
            <div className="flex flex-col gap-4">
              {newsConfig.paused && (
                <div className="flex items-center gap-3 bg-red-900/30 border border-red-700 rounded-xl px-4 py-3">
                  <span className="text-red-400 font-semibold text-sm">자동매매 중단 중</span>
                  <span className="text-gray-300 text-xs flex-1">{newsConfig.pause_reason}</span>
                  <button
                    onClick={handleResumeAutoTrade}
                    className="text-xs bg-green-700 hover:bg-green-600 text-white px-3 py-1 rounded-lg"
                  >재개</button>
                </div>
              )}
              <div className="flex flex-wrap gap-6 items-end">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">감시 주기 (분)</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={newsInterval}
                      onChange={(e) => setNewsInterval(e.target.value)}
                      min={30}
                      step={10}
                      className="bg-gray-700 rounded-lg px-3 py-2 text-sm w-24 outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      onClick={handleUpdateNewsInterval}
                      disabled={newsLoading}
                      className="text-xs bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg"
                    >적용</button>
                  </div>
                </div>
                <div className="text-sm">
                  <div className="text-gray-400 text-xs mb-1">예상 일일 사용량</div>
                  <div className={`font-bold ${previewDaily + 2 > newsConfig.rpd_limit ? "text-red-400" : "text-green-400"}`}>
                    {previewDaily}회
                    <span className="text-gray-400 font-normal text-xs ml-1">
                      (전략 run ~2회 포함 총 {previewDaily + 2}회 / RPD {newsConfig.rpd_limit})
                    </span>
                  </div>
                  <div className="w-48 h-2 bg-gray-700 rounded-full mt-1 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${previewDaily + 2 > newsConfig.rpd_limit ? "bg-red-500" : "bg-blue-500"}`}
                      style={{ width: `${Math.min(100, ((previewDaily + 2) / newsConfig.rpd_limit) * 100)}%` }}
                    />
                  </div>
                </div>
                <div className="text-xs text-gray-500">
                  <div>오늘 사용: {newsConfig.today_usage}회</div>
                  <div>마지막 체크: {newsConfig.last_check_at ? new Date(newsConfig.last_check_at).toLocaleTimeString("ko-KR") : "-"}</div>
                </div>
              </div>
              <p className="text-xs text-gray-500">※ 최소 30분 (이하 설정 시 RPD 20 초과 위험) · 장중 09:00~15:30에만 실행</p>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">로딩 중...</p>
          )}
        </div>
      )}

      {/* 요약 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">보유중</div>
          <div className="text-2xl font-bold text-blue-400">{holding.length}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">종료 포지션</div>
          <div className="text-2xl font-bold text-gray-300">{closed.length}</div>
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">승률</div>
          <div className={`text-2xl font-bold ${winRate && parseFloat(winRate) >= 50 ? "text-green-400" : "text-gray-400"}`}>
            {winRate ? `${winRate}%` : "-"}
          </div>
          {closed.length > 0 && <div className="text-xs text-gray-500">{winCount}승 {closed.length - winCount}패</div>}
        </div>
        <div className="bg-gray-800 rounded-xl p-4">
          <div className="text-xs text-gray-400">평균 수익률</div>
          <div className={`text-2xl font-bold ${avgPnl && parseFloat(avgPnl) >= 0 ? "text-green-400" : "text-red-400"}`}>
            {avgPnl ? `${parseFloat(avgPnl) >= 0 ? "+" : ""}${avgPnl}%` : "-"}
          </div>
        </div>
      </div>

      {/* 탭 */}
      <div className="flex gap-1 mb-4 flex-wrap">
        {STATUS_TABS.map((t) => (
          <button key={t.value} onClick={() => setTab(t.value)}
            className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
              tab === t.value ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-gray-700"
            }`}>
            {t.label}
            {t.value !== "ALL" && (
              <span className="ml-1 text-xs opacity-70">
                {positions.filter((p) => p.status === t.value).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* 테이블 */}
      {filtered.length === 0 ? (
        <p className="text-gray-500 text-center py-12">포지션 없음</p>
      ) : (
        <div className="bg-gray-800 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-4">종목</th>
                <th className="text-left p-4">상태</th>
                <th className="text-right p-4">수량</th>
                <th className="text-right p-4">매수가</th>
                <th className="text-right p-4">현재가</th>
                <th className="text-right p-4">미실현</th>
                <th className="text-right p-4 text-green-500">익절가</th>
                <th className="text-right p-4 text-red-500">손절가</th>
                <th className="text-right p-4">확정손익</th>
                <th className="text-right p-4">매수일</th>
                <th className="p-4"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((pos) => {
                const live = displayPrices[pos.stock_code];
                const entryPrice = Number(pos.entry_price);
                // 시장가 매도 시 bid_price(매수호가1) 기준으로 미실현 손익 계산
                const sellPrice = live ? (live.bid_price || live.current_price) : null;
                const unrealizedPct = sellPrice != null ? ((sellPrice - entryPrice) / entryPrice * 100) : null;
                const unrealizedAmt = sellPrice != null ? Math.round((sellPrice - entryPrice) * pos.quantity) : null;
                // 확정손익 금액
                const pnlAmt = pos.exit_price
                  ? Math.round((Number(pos.exit_price) - entryPrice) * pos.quantity)
                  : null;
                return (
                <tr key={pos.position_id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="p-4 font-medium">{pos.stock_code}</td>
                  <td className="p-4"><Badge value={pos.status} /></td>
                  <td className="p-4 text-right text-gray-300">{pos.quantity}</td>
                  <td className="p-4 text-right text-gray-300">
                    {entryPrice.toLocaleString()}
                  </td>
                  <td className="p-4 text-right">
                    {pos.status === "HOLDING" && live ? (
                      <div>
                        <div className={`font-medium ${live.change >= 0 ? "text-red-400" : "text-blue-400"}`}>
                          {live.current_price.toLocaleString()}
                        </div>
                        <div className={`text-xs ${live.change >= 0 ? "text-red-400" : "text-blue-400"}`}>
                          {live.change >= 0 ? "+" : ""}{live.change.toLocaleString()} ({live.change_pct >= 0 ? "+" : ""}{live.change_pct.toFixed(2)}%)
                        </div>
                      </div>
                    ) : (
                      <span className="text-gray-500">
                        {pos.exit_price ? Number(pos.exit_price).toLocaleString() : "-"}
                      </span>
                    )}
                  </td>
                  <td className={`p-4 text-right ${
                    unrealizedPct == null ? "text-gray-500"
                    : unrealizedPct >= 0 ? "text-red-400" : "text-blue-400"
                  }`}>
                    {unrealizedPct != null && pos.status === "HOLDING" ? (
                      <div>
                        <div className="font-bold">{unrealizedPct >= 0 ? "+" : ""}{unrealizedPct.toFixed(2)}%</div>
                        <div className="text-xs">{unrealizedAmt! >= 0 ? "+" : ""}{unrealizedAmt!.toLocaleString()}원</div>
                      </div>
                    ) : "-"}
                  </td>
                  <td className="p-4 text-right text-green-500 text-xs">
                    {pos.target_price ? Number(pos.target_price).toLocaleString() : "-"}
                  </td>
                  <td className="p-4 text-right text-red-500 text-xs">
                    {pos.trailing_stop_price ? Number(pos.trailing_stop_price).toLocaleString() : "-"}
                  </td>
                  <td className={`p-4 text-right ${pnlColor(pos.pnl_pct)}`}>
                    {pos.pnl_pct ? (
                      <div>
                        <div className="font-bold">{parseFloat(pos.pnl_pct) >= 0 ? "+" : ""}{parseFloat(pos.pnl_pct).toFixed(2)}%</div>
                        {pnlAmt != null && (
                          <div className="text-xs">{pnlAmt >= 0 ? "+" : ""}{pnlAmt.toLocaleString()}원</div>
                        )}
                      </div>
                    ) : "-"}
                  </td>
                  <td className="p-4 text-right text-gray-400 text-xs">{pos.entry_date}</td>
                  <td className="p-4 text-right">
                    {pos.status === "HOLDING" && (
                      <button
                        onClick={() => handleClosePosition(pos.position_id)}
                        className="text-xs text-red-400 hover:text-red-300 border border-red-800 hover:border-red-600 px-2 py-1 rounded transition-colors"
                      >청산</button>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 수동 매수 모달 */}
      {showBuyModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowBuyModal(false)}>
          <div className="bg-gray-800 rounded-2xl p-6 w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-semibold mb-4">수동 매수</h3>
            <div className="flex flex-col gap-3">
              <div>
                <label className="text-xs text-gray-400 mb-1 block">종목</label>
                <StockSearch
                  onSelect={async (stock: StockItem) => {
                    setBuyStockCode(stock.code);
                    setBuyPriceInfo(null);
                    setBuyPriceLoading(true);
                    try {
                      const p = await api.market.price(stock.code);
                      setBuyPriceInfo({ current_price: p.current_price, open_price: p.open_price, change_pct: p.change_pct });
                    } catch { /* 조회 실패 시 무시 */ } finally {
                      setBuyPriceLoading(false);
                    }
                  }}
                  placeholder="종목명 또는 코드 검색"
                />
                {buyStockCode && (
                  <div className="mt-1 flex items-center gap-3 text-xs">
                    <span className="text-blue-400">선택: {buyStockCode}</span>
                    {buyPriceLoading && <span className="text-gray-500">조회 중...</span>}
                    {buyPriceInfo && (
                      <>
                        <span className="text-gray-400">시가 <span className="text-white">{buyPriceInfo.open_price.toLocaleString()}</span></span>
                        <span className="text-gray-400">현재가 <span className="text-yellow-300 font-medium">{buyPriceInfo.current_price.toLocaleString()}</span></span>
                        <span className={buyPriceInfo.change_pct >= 0 ? "text-red-400" : "text-blue-400"}>
                          {buyPriceInfo.change_pct >= 0 ? "+" : ""}{buyPriceInfo.change_pct.toFixed(2)}%
                        </span>
                      </>
                    )}
                  </div>
                )}
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">계좌</label>
                <select
                  value={buyAccountId}
                  onChange={(e) => setBuyAccountId(e.target.value)}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {accounts.map((a) => (
                    <option key={a.account_id} value={a.account_id}>
                      {a.broker} {a.account_no} ({a.account_type})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">전략 (선택)</label>
                <select
                  value={buyStrategyId}
                  onChange={(e) => setBuyStrategyId(e.target.value)}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">전략 없음</option>
                  {strategies.map((s) => (
                    <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">투자금액 (원)</label>
                <input
                  type="number"
                  value={buyAmount}
                  onChange={(e) => setBuyAmount(e.target.value)}
                  min={10000}
                  step={10000}
                  className="w-full bg-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              {buyError && <p className="text-red-400 text-xs">{buyError}</p>}
              <div className="flex gap-2 mt-2">
                <button
                  onClick={handleManualBuy}
                  disabled={buyLoading}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 text-sm font-medium"
                >
                  {buyLoading ? "처리 중..." : "매수 확인"}
                </button>
                <button
                  onClick={() => { setShowBuyModal(false); setBuyError(""); }}
                  className="px-4 py-2 text-sm text-gray-400 hover:text-white"
                >취소</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
