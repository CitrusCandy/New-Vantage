"use client";

import React from "react";
import { Search, Sparkles, ArrowRight, X } from "lucide-react";

interface TopicSearchProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (query: string) => void;
  placeholder?: string;
  isSubmitting?: boolean;
}

export const TopicSearch: React.FC<TopicSearchProps> = ({
  value,
  onChange,
  onSubmit,
  placeholder = "Enter any topic or query to analyze public discourse across sources...",
  isSubmitting = false,
}) => {
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() && onSubmit && !isSubmitting) {
      onSubmit(value.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative w-full">
      <div className="relative flex items-center bg-[#0d1117] border-2 border-white/20 hover:border-white/40 focus-within:border-slate-100 rounded-xl shadow-2xl transition-all overflow-hidden">
        <div className="pl-4 sm:pl-5 text-slate-400">
          <Search className="w-5 h-5 text-slate-400" />
        </div>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={isSubmitting}
          className="w-full pl-3.5 pr-28 sm:pr-36 py-4 bg-transparent text-base sm:text-lg text-slate-100 placeholder-slate-500 focus:outline-none font-serif-body disabled:opacity-60"
        />

        {value && !isSubmitting && (
          <button
            type="button"
            onClick={() => onChange("")}
            className="absolute right-28 sm:right-36 p-2 text-slate-400 hover:text-white transition-colors"
            aria-label="Clear search"
          >
            <X className="w-4 h-4" />
          </button>
        )}

        <button
          type="submit"
          disabled={!value.trim() || isSubmitting}
          className="absolute right-2 sm:right-2.5 px-4 sm:px-5 py-2.5 bg-slate-100 hover:bg-white text-slate-900 rounded-lg text-xs sm:text-sm font-bold uppercase tracking-wider flex items-center gap-1.5 transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-md"
        >
          {isSubmitting ? (
            <>
              <span className="w-3.5 h-3.5 border-2 border-slate-900 border-t-transparent rounded-full animate-spin" />
              <span className="hidden xs:inline">Analyzing</span>
            </>
          ) : (
            <>
              <span>Analyze</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </>
          )}
        </button>
      </div>
    </form>
  );
};

