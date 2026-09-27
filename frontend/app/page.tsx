"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  TrendingUp,
  Layers,
  BookOpen,
  ArrowRight,
  Sparkles,
  CheckCircle2,
  RefreshCw,
  Search,
  Globe,
  MessageSquare,
  Activity,
  AlertCircle,
} from "lucide-react";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { TrendingSection } from "@/components/topic/TrendingSection";
import { TopicGrid } from "@/components/topic/TopicGrid";
import { TopicSearch } from "@/components/topic/TopicSearch";
import { Spinner } from "@/components/common/Spinner";
import { getTopics, getTrendingTopics, submitTopicAndAnalyze } from "@/lib/api";
import { Topic } from "@/lib/types";

const SUGGESTED_QUERIES = [
  "Artificial Intelligence in Healthcare",
  "Commercial Autonomous Coding Agents",
  "Global Nuclear SMR Deployment",
  "Central Bank Digital Currencies",
  "Commercial Electric Vehicle Infrastructure",
];

export default function HomePage() {
  const router = useRouter();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [trendingTopics, setTrendingTopics] = useState<Topic[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [analysisStatus, setAnalysisStatus] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const loadData = async () => {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const [allTopics, trending] = await Promise.all([
        getTopics(),
        getTrendingTopics(4, 0.0),
      ]);
      if (allTopics && allTopics.length > 0) {
        setTopics(allTopics);
        setTrendingTopics(trending && trending.length > 0 ? trending : allTopics.slice(0, 4));
      }
    } catch (err: any) {
      console.warn("Could not load topics from API:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleSearchSubmit = async (queryToSubmit: string) => {
    const trimmed = queryToSubmit.trim();
    if (!trimmed || isSubmitting) return;

    setIsSubmitting(true);
    setErrorMessage(null);
    setAnalysisStatus("Submitting topic and collecting 100+ source items across Google News & Reddit...");

    try {
      setAnalysisStatus("Querying news feeds and discussion threads across sources...");
      const res = await submitTopicAndAnalyze(trimmed, 100);
      const slug = res?.topic?.slug;

      setAnalysisStatus("Deduplicating, clustering viewpoints, and synthesizing perspectives...");

      if (slug) {
        router.push(`/topics/${slug}`);
      } else {
        await loadData();
        setIsSubmitting(false);
        setAnalysisStatus(null);
      }
    } catch (err: any) {
      console.error("Auto analysis failed:", err);
      setErrorMessage(err.message || "Failed to complete automatic topic analysis.");
      setIsSubmitting(false);
      setAnalysisStatus(null);
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-[#090c10] text-[#f0f6fc]">
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 md:py-12 space-y-12 md:space-y-16 flex-1 w-full">
        {/* 1. PROMINENT TOPIC SEARCH & AUTOMATIC ANALYSIS HERO */}
        <section id="search" className="max-w-4xl mx-auto text-center space-y-6 pt-2 pb-4">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.06] border border-white/10 text-xs font-mono text-slate-300 uppercase tracking-wider">
              <BookOpen className="w-3.5 h-3.5 text-slate-400" />
              <span>Autonomous Public Discourse Intelligence</span>
            </div>

            <h1 className="font-headline text-3xl sm:text-5xl md:text-6xl font-bold tracking-tight text-slate-100 leading-[1.12]">
              What Is the Public Saying?
            </h1>

            <p className="font-serif-body text-base sm:text-lg text-slate-300 max-w-2xl mx-auto leading-relaxed">
              Enter any news topic, controversy, or inquiry. Vantage News crawls{" "}
              <strong className="text-slate-100 font-sans font-semibold">Google News</strong> and{" "}
              <strong className="text-slate-100 font-sans font-semibold">Reddit</strong> (targeting 100+ items), eliminates noise, and synthesizes source-grounded perspectives.
            </p>
          </div>

          {/* Search Bar Form */}
          <div className="max-w-2xl mx-auto pt-2">
            <TopicSearch
              value={searchQuery}
              onChange={setSearchQuery}
              onSubmit={handleSearchSubmit}
              isSubmitting={isSubmitting}
              placeholder="e.g. Artificial Intelligence in Healthcare..."
            />

            {/* Suggested Trending Topic Tags */}
            <div className="flex flex-wrap items-center justify-center gap-2 pt-3.5 text-xs text-slate-400 font-mono">
              <span className="text-slate-500 uppercase text-[10px] tracking-wider">Inquire:</span>
              {SUGGESTED_QUERIES.map((q, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => {
                    setSearchQuery(q);
                    handleSearchSubmit(q);
                  }}
                  disabled={isSubmitting}
                  className="px-2.5 py-1 rounded-md bg-white/[0.04] hover:bg-white/[0.1] border border-white/[0.08] hover:border-white/20 text-slate-300 hover:text-white transition-all text-[11px]"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>

          {/* Live Automatic Progress Box */}
          {isSubmitting && (
            <div className="max-w-xl mx-auto p-5 rounded-xl bg-[#0d1117] border border-amber-500/30 text-left space-y-3 shadow-2xl animate-pulse">
              <div className="flex items-center gap-2.5 text-amber-400 font-mono text-xs font-bold uppercase tracking-wider">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping" />
                <span>Automated Discourse Pipeline Running</span>
              </div>
              <p className="text-sm font-serif-body text-slate-200">
                {analysisStatus}
              </p>
              <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                <div className="h-full bg-amber-400 rounded-full w-2/3 animate-indeterminate" />
              </div>
              <p className="text-[11px] font-mono text-slate-400">
                Aiming for 100+ verified discourse items · Grounded citations & neutral synthesis
              </p>
            </div>
          )}

          {errorMessage && (
            <div className="max-w-xl mx-auto p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-mono flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}
        </section>

        {/* 2. BROWSEABLE EDITORIAL FEED */}
        {isLoading ? (
          <div className="py-12 flex justify-center">
            <Spinner size="lg" label="Loading public discourse chronicle..." />
          </div>
        ) : (
          <div className="space-y-12 md:space-y-16">
            {/* Front Page Leading Stories */}
            {trendingTopics.length > 0 && (
              <TrendingSection trendingTopics={trendingTopics} />
            )}

            {/* Discourse Archive & Recent Inquiries */}
            <TopicGrid topics={topics} />
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}

