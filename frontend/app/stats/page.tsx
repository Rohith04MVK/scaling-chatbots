"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import PulseDot from "@/components/PulseDot";
import StatsChart from "@/components/StatsChart";
import TokenChart from "@/components/TokenChart";
import LogsTable from "@/components/LogsTable";
import { api, Dashboard } from "@/lib/api";

export default function StatsPage() {
  const [timeWindow, setTimeWindow] = useState("1h");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const windowMinutes: Record<string, number> = {
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
  };

  useEffect(() => {
    async function loadDashboard() {
      setIsLoading(true);
      try {
        setDashboard(await api.getDashboard(windowMinutes[timeWindow]));
        setError(null);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Unable to load telemetry.");
      } finally {
        setIsLoading(false);
      }
    }
    void loadDashboard();
  }, [timeWindow]);

  const stats = dashboard?.summary ?? {
    request_count: 0,
    avg_latency_ms: 0,
    error_rate: 0,
    total_tokens: 0,
  };
  const { chartData, series } = useMemo(() => {
    const points = new Map<string, Record<string, string | number>>();
    for (const point of dashboard?.latency_points ?? []) {
      const time = point.timestamp;
      const key = `${point.provider} / ${point.model}`;
      const row = points.get(time) ?? { time };
      row[key] = Math.round(point.avg_latency_ms);
      points.set(time, row);
    }
    return {
      chartData: Array.from(points.values()),
      series: Array.from(new Set((dashboard?.latency_points ?? []).map(
        (point) => `${point.provider} / ${point.model}`,
      ))),
    };
  }, [dashboard]);
  const tokenData = (dashboard?.groups ?? []).map((group) => ({
    model: `${group.provider} / ${group.model}`,
    tokens: group.total_tokens,
  }));

  const statItems = [
    { label: "Total Requests", value: stats.request_count, unit: "" },
    { label: "Avg Latency", value: Math.round(stats.avg_latency_ms), unit: "ms" },
    {
      label: "Tokens Processed",
      value: (stats.total_tokens / 1000).toFixed(1) + "k",
      unit: "",
    },
    { label: "Error Rate", value: (stats.error_rate * 100).toFixed(1), unit: "%" },
  ];

  return (
    <div className="min-h-screen bg-paper">
      {/* Top Nav */}
      <div className="h-14 border-b border-border flex items-center justify-between px-8">
        <div className="flex items-center gap-2.5">
          <PulseDot />
          <h1 className="font-display text-lg font-semibold tracking-tight">
            inferlog
          </h1>
        </div>
        <div className="flex gap-6">
          <Link
            href="/"
            className="bg-transparent border-none font-body text-[13px] text-muted hover:text-ink transition-colors"
          >
            Chat
          </Link>
          <span className="nav-link active font-body text-[13px] font-medium text-ink">
            Stats
          </span>
        </div>
      </div>

      <div className="px-8 py-8 max-w-[1400px] mx-auto">
        {/* Header row */}
        <div className="flex justify-between items-baseline mb-8">
          <div>
            <h2 className="font-display text-[28px] font-semibold tracking-tight mb-1">
              Inference Overview
            </h2>
            <p className="text-muted text-sm">
              Real-time telemetry across all providers
            </p>
          </div>
          <div className="flex gap-1">
            {["15m", "1h", "6h", "24h"].map((t) => (
              <button
                key={t}
                onClick={() => setTimeWindow(t)}
                className="px-3.5 py-1.5 font-mono text-xs border border-border transition-colors cursor-pointer"
                style={{
                  background: timeWindow === t ? "var(--ink)" : "transparent",
                  color: timeWindow === t ? "var(--paper)" : "var(--muted)",
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {error && <p className="mb-6 text-sm text-error">{error}</p>}

        {/* Stats Grid */}
        <div
          className="grid grid-cols-4 mb-10"
          style={{ gap: 1, background: "var(--border)" }}
        >
          {statItems.map((stat, i) => (
            <div key={i} className="bg-paper px-5 py-6">
              <div className="font-mono text-[10px] uppercase tracking-widest text-muted mb-2">
                {stat.label}
              </div>
              <div
                className="font-display text-[32px] font-semibold tracking-tight"
                style={{
                  color:
                    stat.label === "Error Rate" && Number(stat.value) > 5
                      ? "var(--error)"
                      : "var(--ink)",
                }}
              >
                {stat.value}
                {stat.unit && (
                  <span className="text-base ml-1 text-muted">{stat.unit}</span>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-[2fr_1fr] gap-8 mb-10">
          {/* Latency over time */}
          <div>
            <h3 className="font-display text-base font-semibold mb-4">
              Latency by Model
            </h3>
            <StatsChart data={chartData} series={series} />
            <div className="flex flex-wrap gap-x-5 gap-y-2 mt-3 font-mono text-[11px]">
              {series.map((name, index) => (
                <span key={name} className="flex items-center gap-1.5" title={name}>
                  <span
                    className="w-3 h-0.5 inline-block"
                    style={{ background: ["#E85D3F", "#2D6A4F", "#6B6560", "#C0392B", "#6D5BD0"][index % 5] }}
                  />
                  {name}
                </span>
              ))}
            </div>
          </div>

          {/* Token distribution */}
          <div>
            <h3 className="font-display text-base font-semibold mb-4">
              Token Distribution
            </h3>
            <TokenChart data={tokenData} />
          </div>
        </div>

        {/* Logs Table */}
        <div>
          <h3 className="font-display text-base font-semibold mb-4">
            Recent Inference Logs
          </h3>
          {isLoading ? (
            <p className="text-sm text-muted">Loading telemetry…</p>
          ) : (
            <LogsTable logs={dashboard?.logs ?? []} />
          )}
        </div>
      </div>
    </div>
  );
}
