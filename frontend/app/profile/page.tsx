"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, User, BrokerAccount, getToken } from "@/lib/api";
import Badge from "@/components/Badge";

export default function ProfilePage() {
  const router = useRouter();
  const [user, setUser]         = useState<User | null>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [chatId, setChatId]     = useState("");
  const [chatMsg, setChatMsg]   = useState("");

  // 계좌 등록 폼
  const [acctForm, setAcctForm] = useState({
    account_no: "", api_key: "", api_secret: "", account_type: "REAL",
  });
  const [acctMsg, setAcctMsg] = useState("");
  const [acctLoading, setAcctLoading] = useState(false);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    const me = await api.auth.me();
    setUser(me);
    setChatId(me.telegram_chat_id ?? "");
    const accts = await api.users.listBrokerAccounts(me.user_id);
    setAccounts(accts);
  }

  async function saveTelegram(e: React.FormEvent) {
    e.preventDefault();
    setChatMsg("");
    try {
      const updated = await api.users.updateTelegram(chatId || null);
      setUser(updated);
      setChatMsg("저장됐습니다.");
    } catch (err: unknown) {
      setChatMsg(err instanceof Error ? err.message : "저장 실패");
    }
  }

  async function addAccount(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    setAcctLoading(true); setAcctMsg("");
    try {
      const a = await api.users.addBrokerAccount(user.user_id, acctForm);
      setAccounts((prev) => [...prev, a]);
      setAcctForm({ account_no: "", api_key: "", api_secret: "", account_type: "REAL" });
      setAcctMsg("계좌가 등록됐습니다.");
    } catch (err: unknown) {
      setAcctMsg(err instanceof Error ? err.message : "등록 실패");
    } finally {
      setAcctLoading(false);
    }
  }

  if (!user) return <div className="flex items-center justify-center h-64 text-gray-400">로딩 중...</div>;

  return (
    <div className="max-w-2xl mx-auto p-6 flex flex-col gap-6">
      <h1 className="text-2xl font-bold">프로필</h1>

      {/* 기본 정보 */}
      <div className="bg-gray-800 rounded-2xl p-6">
        <h2 className="font-semibold mb-4 text-gray-300">기본 정보</h2>
        <div className="flex flex-col gap-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-400">아이디</span>
            <span>{user.username}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">이메일</span>
            <span>{user.email}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">역할</span>
            <Badge value={user.role} />
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">상태</span>
            <Badge value={user.is_active ? "ACTIVE" : "INACTIVE"} />
          </div>
        </div>
      </div>

      {/* 텔레그램 */}
      <div className="bg-gray-800 rounded-2xl p-6">
        <h2 className="font-semibold mb-1 text-gray-300">텔레그램 알림</h2>
        <p className="text-xs text-gray-500 mb-4">
          @BotFather에서 발급받은 봇과 대화 후
          t.me/userinfobot 에서 본인 chat_id 확인
        </p>
        <form onSubmit={saveTelegram} className="flex gap-2">
          <input
            type="text"
            placeholder="chat_id (예: 123456789)"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            className="flex-1 bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button type="submit"
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium">
            저장
          </button>
          {chatId && (
            <button type="button" onClick={() => { setChatId(""); api.users.updateTelegram(null); }}
              className="text-gray-400 hover:text-white px-3 py-2 rounded-lg text-sm hover:bg-gray-700">
              해제
            </button>
          )}
        </form>
        {chatMsg && <p className="text-xs mt-2 text-green-400">{chatMsg}</p>}
      </div>

      {/* 브로커 계좌 */}
      <div className="bg-gray-800 rounded-2xl p-6">
        <h2 className="font-semibold mb-4 text-gray-300">브로커 계좌</h2>

        {accounts.length > 0 ? (
          <div className="flex flex-col gap-2 mb-6">
            {accounts.map((a) => (
              <div key={a.account_id} className="flex items-center justify-between bg-gray-700/50 rounded-lg px-4 py-3 text-sm">
                <div>
                  <span className="font-medium">{a.account_no}</span>
                  <span className="ml-2 text-gray-400">{a.broker} · {a.account_type}</span>
                </div>
                <Badge value={a.is_active ? "ACTIVE" : "INACTIVE"} />
              </div>
            ))}
          </div>
        ) : (
          <p className="text-gray-500 text-sm mb-4">등록된 계좌 없음</p>
        )}

        <h3 className="text-sm font-medium text-gray-400 mb-3">새 계좌 등록 (KIS)</h3>
        <form onSubmit={addAccount} className="flex flex-col gap-3">
          <input type="text" placeholder="계좌번호 (예: 00000000-01)"
            value={acctForm.account_no}
            onChange={(e) => setAcctForm((f) => ({ ...f, account_no: e.target.value }))}
            className="bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            required />
          <input type="text" placeholder="API Key"
            value={acctForm.api_key}
            onChange={(e) => setAcctForm((f) => ({ ...f, api_key: e.target.value }))}
            className="bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            required />
          <input type="password" placeholder="API Secret"
            value={acctForm.api_secret}
            onChange={(e) => setAcctForm((f) => ({ ...f, api_secret: e.target.value }))}
            className="bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            required />
          <select value={acctForm.account_type}
            onChange={(e) => setAcctForm((f) => ({ ...f, account_type: e.target.value }))}
            className="bg-gray-700 text-white rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500">
            <option value="REAL">실전계좌</option>
            <option value="PAPER">모의계좌</option>
          </select>
          {acctMsg && (
            <p className={`text-xs ${acctMsg.includes("등록") ? "text-green-400" : "text-red-400"}`}>
              {acctMsg}
            </p>
          )}
          <button type="submit" disabled={acctLoading}
            className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 text-sm font-medium disabled:opacity-50">
            {acctLoading ? "등록 중..." : "계좌 등록"}
          </button>
        </form>
      </div>
    </div>
  );
}
