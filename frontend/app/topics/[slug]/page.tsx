"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  BookOpen,
  Clock,
  RefreshCw,
  Layers,
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Globe,
  MessageSquare,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { PerspectiveCard } from "@/components/perspective/PerspectiveCard";
import { PerspectiveFilter } from "@/components/perspective/PerspectiveFilter";
import { ShareBarChart } from "@/components/charts/ShareBarChart";
import { SourceBreakdown } from "@/components/source/SourceBreakdown";
import { Spinner } from "@/components/common/Spinner";
import { getTopicBySlug, getTopicIngestionStatus, triggerFullPipeline } from "@/lib/api";
import { Perspective, Topic } from "@/lib/types";
import { classifyStance, formatTimeAgo, normalizeEvidenceSource } from "@/lib/utils";

export default function TopicDetailPage() {
  const params = useParams();
  const slug = params?.slug as string;
  const router = useRouter();

  const [topic, setTopic] = useState<Topic | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Automatic Pipeline State
  const [isRunningPipeline, setIsRunningPipeline] = useState(false);
  const [pipelineStatus, setPipelineStatus] = useState<string | null>(null);
  const hasAutoStartedRef = useRef(false);

  // Filter States
  const [selectedStance, setSelectedStance] = useState<string>("all");
  const [selectedSource, setSelectedSource] = useState<string>("all");
  const [selectedPerspectiveId, setSelectedPerspectiveId] = useState<number | null>(null);

  const fetchTopicData = async (shouldAutoAnalyze = false) => {
    if (!slug) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getTopicBySlug(slug);
      setTopic(data);

      // If topic has no perspectives, has not been clustered yet, and has not auto-started yet, trigger automatic analysis
      if (
        shouldAutoAnalyze &&
        (!data.perspectives || data.perspectives.length === 0) &&
        !data.last_clustered_at &&
        data.source_coverage?.pipeline_status !== "complete" &&
        !hasAutoStartedRef.current
      ) {
        hasAutoStartedRef.current = true;
        handleRunAutoAnalysis(data);
      }
    } catch (err: any) {
      console.error("Could not fetch topic from backend:", err);
      setError(err.message || "Failed to load topic");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTopicData(true);
  }, [slug]);

  const xIngestionStatus = topic?.source_coverage?.x_ingestion_status;
  const xAnalysisStatus = topic?.source_coverage?.x_analysis_status;

  useEffect(() => {
    if (!slug) return;
    const ingestionActive = ["pending", "running", "ready", "merging"].includes(xIngestionStatus || "");
    const analysisActive = ["pending", "running"].includes(xAnalysisStatus || "");
    if (!ingestionActive && !analysisActive) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const pollStatus = async () => {
      try {
        const progress = await getTopicIngestionStatus(slug);
        if (cancelled) return;

        const ingestionFinished = ["complete", "failed"].includes(progress.x_status);
        const analysisFinished = ["complete", "failed", "skipped"].includes(progress.analysis_status);
        if (ingestionFinished && analysisFinished) {
          const refreshed = await getTopicBySlug(slug);
          if (!cancelled) {
            setTopic({
              ...refreshed,
              source_coverage: {
                ...refreshed.source_coverage,
                x_ingestion_status: progress.x_status,
                x_analysis_status: progress.analysis_status,
                x_ingestion_new_count: progress.x_new_count,
                x_ingestion_baseline_coverage: progress.x_baseline_coverage,
                x_ingestion_message: progress.message,
                x_ingestion_posts: progress.x_posts,
              },
            });
          }
          return;
        }

        // Keep the loading state active while updating the visible X count and
        // the newest staged posts on each poll.
        setTopic((current) => current ? {
          ...current,
          source_coverage: {
            ...current.source_coverage,
            x_ingestion_new_count: progress.x_new_count,
            x_ingestion_baseline_coverage: progress.x_baseline_coverage,
            x_ingestion_message: progress.message,
            x_ingestion_posts: progress.x_posts,
          },
        } : current);
      } catch (pollError) {
        console.warn("Could not refresh X/Twitter ingestion progress:", pollError);
      }

      if (!cancelled) timer = setTimeout(pollStatus, 2500);
    };

    void pollStatus();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [slug, xIngestionStatus, xAnalysisStatus]);

  const handleRunAutoAnalysis = async (targetTopic?: Topic) => {
    const activeTopic = targetTopic || topic;
    if (!activeTopic) return;

    setIsRunningPipeline(true);
    setPipelineStatus("Collecting 100+ source items across Google News RSS & Reddit API...");
    try {
      setPipelineStatus("Ingesting discourse items and mapping to common schema...");
      const result = await triggerFullPipeline(activeTopic.slug, 100, 5);
      setPipelineStatus("Deduplicating via MinHash/LSH & clustering perspectives with HDBSCAN...");
      
      const perCount = result?.synthesis?.perspectives_count || result?.topic?.perspectives_count || 0;
      setPipelineStatus(`Analysis complete! Successfully generated ${perCount} grounded perspective(s).`);
      
      const refreshed = await getTopicBySlug(activeTopic.slug);
      setTopic(refreshed);
    } catch (err: any) {
      console.warn("Pipeline execution note:", err);
      setPipelineStatus(`Note: ${err.message || "Analysis pipeline encountered partial provider limits."}`);
    } finally {
      setIsRunningPipeline(false);
    }
  };

  const realPerspectives = topic?.perspectives || [];

  const stanceFilteredPerspectives = useMemo(() => realPerspectives.filter((p) => {
    if (selectedStance === "all") return true;
    return classifyStance(p.stance || p.perspective_type) === selectedStance;
  }), [realPerspectives, selectedStance]);

  const sourceFilteredPerspectives = useMemo(() => {
    if (selectedSource === "all") return stanceFilteredPerspectives;
    return stanceFilteredPerspectives.filter((p) => p.sample_quotes?.some(
      (q) => normalizeEvidenceSource(q.source, q.url) === selectedSource
    ));
  }, [stanceFilteredPerspectives, selectedSource]);

  // Older or sparse analyses may not have source-attributed X quotes even
  // though the page has fetched X posts. Keep viewpoints visible and explain
  // why the evidence filter could not narrow them.
  const sourceFilterHasNoMatches = selectedSource !== "all" &&
    sourceFilteredPerspectives.length === 0 && stanceFilteredPerspectives.length > 0;
  const selectedSourceLabel = selectedSource === "google_news"
    ? "Google News"
    : selectedSource === "reddit"
      ? "Reddit"
      : selectedSource === "x"
        ? "X"
        : selectedSource;
  const perspectivesList = sourceFilterHasNoMatches
    ? stanceFilteredPerspectives
    : sourceFilteredPerspectives;

  const totalShareSum = useMemo(() => {
    return realPerspectives.reduce((acc, p) => acc + p.estimated_share, 0) || 1.0;
  }, [realPerspectives]);

  if (isLoading && !topic) {
    return (
      <div className="min-h-screen flex flex-col justify-between bg-[#090c10] text-[#f0f6fc]">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-24 flex items-center justify-center">
          <Spinner size="lg" label="Loading discourse brief and perspectives..." />
        </main>
        <Footer />
      </div>
    );
  }

  if (!topic) {
    return (
      <div className="min-h-screen flex flex-col justify-between bg-[#090c10] text-[#f0f6fc]">
        <Navbar />
        <main className="max-w-4xl mx-auto px-4 py-24 text-center space-y-4">
          <h2 className="font-headline text-2xl font-bold text-white">Discourse Inquiry Not Found</h2>
          <p className="font-serif-body text-sm text-slate-400">
            The topic with slug &apos;{slug}&apos; could not be retrieved.
          </p>
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-100 text-slate-900 text-xs font-bold uppercase tracking-wider"
          >
            Return to Front Page
          </Link>
        </main>
        <Footer />
      </div>
    );
  }

  const coverage = topic.source_coverage || {
    google_news: 0,
    reddit: 0,
    x: 0,
    total_combined: 0,
  };
  const totalCombined = coverage.total_combined ?? 0;
  const targetItems = 100;
  const targetMet = totalCombined >= targetItems;

  return (
    <div className="flex flex-col min-h-screen bg-[#090c10] text-[#f0f6fc]">
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 flex-1 w-full">
        {/* Navigation & Refresh Control Bar */}
        <div className="flex items-center justify-between gap-4 border-b border-white/[0.08] pb-4">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-xs font-mono font-semibold text-slate-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>← Back to Front Page</span>
          </Link>

          <button
            onClick={() => handleRunAutoAnalysis()}
            disabled={isRunningPipeline}
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/10 text-xs font-mono text-slate-300 hover:text-white transition-all disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRunningPipeline ? "animate-spin" : ""}`} />
            <span>{isRunningPipeline ? "Analyzing Sources..." : "Refresh Analysis"}</span>
          </button>
        </div>

        {/* Broadsheet Editorial Header */}
        <div className="bg-[#0d1117] border border-white/10 rounded-2xl p-6 sm:p-8 space-y-6 shadow-xl">
          {/* Dateline & Sample Size Target Indicators */}
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs font-mono border-b border-white/[0.06] pb-3">
            <div className="flex items-center gap-2.5 flex-wrap">
              <span className="px-2.5 py-1 rounded bg-white/[0.06] border border-white/10 text-slate-200 font-bold uppercase tracking-wider">
                Public Discourse Brief
              </span>
              <span className="text-slate-400">
                Updated {formatTimeAgo(topic.updated_at || topic.created_at)}
              </span>
            </div>

            {/* 100-Item Collection Target Pill */}
            <div className="flex items-center gap-2 px-3 py-1 rounded-md bg-white/[0.04] border border-white/10">
              <span className="text-slate-400">Sampled Discourse:</span>
              <span className="font-bold text-slate-100">
                {totalCombined} Items
              </span>
              <span className="text-slate-500">·</span>
              <span className={targetMet ? "text-emerald-400 font-semibold" : "text-amber-400 font-semibold"}>
                {targetMet ? "Target (100) Met" : `Target: 100 (${totalCombined} gathered)`}
              </span>
            </div>
          </div>

          {/* Headline */}
          <div>
            <h1 className="font-headline text-2xl sm:text-4xl md:text-5xl font-bold text-slate-100 tracking-tight leading-tight">
              {topic.title}
            </h1>
            <p className="font-serif-body text-xs sm:text-sm text-slate-400 mt-2 italic">
              Autonomous multi-perspective synthesis compiled from verified online reporting and discussion.
            </p>
          </div>

          {/* Live Progress Banner (if pipeline is running) */}
          {isRunningPipeline && (
            <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs font-mono space-y-2 animate-pulse">
              <div className="flex items-center gap-2 font-bold uppercase tracking-wider">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
                <span>Automatic Discourse Pipeline in Progress</span>
              </div>
              <p className="text-slate-300 font-serif-body text-sm">
                {pipelineStatus}
              </p>
            </div>
          )}

          {/* Source Breakdown Component */}
          <div className="pt-2 border-t border-white/[0.06]">
            <SourceBreakdown coverage={coverage} />
          </div>
        </div>

        {/* Perspectives Brief Section */}
        {realPerspectives.length > 0 ? (
          <div className="space-y-8">
            {/* Share Distribution Chart */}
            <ShareBarChart
              perspectives={realPerspectives}
              selectedId={selectedPerspectiveId}
              onSelectPerspective={(p) => setSelectedPerspectiveId(p.id)}
            />

            {/* Filter Controls */}
            <PerspectiveFilter
              selectedStance={selectedStance}
              onSelectStance={setSelectedStance}
              selectedSource={selectedSource}
              onSelectSource={setSelectedSource}
              totalPerspectives={perspectivesList.length}
            />

            {/* Perspective Cards Grid */}
            <section className="space-y-4">
              <div className="border-b border-white/10 pb-2 flex items-baseline justify-between">
                <h2 className="font-headline text-xl sm:text-2xl font-bold tracking-tight text-slate-100 uppercase">
                  Identified Stakeholder Perspectives
                </h2>
                <span className="text-xs font-mono text-slate-400">
                  {perspectivesList.length} Grounded Viewpoints
                </span>
              </div>

              {perspectivesList.length > 0 ? (
                <>
                  {sourceFilterHasNoMatches && (
                    <p className="rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-4 py-3 text-xs text-amber-100/80">
                      No identified viewpoint has a directly linked quote from {selectedSourceLabel} yet. Showing the viewpoints from the full analysis.
                    </p>
                  )}
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {perspectivesList.map((perspective) => (
                      <PerspectiveCard
                        key={perspective.id}
                        perspective={perspective}
                        totalShareSum={totalShareSum}
                        highlighted={selectedPerspectiveId === perspective.id}
                      />
                    ))}
                  </div>
                </>
              ) : (
                <div className="bg-[#0d1117] border border-white/10 rounded-xl p-10 text-center space-y-3">
                  <p className="font-serif-body text-sm text-slate-300">
                    No perspectives match the active filter criteria.
                  </p>
                  <button
                    onClick={() => {
                      setSelectedStance("all");
                      setSelectedSource("all");
                    }}
                    className="px-3 py-1.5 rounded-lg bg-white/[0.08] text-xs font-mono text-slate-200 hover:bg-white/[0.15]"
                  >
                    Reset Filter
                  </button>
                </div>
              )}
            </section>

            {/* Editorial Confidence & Methodology Disclaimer */}
            <div className="bg-[#0d1117] border border-white/10 rounded-xl p-5 text-xs text-slate-400 font-mono space-y-2">
              <div className="flex items-center gap-2 text-slate-300 font-bold uppercase tracking-wider">
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                <span>Editorial Methodology & Disclosure</span>
              </div>
              <p className="font-serif-body text-slate-400 leading-relaxed text-xs">
                Estimated discourse shares represent proportions of analyzed public posts across Google News, Reddit, and X—not scientific polling results. All direct quotes are traceable to ingested source URLs. If fewer than 100 relevant items were gathered ({totalCombined} items recorded), analysis is synthesized from available grounded evidence.
              </p>
            </div>
          </div>
        ) : (
          /* Empty / In-Progress State */
          <div className="bg-[#0d1117] border border-white/10 rounded-2xl p-10 text-center space-y-5 max-w-xl mx-auto shadow-xl">
            <div className="w-12 h-12 rounded-xl bg-white/[0.06] border border-white/10 text-slate-300 flex items-center justify-center mx-auto">
              <Sparkles className="w-6 h-6 text-amber-400" />
            </div>

            <div className="space-y-2">
              <h3 className="font-headline text-xl font-bold text-slate-100">
                {isRunningPipeline
                  ? "Synthesizing Discourse Brief..."
                  : topic.last_clustered_at
                  ? "Limited Discourse Evidence Gathered"
                  : "No Perspectives Synthesized Yet"}
              </h3>
              <p className="font-serif-body text-sm text-slate-400">
                {isRunningPipeline
                  ? "Crawling Google News RSS and Reddit API, deduplicating articles, and clustering distinct viewpoint arguments..."
                  : topic.last_clustered_at
                  ? `Discourse crawl gathered ${totalCombined} item(s). Minimum volume threshold for multi-perspective clustering was not met. You can re-run analysis to query updated sources.`
                  : "Click below to trigger automatic 100-item multi-source discourse analysis for this topic."}
              </p>
            </div>

            {!isRunningPipeline && (
              <button
                onClick={() => handleRunAutoAnalysis()}
                className="px-5 py-2.5 rounded-lg bg-slate-100 hover:bg-white text-slate-900 text-xs font-bold uppercase tracking-wider shadow-md transition-all"
              >
                {topic.last_clustered_at ? "Re-run Discourse Analysis" : "Start Discourse Analysis"}
              </button>
            )}
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}

