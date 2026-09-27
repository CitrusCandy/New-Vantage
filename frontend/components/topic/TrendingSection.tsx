import React from "react";
import Link from "next/link";
import { TrendingUp, ArrowRight, Flame, Layers } from "lucide-react";
import { Topic } from "@/lib/types";
import { formatTimeAgo } from "@/lib/utils";

interface TrendingSectionProps {
  trendingTopics: Topic[];
}

export const TrendingSection: React.FC<TrendingSectionProps> = ({
  trendingTopics,
}) => {
  if (!trendingTopics || trendingTopics.length === 0) return null;

  const leadStory = trendingTopics[0];
  const sideStories = trendingTopics.slice(1, 4);

  return (
    <section id="trending" className="space-y-4">
      {/* Editorial Section Header */}
      <div className="border-b-2 border-slate-700/80 pb-2 flex items-baseline justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 bg-amber-400 rounded-sm" />
          <h2 className="font-headline text-xl sm:text-2xl font-bold tracking-tight text-slate-100 uppercase">
            Front Page · Leading Discourse
          </h2>
        </div>
        <span className="text-xs font-mono text-slate-400 uppercase tracking-widest hidden sm:inline">
          Live Discourse Velocity
        </span>
      </div>

      {/* Broadsheet Lead Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Main Lead Editorial Feature (Left 7 cols) */}
        {leadStory && (
          <Link
            href={`/topics/${leadStory.slug}`}
            className="lg:col-span-7 group bg-[#0d1117] border border-white/10 hover:border-white/30 rounded-xl p-5 sm:p-6 flex flex-col justify-between transition-all shadow-xl hover:shadow-2xl"
          >
            <div>
              <div className="flex items-center justify-between gap-2 mb-3">
                <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[11px] font-mono font-bold uppercase tracking-wider">
                  Lead Story · Rank #1
                </span>
                <span className="text-xs font-mono text-slate-400">
                  {formatTimeAgo(leadStory.updated_at || leadStory.created_at)}
                </span>
              </div>

              <h3 className="font-headline text-xl sm:text-2xl font-bold text-slate-100 group-hover:text-amber-200 transition-colors leading-tight mb-3">
                {leadStory.title}
              </h3>

              {leadStory.perspectives && leadStory.perspectives.length > 0 && (
                <p className="font-serif-body text-sm sm:text-base text-slate-300 italic line-clamp-3 mb-4 border-l-2 border-slate-700 pl-3">
                  “{leadStory.perspectives[0].summary}”
                </p>
              )}
            </div>

            <div className="pt-4 border-t border-white/[0.08] flex items-center justify-between text-xs text-slate-400">
              <div className="flex items-center gap-3">
                <span className="font-mono text-slate-300">
                  {leadStory.source_coverage?.total_combined || "100+"} Items
                </span>
                <span>·</span>
                <span className="text-slate-400">
                  {leadStory.perspectives?.length || 3} Perspectives
                </span>
              </div>
              <span className="font-semibold text-slate-200 group-hover:text-white flex items-center gap-1">
                <span>Read Analysis</span>
                <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
              </span>
            </div>
          </Link>
        )}

        {/* Side Column Stories (Right 5 cols) */}
        <div className="lg:col-span-5 flex flex-col gap-3.5 justify-between">
          {sideStories.map((topic, idx) => (
            <Link
              key={topic.id}
              href={`/topics/${topic.slug}`}
              className="group bg-[#0d1117] border border-white/10 hover:border-white/25 rounded-xl p-4 transition-all flex flex-col justify-between flex-1 shadow-md hover:shadow-lg"
            >
              <div>
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span className="text-[10px] font-mono font-bold uppercase text-slate-400">
                    Rank #{idx + 2} Trending
                  </span>
                  <span className="text-[10px] font-mono text-slate-500">
                    {formatTimeAgo(topic.updated_at || topic.created_at)}
                  </span>
                </div>

                <h4 className="font-headline text-base font-bold text-slate-100 group-hover:text-amber-200 transition-colors leading-snug line-clamp-2">
                  {topic.title}
                </h4>
              </div>

              <div className="mt-2.5 pt-2 border-t border-white/[0.06] flex items-center justify-between text-[11px] text-slate-400">
                <span className="font-mono text-slate-400">
                  {topic.perspectives?.length ? `${topic.perspectives.length} Perspectives` : "Verified Discourse"}
                </span>
                <span className="text-slate-300 group-hover:text-white flex items-center gap-0.5">
                  <span>View</span>
                  <ArrowRight className="w-3 h-3" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
};

