import React from "react";
import { Loader2 } from "lucide-react";
import { SourceCoverage } from "@/lib/types";
import { SourceBadge } from "./SourceBadge";

interface SourceBreakdownProps {
  coverage: SourceCoverage;
  totalUsable?: number;
}

export const SourceBreakdown: React.FC<SourceBreakdownProps> = ({
  coverage,
  totalUsable,
}) => {
  const xStatus = coverage.x_ingestion_status || "complete";
  const xAnalysisStatus = coverage.x_analysis_status || "complete";
  const xScrapeActive = ["pending", "running", "ready", "merging"].includes(xStatus);
  const xIsLoading = xScrapeActive || ["pending", "running"].includes(xAnalysisStatus);
  const xCount = xScrapeActive
    ? Math.max(coverage.x || 0, (coverage.x_ingestion_baseline_coverage || 0) + (coverage.x_ingestion_new_count || 0))
    : (coverage.x || 0);
  const total = Math.max(
    coverage.total_combined || 1,
    coverage.google_news + coverage.reddit + xCount,
  );
  const gPercent = Math.round((coverage.google_news / total) * 100);
  const rPercent = Math.round((coverage.reddit / total) * 100);
  const xPercent = Math.round((xCount / total) * 100);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span className="font-semibold uppercase tracking-wider text-slate-300">
          Source Platform Coverage
        </span>
        <span>
          {coverage.total_combined.toLocaleString()} total ingested posts
        </span>
      </div>

      {/* Segmented Bar */}
      <div className="h-2.5 w-full bg-slate-800 rounded-full overflow-hidden flex gap-0.5 p-0.5">
        {coverage.google_news > 0 && (
          <div
            className="bg-blue-500 rounded-l-full transition-all duration-500"
            style={{ width: `${gPercent}%` }}
            title={`Google News: ${coverage.google_news} (${gPercent}%)`}
          />
        )}
        {coverage.reddit > 0 && (
          <div
            className="bg-orange-500 transition-all duration-500"
            style={{ width: `${rPercent}%` }}
            title={`Reddit: ${coverage.reddit} (${rPercent}%)`}
          />
        )}
        {xCount > 0 && (
          <div
            className="bg-zinc-400 rounded-r-full transition-all duration-500"
            style={{ width: `${xPercent}%` }}
            title={`X: ${xCount} (${xPercent}%)`}
          />
        )}
      </div>

      {/* Badges */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <SourceBadge source="google_news" count={coverage.google_news} />
        <SourceBadge source="reddit" count={coverage.reddit} />
        <SourceBadge source="x" count={xCount} />
      </div>

      {(xIsLoading || xStatus === "failed" || Boolean(coverage.x_ingestion_message)) && (
        <div
          className={`flex items-center gap-2 text-[11px] font-mono ${xStatus === "failed" ? "text-rose-300" : "text-slate-400"}`}
          aria-live="polite"
        >
          {xIsLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-400" />}
          <span>
            {coverage.x_ingestion_message || (xIsLoading ? "Searching X/Twitter instances…" : "X/Twitter enrichment could not finish.")}
            {xIsLoading && (coverage.x_ingestion_new_count || 0) > 0
              ? ` · ${(coverage.x_ingestion_new_count || 0).toLocaleString()} new post(s) staged live`
              : ""}
          </span>
        </div>
      )}

      {coverage.x_ingestion_posts && coverage.x_ingestion_posts.length > 0 && (
        <div className="space-y-2 rounded-lg border border-white/[0.07] bg-black/20 p-3">
          <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">
            Latest X/Twitter posts from this search
          </p>
          {coverage.x_ingestion_posts.map((post, index) => (
            <div key={`${post.tweet_id || "post"}-${index}`} className="border-l-2 border-zinc-500/50 pl-2.5">
              <p className="text-[10px] font-mono text-slate-500">@{post.handle || "anonymous"}</p>
              <p className="text-xs leading-relaxed text-slate-300">{post.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
