"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api, User, clearToken } from "@/lib/api";

const links = [
  { href: "/",           label: "대시보드" },
  { href: "/positions",  label: "포지션" },
  { href: "/stats",      label: "수익통계" },
  { href: "/watchlist",  label: "관심종목" },
  { href: "/research",   label: "AI 리서치" },
  { href: "/news",       label: "뉴스감시" },
  { href: "/profile",    label: "프로필" },
];

const adminLinks = [
  { href: "/admin/users", label: "유저 관리" },
];

// 서버 셸 권한을 가진 에이전트 — SUPER_ADMIN만 (백엔드 trading-agent도 같은 기준)
const superAdminLinks = [
  { href: "/agent", label: "에이전트" },
];

export default function Nav() {
  const pathname  = usePathname();
  const router    = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api.auth.me().then(setUser).catch(() => {});
  }, []);

  function logout() {
    clearToken();
    router.push("/login");
  }

  const isAdmin = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN";
  const navLinks = [
    ...links,
    ...(isAdmin ? adminLinks : []),
    ...(user?.role === "SUPER_ADMIN" ? superAdminLinks : []),
  ];

  return (
    <nav className="bg-gray-800 border-b border-gray-700 px-3 sm:px-6 py-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-3 sm:gap-6 min-w-0">
        <span className="font-bold text-white text-sm shrink-0">📈<span className="hidden sm:inline"> Trading</span></span>
        <div className="flex gap-1 overflow-x-auto">
          {navLinks.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`px-3 py-1.5 rounded-lg text-sm whitespace-nowrap transition-colors ${
                pathname === l.href
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-white hover:bg-gray-700"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        {user && (
          <span className="hidden sm:inline text-xs text-gray-400">
            {user.username}
            <span className="ml-1 px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">
              {user.role}
            </span>
          </span>
        )}
        <button
          onClick={logout}
          className="text-xs text-gray-400 hover:text-white px-2 py-1 rounded hover:bg-gray-700"
        >
          로그아웃
        </button>
      </div>
    </nav>
  );
}
