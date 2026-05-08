"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, CandidateStock, getToken } from "@/lib/api";
import { StockItem } from "@/lib/korean-stocks";
import StockSearch from "@/components/StockSearch";

const BULK_PLACEHOLDER = `005930,삼성전자,KOSPI,반도체
000660,SK하이닉스,KOSPI,반도체
035420,NAVER,KOSPI,인터넷`;

export default function AdminStocksPage() {
  const router = useRouter();
  const [stocks, setStocks]   = useState<CandidateStock[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg]         = useState("");

  // 선택된 종목 (검색 후 확인/편집 대기)
  const [selected, setSelected] = useState<StockItem | null>(null);
  // 직접 입력 종목의 편집 폼
  const [editForm, setEditForm] = useState({ name: "", market: "KOSPI", sector: "" });
  const [addLoading, setAddLoading] = useState(false);

  // 일괄 등록
  const [bulkText, setBulkText] = useState("");
  const [bulkMsg, setBulkMsg]   = useState("");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, [showAll]); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    setLoading(true);
    try {
      setStocks(await api.stocks.list(!showAll));
    } finally {
      setLoading(false);
    }
  }

  async function toggleActive(s: CandidateStock) {
    try {
      const updated = await api.stocks.update(s.stock_id, { is_active: !s.is_active });
      setStocks((prev) => prev.map((x) => x.stock_id === s.stock_id ? updated : x));
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "변경 실패");
    }
  }

  async function deleteStock(s: CandidateStock) {
    if (!confirm(`${s.stock_name}(${s.stock_code})을 삭제할까요?`)) return;
    try {
      await api.stocks.remove(s.stock_id);
      setStocks((prev) => prev.filter((x) => x.stock_id !== s.stock_id));
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "삭제 실패");
    }
  }

  function handleSelect(s: StockItem) {
    setSelected(s);
    setMsg("");
    // 목록에 없는 종목(name이 비어있음)이면 편집 폼 초기화
    if (!s.name) setEditForm({ name: "", market: "KOSPI", sector: "" });
  }

  const isUnknown = selected && !selected.name;

  async function addStock() {
    if (!selected) return;
    const name   = isUnknown ? editForm.name.trim()   : selected.name;
    const market = isUnknown ? editForm.market        : selected.market;
    const sector = isUnknown ? editForm.sector.trim() : selected.sector;
    if (!name) { setMsg("종목명을 입력하세요"); return; }
    setAddLoading(true); setMsg("");
    try {
      const created = await api.stocks.create({
        stock_code: selected.code,
        stock_name: name,
        market:     market ?? null,
        sector:     sector || null,
        notes:      null,
      });
      setStocks((prev) => [...prev, created]);
      setSelected(null);
      setMsg(`${created.stock_name} 추가 완료`);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "추가 실패");
    } finally {
      setAddLoading(false);
    }
  }

  async function bulkImport() {
    setBulkMsg("");
    const lines = bulkText.trim().split("\n").filter(Boolean);
    const items = lines.map((line) => {
      const [stock_code, stock_name, market, sector] = line.split(",").map((s) => s.trim());
      return { stock_code, stock_name, market: market || null, sector: sector || null };
    });
    if (!items.length) { setBulkMsg("입력값 없음"); return; }
    try {
      const res = await api.stocks.bulkImport(items);
      setBulkMsg(`${res.created}개 추가, ${res.skipped}개 중복 건너뜀`);
      setBulkText("");
      load();
    } catch (err: unknown) {
      setBulkMsg(err instanceof Error ? err.message : "일괄 등록 실패");
    }
  }

  const active   = stocks.filter((s) => s.is_active).length;
  const inactive = stocks.filter((s) => !s.is_active).length;

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">후보 종목 풀 관리</h1>
        <div className="flex items-center gap-3 text-sm text-gray-400">
          <span>활성 <b className="text-white">{active}</b></span>
          <span>비활성 <b className="text-gray-500">{inactive}</b></span>
          <label className="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            <span>비활성 포함</span>
          </label>
        </div>
      </div>

      {msg && <p className="text-sm mb-4 text-green-400">{msg}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 종목 테이블 */}
        <div className="lg:col-span-2 bg-gray-800 rounded-2xl overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-gray-400">로딩 중...</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-gray-400 border-b border-gray-700">
                <tr>
                  <th className="text-left p-4">코드</th>
                  <th className="text-left p-4">종목명</th>
                  <th className="text-left p-4">시장</th>
                  <th className="text-left p-4">섹터</th>
                  <th className="text-center p-4">상태</th>
                  <th className="text-center p-4">삭제</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s) => (
                  <tr key={s.stock_id}
                    className={`border-b border-gray-700/50 ${!s.is_active ? "opacity-40" : ""}`}>
                    <td className="p-4 font-mono">{s.stock_code}</td>
                    <td className="p-4 font-medium">{s.stock_name}</td>
                    <td className="p-4 text-gray-400 text-xs">{s.market ?? "-"}</td>
                    <td className="p-4 text-gray-400 text-xs">{s.sector ?? "-"}</td>
                    <td className="p-4 text-center">
                      <button onClick={() => toggleActive(s)}
                        className={`text-xs px-2 py-1 rounded ${
                          s.is_active
                            ? "bg-green-900 text-green-300 hover:bg-green-800"
                            : "bg-gray-700 text-gray-400 hover:bg-gray-600"
                        }`}>
                        {s.is_active ? "활성" : "비활성"}
                      </button>
                    </td>
                    <td className="p-4 text-center">
                      <button onClick={() => deleteStock(s)}
                        className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded hover:bg-red-900/30">
                        삭제
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 사이드 패널 */}
        <div className="flex flex-col gap-4">
          {/* 검색 추가 */}
          <div className="bg-gray-800 rounded-2xl p-5">
            <h3 className="font-semibold text-gray-300 mb-3">종목 추가</h3>
            <StockSearch
              onSelect={handleSelect}
              placeholder="종목명 또는 코드 검색"
            />

            {/* 알려진 종목: 확인 카드 */}
            {selected && !isUnknown && (
              <div className="mt-3 bg-gray-700/50 rounded-lg p-3">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="font-medium">{selected.name}</div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {selected.code} · {selected.market} · {selected.sector}
                    </div>
                  </div>
                  <button onClick={() => setSelected(null)}
                    className="text-gray-500 hover:text-white text-xs">✕</button>
                </div>
                <button onClick={addStock} disabled={addLoading}
                  className="mt-3 w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 text-sm font-medium disabled:opacity-50">
                  {addLoading ? "추가 중..." : "풀에 추가"}
                </button>
              </div>
            )}

            {/* 미지 종목: 정보 입력 폼 */}
            {selected && isUnknown && (
              <div className="mt-3 bg-yellow-900/20 border border-yellow-700/40 rounded-lg p-3 flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-yellow-400 font-medium">
                    코드 {selected.code} — 종목 정보 입력
                  </span>
                  <button onClick={() => setSelected(null)}
                    className="text-gray-500 hover:text-white text-xs">✕</button>
                </div>
                <input type="text" placeholder="종목명 *" required
                  value={editForm.name}
                  onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                  className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
                <select value={editForm.market}
                  onChange={(e) => setEditForm((f) => ({ ...f, market: e.target.value }))}
                  className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="KOSPI">KOSPI</option>
                  <option value="KOSDAQ">KOSDAQ</option>
                </select>
                <input type="text" placeholder="섹터 (선택)"
                  value={editForm.sector}
                  onChange={(e) => setEditForm((f) => ({ ...f, sector: e.target.value }))}
                  className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
                {msg && <p className="text-xs text-red-400">{msg}</p>}
                <button onClick={addStock} disabled={addLoading || !editForm.name.trim()}
                  className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 text-sm font-medium disabled:opacity-50">
                  {addLoading ? "추가 중..." : "풀에 추가"}
                </button>
              </div>
            )}
          </div>

          {/* 일괄 등록 */}
          <div className="bg-gray-800 rounded-2xl p-5">
            <h3 className="font-semibold text-gray-300 mb-1">일괄 등록</h3>
            <p className="text-xs text-gray-500 mb-2">
              형식: 종목코드,종목명,시장,섹터 (줄 구분)
            </p>
            <textarea
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={BULK_PLACEHOLDER}
              rows={5}
              className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-xs font-mono outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            />
            {bulkMsg && (
              <p className={`text-xs mt-1 ${bulkMsg.includes("추가") ? "text-green-400" : "text-red-400"}`}>
                {bulkMsg}
              </p>
            )}
            <button onClick={bulkImport}
              className="mt-2 w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 text-sm font-medium">
              일괄 등록
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
