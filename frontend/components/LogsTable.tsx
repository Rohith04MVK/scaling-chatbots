"use client";

import { DashboardLog } from "@/lib/api";

interface LogsTableProps {
  logs: DashboardLog[];
}

export default function LogsTable({ logs }: LogsTableProps) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Request ID</th>
          <th>Model</th>
          <th>Provider</th>
          <th className="text-right">Latency</th>
          <th className="text-right">Tokens In</th>
          <th className="text-right">Tokens Out</th>
          <th>Status</th>
          <th className="text-right">Time</th>
        </tr>
      </thead>
      <tbody>
        {logs.map((log) => (
          <tr key={log.id} className={log.status === "error" ? "error-row" : ""}>
            <td className="font-mono text-xs">{log.id.slice(0, 8)}</td>
            <td className="font-medium">{log.model}</td>
            <td className="text-muted">{log.provider}</td>
            <td className="font-mono text-right text-xs">
              {log.status === "error" ? "—" : `${log.latency_ms}ms`}
            </td>
            <td className="font-mono text-right text-xs">
              {log.input_tokens.toLocaleString()}
            </td>
            <td className="font-mono text-right text-xs">
              {log.output_tokens.toLocaleString()}
            </td>
            <td>
              {log.status === "success" ? (
                <span className="inline-flex items-center gap-1.5 text-success text-xs font-medium">
                  <span className="w-1.5 h-1.5 bg-success inline-block" />
                  OK
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-error text-xs font-medium">
                  <span className="w-1.5 h-1.5 bg-error inline-block" />
                  {log.error_message ?? "Failed"}
                </span>
              )}
            </td>
            <td className="font-mono text-right text-xs text-muted">
              {new Date(log.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
