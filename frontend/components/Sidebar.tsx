"use client";

import Link from "next/link";
import PulseDot from "./PulseDot";
import { Conversation } from "@/lib/api";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: SidebarProps) {
  return (
    <div className="w-[280px] border-r border-border h-screen flex flex-col bg-paper shrink-0">
      <div className="px-5 pt-6 pb-4">
        <div className="flex items-center gap-2.5 mb-6">
          <PulseDot />
          <h1 className="font-display text-lg font-semibold tracking-tight">
            inferlog
          </h1>
        </div>
        <button
          onClick={onNew}
          className="w-full px-3.5 py-2.5 bg-ink text-paper border-none font-body text-[13px] font-medium cursor-pointer flex items-center gap-2 hover:bg-ink/90 transition-colors"
        >
          <span className="text-base leading-none">+</span> New conversation
        </button>
      </div>

      <div className="flex-1 overflow-auto px-3">
        <div className="font-mono text-[10px] uppercase tracking-widest text-muted px-2 pt-3 pb-2">
          Recent
        </div>
        {conversations.map((conv) => (
          <div
            key={conv.id}
            onClick={() => onSelect(conv.id)}
            className="px-3 py-2.5 cursor-pointer mb-0.5 transition-colors"
            style={{
              borderLeft:
                activeId === conv.id
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
              background: activeId === conv.id ? "var(--surface)" : "transparent",
            }}
          >
            <div className="flex items-center gap-2">
              <div className="text-[13px] font-medium text-ink truncate flex-1">
                {conv.title ?? "Untitled conversation"}
              </div>
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(conv.id);
                }}
                className="bg-transparent border-none text-muted hover:text-error cursor-pointer px-1"
                aria-label={`Delete ${conv.title ?? "conversation"}`}
                title="Delete conversation"
              >
                ×
              </button>
            </div>
            <div className="font-mono text-[11px] text-muted mt-0.5 flex justify-between">
              <span>{conv.model ?? "No model"}</span>
              <span>{new Date(conv.created_at).toLocaleDateString()}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="px-5 py-4 border-t border-border font-mono text-[11px] text-muted">
        {conversations.length} conversations
      </div>
    </div>
  );
}
