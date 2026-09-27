"use client";

import React, { useState, useMemo } from "react";
import { Layers, Inbox } from "lucide-react";
import { Topic } from "@/lib/types";
import { TopicCard } from "./TopicCard";

interface TopicGridProps {
  topics: Topic[];
  onOpenCreateModal?: () => void;
}

export const TopicGrid: React.FC<TopicGridProps> = ({
  topics,
  onOpenCreateModal,
}) => {
  const [activeTab, setActiveTab] = useState<"all" | "synthesized" | "active">("all");
  const [sortBy, setSortBy] = useState<"recent" | "trending" | "volume">("recent");

  const filteredTopics = useMemo(() => {
    let list = [...topics];

    // Tab filter
    if (activeTab === "synthesized") {
      list = list.filter((t) => (t.perspectives?.length || 0) > 0);
    } else if (activeTab === "active") {
      list = list.filter((t) => (t.source_coverage?.total_combined || 0) > 0);
    }

    // Sorting
    list.sort((a, b) => {
      if (sortBy === "trending") {
        return b.trending_score - a.trending_score;
      }
      if (sortBy === "volume") {
        return (
          (b.source_coverage?.total_combined || 0) -
          (a.source_coverage?.total_combined || 0)
        );
      }
      return (
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
    });

    return list;
  }, [topics, activeTab, sortBy]);

  return (
    <div id="archive" className="space-y-5">
      {/* Editorial Section Header & Filter Controls */}
      <div className="border-b-2 border-slate-700/80 pb-3 flex flex-col sm:flex-row sm:items-baseline justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 bg-slate-300 rounded-sm" />
          <h2 className="font-headline text-xl sm:text-2xl font-bold tracking-tight text-slate-100 uppercase">
            Discourse Archive · Recent Inquiries
          </h2>
        </div>

        {/* Filter Tabs & Sort Dropdown */}
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center p-0.5 rounded-lg bg-[#0d1117] border border-white/10">
            <button
              onClick={() => setActiveTab("all")}
              className={`px-3 py-1 rounded-md text-xs font-semibold font-mono transition-all ${
                activeTab === "all"
                  ? "bg-slate-100 text-slate-900 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              All ({topics.length})
            </button>
            <button
              onClick={() => setActiveTab("synthesized")}
              className={`px-3 py-1 rounded-md text-xs font-semibold font-mono transition-all ${
                activeTab === "synthesized"
                  ? "bg-slate-100 text-slate-900 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Synthesized
            </button>
          </div>

          <select
            value={sortBy}
            onChange={(e: any) => setSortBy(e.target.value)}
            className="bg-[#0d1117] border border-white/10 text-slate-300 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-white/30 cursor-pointer font-mono"
          >
            <option value="recent">Sort: Most Recent</option>
            <option value="trending">Sort: Highest Velocity</option>
            <option value="volume">Sort: Most Items Sampled</option>
          </select>
        </div>
      </div>

      {/* Grid of editorial cards */}
      {filteredTopics.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredTopics.map((topic) => (
            <TopicCard key={topic.id} topic={topic} />
          ))}
        </div>
      ) : (
        <div className="bg-[#0d1117] border border-white/10 rounded-xl p-10 text-center space-y-3 max-w-md mx-auto">
          <div className="w-10 h-10 rounded-xl bg-white/[0.05] flex items-center justify-center mx-auto text-slate-400">
            <Inbox className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-headline text-base font-bold text-slate-200">
              No discourse topics archived yet
            </h3>
            <p className="font-serif-body text-xs text-slate-400 mt-1">
              Enter any inquiry in the search bar above to generate the first public brief.
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

