import React from "react";
import Link from "next/link";
import { Newspaper, ShieldCheck, Database, GitBranch, ExternalLink } from "lucide-react";

export const Footer: React.FC = () => {
  return (
    <footer className="w-full border-t-2 border-stone-800 bg-[#0c0c0d] mt-24 text-stone-400">
      {/* Editorial Rule Top */}
      <div className="border-b border-stone-800/80 py-2 bg-stone-950/40 text-center">
        <span className="font-mono text-[10px] tracking-widest text-stone-500 uppercase">
          Vantage Gazette &bull; Open-Discourse Intelligence Bureau &bull; Published Continuously
        </span>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-10">
          {/* Brand / Editorial Mission */}
          <div className="md:col-span-2 space-y-3">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-sm bg-stone-100 text-stone-900 flex items-center justify-center font-serif font-black text-base shadow-sm">
                V
              </div>
              <span className="font-serif font-bold text-lg tracking-wider text-stone-100 uppercase">
                THE VANTAGE GAZETTE
              </span>
            </div>
            <p className="font-serif text-sm text-stone-400 max-w-md leading-relaxed">
              An independent algorithmic editorial parsing the plurality of public discourse.
              Aggregating verified reports, community discussions, and primary sources to map
              divergent worldviews with complete provenance and statistical neutrality.
            </p>
          </div>

          {/* Editorial Methodology */}
          <div>
            <h4 className="font-mono text-xs font-semibold text-stone-300 uppercase tracking-widest mb-3">
              Editorial Standards
            </h4>
            <ul className="space-y-2 text-xs font-serif text-stone-400">
              <li className="flex items-center gap-2">
                <ShieldCheck className="w-3.5 h-3.5 text-amber-500/80" />
                <span>Min. 100 Item Sample Target</span>
              </li>
              <li className="flex items-center gap-2">
                <Database className="w-3.5 h-3.5 text-stone-400" />
                <span>Multi-Source Cross-Deduplication</span>
              </li>
              <li className="flex items-center gap-2">
                <Newspaper className="w-3.5 h-3.5 text-stone-400" />
                <span>Traceable Primary Citations</span>
              </li>
            </ul>
          </div>

          {/* Colophon & Maintenance */}
          <div>
            <h4 className="font-mono text-xs font-semibold text-stone-300 uppercase tracking-widest mb-3">
              Colophon & System
            </h4>
            <ul className="space-y-2 text-xs font-mono text-stone-400">
              <li>
                <a
                  href="https://github.com/CitrusCandy/New-Vantage"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-amber-400 transition-colors flex items-center gap-1"
                >
                  <GitBranch className="w-3.5 h-3.5" />
                  GitHub Repository
                  <ExternalLink className="w-2.5 h-2.5 ml-0.5 opacity-60" />
                </a>
              </li>
              <li>
                <Link
                  href="/ops"
                  className="text-stone-500 hover:text-stone-300 transition-colors flex items-center gap-1"
                >
                  <span>System Diagnostics (Admin)</span>
                </Link>
              </li>
              <li>
                <span className="text-stone-600">Engine: FastAPI + Next.js</span>
              </li>
            </ul>
          </div>
        </div>

        <div className="pt-8 border-t border-stone-800/80 flex flex-col sm:flex-row items-center justify-between text-xs text-stone-500 gap-4 font-serif">
          <p>© {new Date().getFullYear()} The Vantage Gazette. Perspectives represent sampled public discourse, not scientific polling.</p>
          <div className="flex items-center gap-4 font-mono text-[11px]">
            <span className="inline-flex items-center gap-1.5 text-emerald-500/90">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Automated Intake Active
            </span>
          </div>
        </div>
      </div>
    </footer>
  );
};
