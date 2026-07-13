"use client";

import {
  BookOpen,
  BriefcaseBusiness,
  ChartLine,
  FlaskConical,
  History,
  LayoutDashboard,
  ListFilter,
  Menu,
  Settings2,
  ShieldCheck,
  WalletCards,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { DataModeBadge } from "@/components/data-mode-badge";
import { apiFetch, type Meta } from "@/lib/api";
import { cn } from "@/lib/utils";

const CORE_NAVIGATION = [
  { href: "/", label: "대시보드", icon: LayoutDashboard },
  { href: "/screener", label: "추세 스크리너", icon: ListFilter },
  { href: "/methodology", label: "방법론", icon: BookOpen },
];

const DEMO_NAVIGATION = [
  { href: "/backtests", label: "백테스트", icon: FlaskConical },
  { href: "/portfolio", label: "모의 포트폴리오", icon: BriefcaseBusiness },
];

const LOCAL_NAVIGATION = [
  ...CORE_NAVIGATION.slice(0, 2),
  { href: "/quality", label: "데이터 품질", icon: ShieldCheck },
  { href: "/replays", label: "전략 실험실", icon: ChartLine },
  { href: "/candidate-history", label: "후보 이력", icon: History },
  { href: "/forward", label: "포워드 포트폴리오", icon: WalletCards },
  CORE_NAVIGATION[2],
  { href: "/admin", label: "관리", icon: Settings2 },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    apiFetch<Meta>("/meta")
      .then(setMeta)
      .catch(() => setMeta(null));
  }, [pathname]);

  const navigation =
    meta?.app_mode === "public_demo"
      ? [...CORE_NAVIGATION.slice(0, 2), ...DEMO_NAVIGATION, CORE_NAVIGATION[2]]
      : meta?.app_mode === "local_research"
        ? LOCAL_NAVIGATION
        : CORE_NAVIGATION;

  return (
    <div className="app-frame">
      <aside
        className={cn("sidebar", open && "sidebar-open")}
        aria-label="주요 메뉴"
      >
        <div className="brand-block">
          <Link href="/" className="brand" onClick={() => setOpen(false)}>
            <span className="brand-mark" aria-hidden="true">
              Q
            </span>
            <span>
              <strong>Quant Trend Lab</strong>
              <small>설명 가능한 추세 연구</small>
            </span>
          </Link>
          <button
            className="icon-button mobile-close"
            type="button"
            aria-label="메뉴 닫기"
            title="메뉴 닫기"
            onClick={() => setOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <nav className="nav-list">
          {navigation.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn("nav-item", active && "nav-item-active")}
                onClick={() => setOpen(false)}
              >
                <Icon size={18} strokeWidth={1.9} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="environment-dot" aria-hidden="true" />
          <div>
            <strong>
              {meta?.data_source === "YFINANCE"
                ? "로컬 실데이터 연구"
                : "가상 데이터 모드"}
            </strong>
            <small>{meta?.data_version ?? "연결 확인 중"}</small>
          </div>
        </div>
      </aside>
      {open && (
        <button
          className="sidebar-backdrop"
          aria-hidden="true"
          tabIndex={-1}
          onClick={() => setOpen(false)}
        />
      )}
      <div className="main-column">
        <header className="mobile-header">
          <button
            className="icon-button"
            type="button"
            aria-label="메뉴 열기"
            title="메뉴 열기"
            onClick={() => setOpen(true)}
          >
            <Menu size={21} />
          </button>
          <strong>Quant Trend Lab</strong>
          <DataModeBadge source={meta?.data_source ?? "SYNTHETIC"} />
        </header>
        <main className="page-shell">{children}</main>
      </div>
    </div>
  );
}
