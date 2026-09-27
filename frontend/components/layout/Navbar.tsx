"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import {
  Compass,
  TrendingUp,
  Sparkles,
  Search,
  Layers,
  BookOpen,
  Globe,
} from "lucide-react";

interface NavbarProps {
  onOpenCreateModal?: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({ onOpenCreateModal }) => {
  const [currentDate, setCurrentDate] = useState<string>("");

  useEffect(() => {
    const now = new Date();
    setCurrentDate(
      now.toLocaleDateString("en-US", {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    );
  }, []);

  return (
    <header className="w-full border-b border-white/[0.1] bg-[#090c10]/95 backdrop-blur-md sticky top-0 z-40">
      {/* Top Editorial Dateline Strip */}
      <div className="border-b border-white/[0.06] py-1 px-4 sm:px-6 lg:px-8 text-[11px] text-slate-400 font-mono flex flex-wrap items-center justify-between gap-2 max-w-7xl mx-auto">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-slate-300">{currentDate || "Daily Public Discourse Edition"}</span>
          <span className="text-slate-600 hidden sm:inline">|</span>
          <span className="hidden sm:inline text-slate-400">Vol. III · No. 284</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-emerald-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span>Multi-Source Ingestion: Active</span>
          </span>
          <span className="text-slate-500 hidden md:inline">Google News RSS & Reddit API</span>
        </div>
      </div>

      {/* Main Newspaper Masthead Header */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between gap-4">
        {/* Masthead Branding */}
        <Link href="/" className="flex items-center gap-3 group">
          <div className="w-9 h-9 rounded-lg bg-white/[0.06] border border-white/20 flex items-center justify-center text-slate-200 group-hover:border-white/40 transition-colors">
            <BookOpen className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-baseline gap-2">
              <span className="font-masthead text-xl sm:text-2xl font-bold tracking-wider text-slate-100 uppercase">
                Vantage News
              </span>
              <span className="text-[10px] uppercase font-mono tracking-widest px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-300 border border-white/10 hidden xs:inline">
                Discourse
              </span>
            </div>
            <p className="text-[11px] font-serif-body text-slate-400 italic hidden sm:block">
              Multi-Perspective Public Discourse Intelligence
            </p>
          </div>
        </Link>

        {/* Clean Editorial Navigation */}
        <nav className="flex items-center gap-1.5">
          <Link
            href="/#search"
            className="px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-md transition-colors flex items-center gap-1.5 border border-transparent hover:border-white/10"
          >
            <Search className="w-3.5 h-3.5 text-slate-400" />
            <span>Topic Search</span>
          </Link>
          <Link
            href="/#trending"
            className="px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-md transition-colors flex items-center gap-1.5 border border-transparent hover:border-white/10"
          >
            <TrendingUp className="w-3.5 h-3.5 text-amber-400/90" />
            <span>Trending Feed</span>
          </Link>
          <Link
            href="/#archive"
            className="px-3 py-1.5 text-xs font-medium text-slate-300 hover:text-white hover:bg-white/[0.06] rounded-md transition-colors flex items-center gap-1.5 border border-transparent hover:border-white/10"
          >
            <Layers className="w-3.5 h-3.5 text-slate-400" />
            <span>Discourse Archive</span>
          </Link>
        </nav>
      </div>
    </header>
  );
};

