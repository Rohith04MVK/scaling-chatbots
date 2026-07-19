"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
interface StatsChartProps {
  data: Array<Record<string, number | string>>;
  series: string[];
}

const COLORS = ["#E85D3F", "#2D6A4F", "#6B6560", "#C0392B", "#6D5BD0"];

export default function StatsChart({ data, series }: StatsChartProps) {
  if (!data.length || !series.length) {
    return (
      <div className="h-[280px] border border-dashed border-border flex items-center justify-center text-sm text-muted">
        No latency data in this time window.
      </div>
    );
  }

  return (
    <div className="h-[280px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#D4CFC8"
            vertical={false}
          />
          <XAxis
            dataKey="time"
            tick={{
              fontSize: 11,
              fontFamily: "var(--font-ibm-plex-mono)",
              fill: "#6B6560",
            }}
            axisLine={{ stroke: "#D4CFC8" }}
            tickLine={false}
            minTickGap={36}
            tickFormatter={(value: string) =>
              new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            }
          />
          <YAxis
            tick={{
              fontSize: 11,
              fontFamily: "var(--font-ibm-plex-mono)",
              fill: "#6B6560",
            }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `${v}ms`}
          />
          <Tooltip
            contentStyle={{
              background: "var(--ink)",
              border: "none",
              fontFamily: "var(--font-ibm-plex-mono)",
              fontSize: 12,
              color: "var(--paper)",
            }}
            itemStyle={{ color: "var(--paper)" }}
            labelFormatter={(value) =>
              new Date(String(value)).toLocaleString([], {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })
            }
          />
          {series.map((name, index) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              name={name}
              stroke={COLORS[index % COLORS.length]}
              strokeWidth={2}
              dot={{ r: 3, strokeWidth: 1 }}
              activeDot={{ r: 5 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
