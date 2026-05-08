"use client";

import { usePathname } from "next/navigation";
import Nav from "./Nav";

// 로그인/회원가입 페이지에서는 Nav 숨김
const HIDE_NAV = ["/login", "/register"];

export default function NavWrapper() {
  const pathname = usePathname();
  if (HIDE_NAV.includes(pathname)) return null;
  return <Nav />;
}
