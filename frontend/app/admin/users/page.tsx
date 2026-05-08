"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, User, UserRole, BrokerAccount, getToken } from "@/lib/api";
import Badge from "@/components/Badge";

const ROLES: UserRole[] = ["SUPER_ADMIN", "ADMIN", "TRADER", "VIEWER"];

export default function AdminUsersPage() {
  const router = useRouter();
  const [me, setMe]           = useState<User | null>(null);
  const [users, setUsers]     = useState<User[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg]         = useState("");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function load() {
    try {
      const [meData, usersData] = await Promise.all([api.auth.me(), api.users.list()]);
      if (meData.role !== "ADMIN" && meData.role !== "SUPER_ADMIN") {
        router.push("/"); return;
      }
      setMe(meData);
      setUsers(usersData);
    } finally {
      setLoading(false);
    }
  }

  async function selectUser(userId: string) {
    setSelected(userId);
    setAccounts([]);
    const accts = await api.users.listBrokerAccounts(userId);
    setAccounts(accts);
  }

  async function toggleActive(u: User) {
    setMsg("");
    try {
      const updated = await api.users.update(u.user_id, { is_active: !u.is_active });
      setUsers((prev) => prev.map((x) => x.user_id === u.user_id ? updated : x));
      setMsg(`${u.username} 상태 변경 완료`);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "변경 실패");
    }
  }

  async function changeRole(u: User, role: UserRole) {
    setMsg("");
    try {
      const updated = await api.users.update(u.user_id, { role });
      setUsers((prev) => prev.map((x) => x.user_id === u.user_id ? updated : x));
      setMsg(`${u.username} 역할 변경 완료`);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "변경 실패");
    }
  }

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">로딩 중...</div>;

  const selectedUser = users.find((u) => u.user_id === selected);

  return (
    <div className="max-w-5xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-6">유저 관리</h1>
      {msg && <p className="text-green-400 text-sm mb-4">{msg}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 유저 목록 */}
        <div className="lg:col-span-2 bg-gray-800 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-4">유저</th>
                <th className="text-left p-4">역할</th>
                <th className="text-left p-4">상태</th>
                <th className="text-left p-4">텔레그램</th>
                <th className="text-left p-4">액션</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id}
                  className={`border-b border-gray-700/50 cursor-pointer transition-colors ${
                    selected === u.user_id ? "bg-blue-900/30" : "hover:bg-gray-700/30"
                  }`}
                  onClick={() => selectUser(u.user_id)}>
                  <td className="p-4">
                    <div className="font-medium">{u.username}</div>
                    <div className="text-xs text-gray-400">{u.email}</div>
                  </td>
                  <td className="p-4">
                    {me?.role === "SUPER_ADMIN" && u.user_id !== me.user_id ? (
                      <select value={u.role}
                        onChange={(e) => { e.stopPropagation(); changeRole(u, e.target.value as UserRole); }}
                        onClick={(e) => e.stopPropagation()}
                        className="bg-gray-700 text-white rounded px-2 py-1 text-xs outline-none">
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    ) : (
                      <Badge value={u.role} />
                    )}
                  </td>
                  <td className="p-4">
                    <Badge value={u.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td className="p-4 text-xs text-gray-400">
                    {u.telegram_chat_id ? `✓ ${u.telegram_chat_id}` : "-"}
                  </td>
                  <td className="p-4">
                    {u.user_id !== me?.user_id && (
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleActive(u); }}
                        className={`text-xs px-2 py-1 rounded ${
                          u.is_active
                            ? "bg-red-900 text-red-300 hover:bg-red-800"
                            : "bg-green-900 text-green-300 hover:bg-green-800"
                        }`}>
                        {u.is_active ? "비활성화" : "활성화"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 선택된 유저 상세 */}
        <div className="bg-gray-800 rounded-2xl p-5">
          {selectedUser ? (
            <>
              <h3 className="font-semibold mb-4 text-gray-300">
                {selectedUser.username} 계좌 정보
              </h3>
              {accounts.length === 0 ? (
                <p className="text-gray-500 text-sm">등록된 계좌 없음</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {accounts.map((a) => (
                    <div key={a.account_id} className="bg-gray-700/50 rounded-lg p-3 text-sm">
                      <div className="font-medium">{a.account_no}</div>
                      <div className="text-xs text-gray-400 mt-1">
                        {a.broker} · {a.account_type}
                      </div>
                      <div className="mt-1">
                        <Badge value={a.is_active ? "ACTIVE" : "INACTIVE"} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="mt-4 pt-4 border-t border-gray-700 text-xs text-gray-500">
                <div>가입일: {new Date(selectedUser.created_at).toLocaleDateString("ko-KR")}</div>
                <div className="mt-1 break-all">ID: {selectedUser.user_id.slice(0, 8)}...</div>
              </div>
            </>
          ) : (
            <p className="text-gray-500 text-sm">유저를 선택하세요</p>
          )}
        </div>
      </div>
    </div>
  );
}
