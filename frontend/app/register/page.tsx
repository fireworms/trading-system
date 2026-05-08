"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm]     = useState({ username: "", email: "", password: "", confirm: "" });
  const [error, setError]   = useState("");
  const [loading, setLoading] = useState(false);

  function set(k: string, v: string) { setForm((f) => ({ ...f, [k]: v })); }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (form.password !== form.confirm) { setError("비밀번호가 일치하지 않습니다."); return; }
    if (form.password.length < 8) { setError("비밀번호는 8자 이상이어야 합니다."); return; }
    setLoading(true); setError("");
    try {
      await api.auth.register(form.username, form.email, form.password);
      await api.auth.login(form.username, form.password);
      router.push("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "회원가입 실패");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center p-4">
      <div className="bg-gray-800 rounded-2xl p-8 w-full max-w-sm">
        <h1 className="text-xl font-bold text-white mb-6">회원가입</h1>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {[
            { key: "username", label: "아이디", type: "text" },
            { key: "email",    label: "이메일",  type: "email" },
            { key: "password", label: "비밀번호 (8자 이상)", type: "password" },
            { key: "confirm",  label: "비밀번호 확인", type: "password" },
          ].map(({ key, label, type }) => (
            <div key={key} className="flex flex-col gap-1">
              <label className="text-xs text-gray-400">{label}</label>
              <input
                type={type}
                value={form[key as keyof typeof form]}
                onChange={(e) => set(key, e.target.value)}
                className="bg-gray-700 text-white rounded-lg px-4 py-2 outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>
          ))}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2 font-medium disabled:opacity-50 mt-1"
          >
            {loading ? "처리 중..." : "가입하기"}
          </button>
        </form>
        <p className="text-center text-sm text-gray-400 mt-4">
          이미 계정이 있으신가요?{" "}
          <Link href="/login" className="text-blue-400 hover:underline">로그인</Link>
        </p>
      </div>
    </div>
  );
}
