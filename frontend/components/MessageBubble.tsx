"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Message } from "@/lib/api";

interface MessageBubbleProps {
  message: Message;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div
      className="msg-appear flex mb-5"
      style={{ justifyContent: isUser ? "flex-end" : "flex-start" }}
    >
      <div className="max-w-[85%]">
        <div
          className="px-[18px] py-3.5 leading-relaxed text-sm"
          style={{
            background: isUser ? "var(--ink)" : "var(--surface)",
            color: isUser ? "var(--paper)" : "var(--ink)",
          }}
        >
          {isUser ? (
            <span className="whitespace-pre-wrap">{message.content}</span>
          ) : (
            <div className="markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </div>
          )}
        </div>
        <div
          className="font-mono text-[11px] text-muted mt-1.5"
          style={{ textAlign: isUser ? "right" : "left" }}
        >
          {new Date(message.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </div>
      </div>
    </div>
  );
}
