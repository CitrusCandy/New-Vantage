"use client";

import React, { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Sparkles,
  TrendingUp,
  Clock,
  RefreshCw,
  Cpu,
  Shield,
  Layers,
  Activity,
  AlertCircle,
  CheckCircle,
  Database,
  ExternalLink,
  Globe,
  MessageSquare,
  Twitter,
  Play,
} from "lucide-react";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { PerspectiveCard } from "@/components/perspective/PerspectiveCard";
import { PerspectiveFilter } from "@/components/perspective/PerspectiveFilter";
import { ShareBarChart } from "@/components/charts/ShareBarChart";
import { SourceBreakdown } from "@/components/source/SourceBreakdown";
import { Button } from "@/components/common/Button";
import { Spinner } from "@/components/common/Spinner";
import { Badge } from "@/components/common/Badge";
import {
  getTopicBySlug,
  triggerIngestion,
  triggerClustering,
  triggerSynthesis,
  triggerFullPipeline,
} from "@/lib/api";
import { Perspective, Topic } from "@/lib/types";
import { classifyStance, formatTimeAgo } from "@/lib/utils";

export default function TopicDetailPage() {
  const params = useParams();
  const slug = params?.slug as string;
  const router = useRouter();

  const [topic, setTopic] = useState<Topic | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pipeline Action States
  const [isRunningPipeline, setIsRunningPipeline] = useState(false);
  const [isIngesting, setIsIngesting] = useState(false);
  const [isClustering, setIsClustering] = useState(false);
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Filter States
  const [selectedStance, setSelectedStance] = useState<string>("all");
  const [selectedSource, setSelectedSource] = useState<string>("all");
  const [selectedPerspectiveId, setSelectedPerspectiveId] = useState<number | null>(null);

  const fetchTopicData = async () => {
    if (!slug) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getTopicBySlug(slug);
      setTopic(data);
    } catch (err: any) {
      console.error("Could not fetch topic from backend:", err);
      setError(err.message || "Failed to load topic");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTopicData();
  }, [slug]);

  // Handle Full End-to-End Analysis Pipeline
  const handleTriggerFullPipeline = async () => {
    if (!topic) return;
    setIsRunningPipeline(true);
    setActionMessage("Executing end-to-end intelligence pipeline: Ingesting -> Merging -> Clustering -> Synthesizing...");
    try {
      const result = await triggerFullPipeline(topic.slug, 50, 5);
      const perCount = result?.synthesis?.perspectives_count || result?.topic?.perspectives_count || 0;
      setActionMessage(`Analysis complete! Successfully generated ${perCount} grounded perspective(s).`);
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Pipeline note: ${err.message}`);
    } finally {
      setIsRunningPipeline(false);
    }
  };

  // Handle Individual Pipeline Stages
  const handleTriggerIngestion = async () => {
    if (!topic) return;
    setIsIngesting(true);
    setActionMessage("Scraping Google News, Reddit, and X into staging tables & merging...");
    try {
      const res = await triggerIngestion(topic.slug, 50);
      const added = res?.merge_result?.new_records_added ?? 0;
      setActionMessage(`Ingestion finished: ${added} new records staged and merged into combined dataset.`);
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Ingestion error: ${err.message}`);
    } finally {
      setIsIngesting(false);
    }
  };

  const handleTriggerClustering = async () => {
    if (!topic) return;
    setIsClustering(true);
    setActionMessage("Generating normalized vector embeddings and executing HDBSCAN clustering...");
    try {
      const res = await triggerClustering(topic.slug, 5);
      const clustersCount = res?.cluster_count ?? 0;
      setActionMessage(`Clustering finished: Formed ${clustersCount} discourse clusters from ${res?.sample_size ?? 0} items.`);
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Clustering error: ${err.message}`);
    } finally {
      setIsClustering(false);
    }
  };

  const handleTriggerSynthesis = async () => {
    if (!topic) return;
    setIsSynthesizing(true);
    setActionMessage("Extracting grounded cluster samples & synthesizing distinct viewpoints via LLM...");
    try {
      const res = await triggerSynthesis(topic.slug, 5);
      const count = res?.perspectives_count ?? (res?.perspectives?.length || 0);
      setActionMessage(`Synthesis finished: Generated ${count} grounded perspectives.`);
      await fetchTopicData();
    } catch (err: any) {
      setActionMessage(`Synthesis note: ${err.message}`);
    } finally {
      setIsSynthesizing(false);
    }
  };

  // Grounded perspectives list directly from topic data (no fake demo fallbacks)
  const realPerspectives = topic?.perspectives || [];

  const perspectivesList = useMemo(() => {
    return realPerspectives.filter((p) => {
      // Filter stance
      if (selectedStance !== "all") {
        const category = classifyStance(p.perspective_type);
        if (category !== selectedStance) return false;
      }
      // Filter source
      if (selectedSource !== "all") {
        const hasSource = p.sample_quotes?.some(
          (q) => q.source.toLowerCase() === selectedSource.toLowerCase()
        );
        if (!hasSource) return false;
      }
      return true;
    });
  }, [realPerspectives, selectedStance, selectedSource]);

  const totalShareSum = useMemo(() => {
    return realPerspectives.reduce((acc, p) => acc + p.estimated_share, 0) || 1.0;
  }, [realPerspectives]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex flex-col justify-between">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-24 flex items-center justify-center">
          <Spinner size="lg" label="Loading topic perspectives and discourse data..." />
        </main>
        <Footer />
      </div>
    );
  }

  if (!topic) {
    return (
      <div className="min-h-screen flex flex-col justify-between">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-24 text-center space-y-4">
          <h2 className="text-2xl font-bold text-white">Topic Not Found</h2>
          <p className="text-sm text-slate-400">
            The discourse topic with slug &apos;{slug}&apos; could not be retrieved from the server.
          </p>
          <Link href="/">
            <Button variant="primary">Return to Trending Feed</Button>
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
  const trendingPercent = Math.round((topic.trending_score || 0) * 100);

  return (
    <div className="flex flex-col min-h-screen">
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 flex-1 w-full">
        {/* Back Link */}
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-xs font-semibold text-slate-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Trending Feed</span>
        </Link>

        {/* Topic Header Card */}
        <div className="glass-card rounded-3xl p-6 md:p-8 border border-white/[0.08] space-y-6 relative overflow-hidden">
          {/* Ambient Glow */}
          <div className="absolute top-0 right-0 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />

          {/* Top Row: Trending Score & Pipeline Trigger */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400 text-xs font-bold">
                <TrendingUp className="w-3.5 h-3.5" />
                Trending Score: {trendingPercent}%
              </span>
              <span className="text-xs text-slate-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                Updated {formatTimeAgo(topic.updated_at)}
              </span>
            </div>

            {/* Pipeline Action Controls */}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="primary"
                isLoading={isRunningPipeline}
                icon={<Sparkles className="w-3.5 h-3.5" />}
                onClick={handleTriggerFullPipeline}
                title="Execute full end-to-end intelligence analysis: Ingestion, HDBSCAN clustering, and LLM perspective synthesis"
              >
                Run End-to-End Analysis
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isLoading={isIngesting}
                icon={<Database className="w-3.5 h-3.5 text-blue-400" />}
                onClick={handleTriggerIngestion}
                title="Scrape Google News, Reddit, and X into staging tables"
              >
                Ingest Sources
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isLoading={isClustering}
                icon={<Cpu className="w-3.5 h-3.5 text-purple-400" />}
                onClick={handleTriggerClustering}
                title="Run MinHash deduplication & vector clustering"
              >
                Cluster Discourse
              </Button>
              <Button
                size="sm"
                variant="secondary"
                isLoading={isSynthesizing}
                icon={<RefreshCw className="w-3.5 h-3.5 text-emerald-400" />}
                onClick={handleTriggerSynthesis}
                title="Synthesize viewpoints via LLM"
              >
                Synthesize
              </Button>
            </div>
          </div>

          {/* Action Notification Banner */}
          {actionMessage && (
            <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-300 flex items-center gap-2 animate-fade-in">
              <Activity className="w-4 h-4 text-indigo-400 shrink-0 animate-spin" />
              <span>{actionMessage}</span>
            </div>
          )}

          {/* Title & Slug */}
          <div>
            <h1 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight leading-tight">
              {topic.title}
            </h1>
            <p className="text-xs sm:text-sm text-slate-400 mt-2 font-mono">
              Topic Query: <span className="text-slate-300 font-semibold">{topic.title}</span> | Slug: <span className="text-slate-400">{topic.slug}</span>
            </p>
          </div>

          {/* Source Breakdown Component */}
          <div className="pt-2 border-t border-white/[0.06]">
            <SourceBreakdown coverage={coverage} />
          </div>
        </div>

        {/* Persisted Grounded Perspectives Section */}
        {realPerspectives.length > 0 ? (
          <>
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

            {/* Perspective Cards Showcase Grid */}
            <section className="space-y-6">
              <div className="flex items-center justify-between">
                <h2 className="text-lg sm:text-xl font-extrabold text-white tracking-tight flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-indigo-400" />
                  <span>Synthesized Perspectives Brief</span>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                    {perspectivesList.length} grounded viewpoint(s)
                  </span>
                </h2>
              </div>

              {perspectivesList.length > 0 ? (
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
              ) : (
                <div className="glass-card rounded-2xl p-12 text-center space-y-3">
                  <p className="text-sm font-semibold text-slate-300">
                    No perspectives match the selected filters
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setSelectedStance("all");
                      setSelectedSource("all");
                    }}
                  >
                    Reset Filters
                  </Button>
                </div>
              )}
            </section>
          </>
        ) : (
          /* Empty / Unanalyzed Topic State */
          <div className="glass-card rounded-3xl p-8 sm:p-12 text-center space-y-6 border border-white/[0.08] relative overflow-hidden">
            <div className="w-16 h-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center mx-auto shadow-inner">
              <Sparkles className="w-8 h-8" />
            </div>

            <div className="max-w-xl mx-auto space-y-2">
              <h3 className="text-xl font-bold text-white">
                {totalCombined === 0
                  ? "Discourse Intelligence Ready to Ingest"
                  : `Discourse Gathered (${totalCombined} sources) — Ready for Synthesis`}
              </h3>
              <p className="text-xs sm:text-sm text-slate-400">
                {totalCombined === 0
                  ? "Click below to execute the end-to-end pipeline. Vantage News will scrape Google News RSS, Reddit, and X into staging tables, run MinHash deduplication, cluster viewpoints using HDBSCAN, and synthesize structured perspectives."
                  : "Sources have been ingested and normalized. Run perspective synthesis to cluster viewpoints and generate grounded multi-perspective intelligence."}
              </p>
            </div>

            {/* Provider Readiness Overview */}
            <div className="max-w-2xl mx-auto grid grid-cols-1 sm:grid-cols-3 gap-3 text-left">
              <div className="p-4 rounded-xl bg-surface-light border border-surface-border space-y-1">
                <div className="flex items-center gap-2 text-xs font-semibold text-blue-400">
                  <Globe className="w-3.5 h-3.5" />
                  <span>Google News RSS</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  {coverage.google_news > 0 ? `${coverage.google_news} items staged` : "Public RSS active"}
                </p>
              </div>

              <div className="p-4 rounded-xl bg-surface-light border border-surface-border space-y-1">
                <div className="flex items-center gap-2 text-xs font-semibold text-orange-400">
                  <MessageSquare className="w-3.5 h-3.5" />
                  <span>Reddit Public API</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  {coverage.reddit > 0 ? `${coverage.reddit} items staged` : "Public REST active"}
                </p>
              </div>

              <div className="p-4 rounded-xl bg-surface-light border border-surface-border space-y-1">
                <div className="flex items-center gap-2 text-xs font-semibold text-sky-400">
                  <Twitter className="w-3.5 h-3.5" />
                  <span>X Public Scraper</span>
                </div>
                <p className="text-[11px] text-slate-400">
                  {coverage.x > 0 ? `${coverage.x} items staged` : "Provider fallback ready"}
                </p>
              </div>
            </div>

            <div className="pt-4 flex justify-center gap-3">
              <Button
                variant="primary"
                size="lg"
                isLoading={isRunningPipeline}
                icon={<Play className="w-4 h-4 fill-current" />}
                onClick={handleTriggerFullPipeline}
              >
                Run End-to-End Analysis
              </Button>
            </div>
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
