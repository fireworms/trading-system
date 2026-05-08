"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, CandidateStock, getToken } from "@/lib/api";

const BULK_PLACEHOLDER = `005930,삼성전자,KOSPI,반도체
000660,SK하이닉스,KOSPI,반도체
035420,NAVER,KOSPI,인터넷`;

export default function AdminStocksPage() {
  const router = useRouter();
  const [stocks, setStocks]     = useState<CandidateStock[]>([]);
  const [showAll, setShowAll]   = useState(false);
  const [loading, setLoading]   = useState(true);
  const [msg, setMsg]           = useState("");

  // 단일 추가 폼
  const [form, setForm] = useState({ stock_code: "", stock_name: "", market: "KOSPI", sector: "", notes: "" });
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
      const data = await api.stocks.list(!showAll);
      setStocks(data);
    } finally {
      setLoading(false);
    }
  }

  async function toggleActive(s: CandidateStock) {
    setMsg("");
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

  async function addStock(e: React.FormEvent) {
    e.preventDefault();
    setAddLoading(true); setMsg("");
    try {
      const created = await api.stocks.create({
        ...form,
        notes: form.notes || null,
      } as Parameters<typeof api.stocks.create>[0]);
      setStocks((prev) => [...prev, created]);
      setForm({ stock_code: "", stock_name: "", market: "KOSPI", sector: "", notes: "" });
      setMsg("종목 추가 완료");
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
    if (items.length === 0) { setBulkMsg("입력값 없음"); return; }
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
                  <th className="text-left p-4">종목코드</th>
                  <th className="text-left p-4">종목명</th>
                  <th className="text-left p-4">시장</th>
                  <th className="text-left p-4">섹터</th>
                  <th className="text-center p-4">상태</th>
                  <th className="text-center p-4">액션</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s) => (
                  <tr key={s.stock_id}
                    className={`border-b border-gray-700/50 ${!s.is_active ? "opacity-40" : ""}`}>
                    <td className="p-4 font-mono font-medium">{s.stock_code}</td>
                    <td className="p-4">{s.stock_name}</td>
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
          {/* 단일 추가 */}
          <div className="bg-gray-800 rounded-2xl p-5">
            <h3 className="font-semibold text-gray-300 mb-3">종목 추가</h3>
            <form onSubmit={addStock} className="flex flex-col gap-2">
              {[
                { key: "stock_code", placeholder: "종목코드 (예: 005930)", required: true },
                { key: "stock_name", placeholder: "종목명 (예: 삼성전자)", required: true },
                { key: "sector",     placeholder: "섹터 (예: 반도체)",     required: false },
                { key: "notes",      placeholder: "메모 (선택)",           required: false },
              ].map(({ key, placeholder, required }) => (
                <input key={key} type="text" placeholder={placeholder} required={required}
                  value={form[key as keyof typeof form]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
              ))}
              <select value={form.market}
                onChange={(e) => setForm((f) => ({ ...f, market: e.target.value }))}
                className="bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
                <option value="KOSPI">KOSPI</option>
                <option value="KOSDAQ">KOSDAQ</option>
              </select>
              <button type="submit" disabled={addLoading}
                className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 text-sm font-medium disabled:opacity-50">
                {addLoading ? "추가 중..." : "추가"}
              </button>
            </form>
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
