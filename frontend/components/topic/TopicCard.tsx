import React from "react";
import Link from "next/link";
import {
  TrendingUp,
  Clock,
  Layers,
  ArrowUpRight,
  BookOpen,
} from "lucide-react";
import { Topic } from "@/lib/types";
import { SourceBadge } from "../source/SourceBadge";
import { formatTimeAgo } from "@/lib/utils";

interface TopicCardProps {
  topic: Topic;
}

export const TopicCard: React.FC<TopicCardProps> = ({ topic }) => {
  const coverage = topic.source_coverage || {
    google_news: 0,
    reddit: 0,
    x: 0,
    total_combined: 0,
  };
  const perspectiveCount = topic.perspectives?.length || 0;
  const trendingPercent = Math.round(topic.trending_score * 100);

  return (
    <Link href={`/topics/${topic.slug}`} className="block h-full group">
      <div className="h-full flex flex-col justify-between bg-[#0d1117] border border-white/10 hover:border-white/30 rounded-xl p-5 transition-all shadow-md hover:shadow-xl hover:-translate-y-0.5">
        <div className="space-y-3">
          {/* Top Bar: Dateline & Trending Score */}
          <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] pb-2.5">
            <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1">
              <Clock className="w-3 h-3 text-slate-500" />
              {formatTimeAgo(topic.updated_at || topic.created_at)}
            </span>

            <span className="px-2 py-0.5 rounded bg-white/[0.06] border border-white/10 text-[10px] font-mono font-bold text-slate-300 uppercase">
              {trendingPercent}% Velocity
            </span>
          </div>

          {/* Topic Title in Broadsheet Headline Typography */}
          <div>
            <h3 className="font-headline text-lg sm:text-xl font-bold text-slate-100 group-hover:text-amber-200 transition-colors line-clamp-2 leading-snug">
              {topic.title}
            </h3>
          </div>

          {/* Source Breakdown Badges */}
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {coverage.google_news > 0 && (
              <SourceBadge source="google_news" count={coverage.google_news} />
            )}
            {coverage.reddit > 0 && (
              <SourceBadge source="reddit" count={coverage.reddit} />
            )}
            {coverage.x > 0 && (
              <SourceBadge source="x" count={coverage.x} />
            )}
            {coverage.total_combined === 0 && (
              <span className="text-xs font-serif-body text-slate-500 italic">
                Awaiting initial ingestion
              </span>
            )}
          </div>
        </div>

        {/* Bottom Footer: Perspectives Count & Read Link */}
        <div className="mt-4 pt-3 border-t border-white/[0.06] flex items-center justify-between text-xs text-slate-400">
          <div className="flex items-center gap-1.5">
            {perspectiveCount > 0 ? (
              <span className="font-medium text-slate-200">
                <span className="text-amber-400 font-bold">{perspectiveCount}</span> Perspectives
              </span>
            ) : (
              <span className="text-slate-500">
                {coverage.total_combined > 0
                  ? `${coverage.total_combined} Items Collected`
                  : "Ready to Analyze"}
              </span>
            )}
          </div>

          <span className="text-slate-300 group-hover:text-white flex items-center gap-1 text-xs font-semibold">
            <span>Read Brief</span>
            <ArrowUpRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
          </span>
        </div>
      </div>
    </Link>
  );
};

