"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
interface TokenChartProps {
  data: Array<{ model: string; tokens: number }>;
}

export default function TokenChart({ data }: TokenChartProps) {
  if (!data.length) {
    return (
      <div className="h-[280px] border border-dashed border-border flex items-center justify-center text-sm text-muted">
        No token data in this time window.
      </div>
    );
  }

  return (
    <div className="h-[280px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical">
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#D4CFC8"
            horizontal={false}
          />
          <XAxis
            type="number"
            tick={{
              fontSize: 11,
              fontFamily: "var(--font-ibm-plex-mono)",
              fill: "#6B6560",
            }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `${v / 1000}k`}
          />
          <YAxis
            type="category"
            dataKey="model"
            tick={{
              fontSize: 11,
              fontFamily: "var(--font-ibm-plex-mono)",
              fill: "#6B6560",
            }}
            axisLine={false}
            tickLine={false}
            width={170}
            tickFormatter={(value: string) =>
              value.length > 24 ? `${value.slice(0, 21)}…` : value
            }
          />
          <Tooltip
            contentStyle={{
              background: "var(--ink)",
              border: "none",
              fontFamily: "var(--font-ibm-plex-mono)",
              fontSize: 12,
              color: "var(--paper)",
            }}
            labelFormatter={(value) => String(value)}
            formatter={(value: number) => [value.toLocaleString(), "Tokens"]}
          />
          <Bar dataKey="tokens" fill="#E85D3F" radius={[0, 2, 2, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
