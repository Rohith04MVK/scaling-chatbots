"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import MessageBubble from "@/components/MessageBubble";
import LoadingDots from "@/components/LoadingDots";
import PulseDot from "@/components/PulseDot";
import {
  api,
  ApiError,
  Conversation,
  ConversationDetail,
  Message,
  Provider,
} from "@/lib/api";

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConv, setActiveConv] = useState<string | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const activeConversation = conversations.find((conversation) => conversation.id === activeConv);
  const modelOptions =
    model && !models.includes(model) ? [model, ...models] : models;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    async function load() {
      try {
        const [conversationList, providerResponse] = await Promise.all([
          api.listConversations(),
          api.getProviders(),
        ]);
        setConversations(conversationList);
        setProviders(providerResponse.providers);
        const available = providerResponse.providers.find(
          (provider) => provider.configured || !provider.requires_api_key,
        );
        if (available) {
          setProviderId(available.id);
          setModel(available.default_model);
        }
        if (conversationList[0]) {
          await selectConversation(conversationList[0].id);
        }
      } catch (loadError) {
        setError(messageFor(loadError));
      }
    }
    void load();
  }, []);

  useEffect(() => {
    if (!providerId || !selectedProvider) return;
    if (selectedProvider.requires_api_key && !apiKey.trim()) {
      setModels([]);
      setModelsLoading(false);
      return;
    }

    const controller = new AbortController();
    const delayMs = selectedProvider.requires_api_key ? 400 : 0;
    const timer = window.setTimeout(() => {
      void (async () => {
        setModelsLoading(true);
        try {
          const response = await api.getProviderModels(
            providerId,
            selectedProvider.requires_api_key ? apiKey.trim() : undefined,
          );
          if (controller.signal.aborted) return;
          setModels(response.models);
          setModel((current) =>
            current && response.models.includes(current) ? current : response.default_model,
          );
        } catch (loadError) {
          if (controller.signal.aborted) return;
          setModels([]);
          setError(messageFor(loadError));
        } finally {
          if (!controller.signal.aborted) setModelsLoading(false);
        }
      })();
    }, delayMs);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [providerId, apiKey, selectedProvider]);

  function messageFor(cause: unknown) {
    return cause instanceof ApiError || cause instanceof Error
      ? cause.message
      : "Unable to reach the API.";
  }

  function applyConversation(detail: ConversationDetail) {
    setMessages(detail.messages);
    setActiveConv(detail.id);
    setConversations((current) => {
      const conversation = {
        id: detail.id,
        created_at: detail.created_at,
        status: detail.status,
        title: detail.title,
        provider: detail.provider,
        model: detail.model,
      };
      return [conversation, ...current.filter((item) => item.id !== detail.id)];
    });
  }

  async function selectConversation(id: string) {
    streamControllerRef.current?.abort();
    setError(null);
    try {
      const detail = await api.getConversation(id);
      setMessages(detail.messages);
      setActiveConv(detail.id);
      if (detail.provider) setProviderId(detail.provider);
      if (detail.model) setModel(detail.model);
    } catch (loadError) {
      setError(messageFor(loadError));
    }
  }

  async function handleSend() {
    if (!input.trim() || isLoading || activeConversation?.status === "cancelled") return;
    if (selectedProvider?.requires_api_key && !apiKey) {
      setError(`Enter an API key for ${selectedProvider.label} before sending.`);
      return;
    }
    const message = input.trim();
    setInput("");
    setIsLoading(true);
    setError(null);
    const temporaryAssistantId = `streaming-${crypto.randomUUID()}`;
    const temporaryUserId = `pending-${crypto.randomUUID()}`;
    const now = new Date().toISOString();
    const optimisticMessages: Message[] = [
      {
        id: temporaryUserId,
        conversation_id: activeConv ?? "",
        role: "user",
        content: message,
        created_at: now,
        sequence_number: messages.length,
      },
      {
        id: temporaryAssistantId,
        conversation_id: activeConv ?? "",
        role: "assistant",
        content: "",
        created_at: now,
        sequence_number: messages.length + 1,
      },
    ];
    setMessages((current) => [...current, ...optimisticMessages]);
    const controller = new AbortController();
    streamControllerRef.current = controller;
    let streamedConversationId = activeConv;
    try {
      const options = {
        message,
        provider: providerId || undefined,
        model: model || undefined,
        api_key: apiKey || undefined,
      };
      const handlers = {
        onConversation: (conversation: Conversation) => {
          streamedConversationId = conversation.id;
          setActiveConv(conversation.id);
          setConversations((current) => [
            conversation,
            ...current.filter((item) => item.id !== conversation.id),
          ]);
        },
        onDelta: (content: string) => {
          setMessages((current) =>
            current.map((item) =>
              item.id === temporaryAssistantId
                ? { ...item, content: item.content + content }
                : item,
            ),
          );
        },
      };
      if (activeConv) {
        await api.sendMessageStream(activeConv, options, handlers, controller.signal);
      } else {
        await api.createConversationStream(options, handlers, controller.signal);
      }
      if (streamedConversationId) {
        applyConversation(await api.getConversation(streamedConversationId));
      }
    } catch (sendError) {
      if (sendError instanceof DOMException && sendError.name === "AbortError") return;
      setError(messageFor(sendError));
      if (streamedConversationId) {
        try {
          applyConversation(await api.getConversation(streamedConversationId));
        } catch {
          // Preserve the streamed content if refreshing the durable conversation fails.
        }
      }
    } finally {
      setIsLoading(false);
      if (streamControllerRef.current === controller) streamControllerRef.current = null;
    }
  }

  function handleNewConv() {
    streamControllerRef.current?.abort();
    setMessages([]);
    setActiveConv(null);
    setError(null);
  }

  async function handleCancel() {
    if (!activeConv) return;
    try {
      streamControllerRef.current?.abort();
      const conversation = await api.cancelConversation(activeConv);
      setConversations((current) =>
        current.map((item) => (item.id === conversation.id ? conversation : item)),
      );
      setError(null);
    } catch (cancelError) {
      setError(messageFor(cancelError));
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this conversation and its logged inference history?")) return;
    try {
      if (id === activeConv) streamControllerRef.current?.abort();
      await api.deleteConversation(id);
      setConversations((current) => current.filter((conversation) => conversation.id !== id));
      if (id === activeConv) handleNewConv();
      setError(null);
    } catch (deleteError) {
      setError(messageFor(deleteError));
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        conversations={conversations}
        activeId={activeConv}
        onSelect={(id) => void selectConversation(id)}
        onNew={handleNewConv}
        onDelete={(id) => void handleDelete(id)}
      />
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="h-14 border-b border-border flex items-center justify-between px-6 shrink-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-muted uppercase tracking-wide">
              Model
            </span>
            <select
              value={providerId}
              onChange={(event) => {
                const nextProvider = providers.find((provider) => provider.id === event.target.value);
                setProviderId(event.target.value);
                setModel(nextProvider?.default_model ?? "");
                setModels([]);
                setApiKey("");
              }}
              className="bg-transparent text-[13px] font-medium border-none"
              aria-label="Provider"
            >
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.label}
                </option>
              ))}
            </select>
            <select
              value={model}
              onChange={(event) => setModel(event.target.value)}
              className="bg-transparent text-[13px] font-medium border-none"
              aria-label="Model"
              disabled={modelsLoading || modelOptions.length === 0}
            >
              {modelsLoading && (
                <option value={model}>{model || "Loading models…"}</option>
              )}
              {!modelsLoading && modelOptions.length === 0 && (
                <option value="">
                  {selectedProvider?.requires_api_key && !apiKey.trim()
                    ? "Enter API key to load models"
                    : "No models available"}
                </option>
              )}
              {!modelsLoading &&
                modelOptions.map((providerModel) => (
                  <option key={providerModel} value={providerModel}>
                    {providerModel}
                  </option>
                ))}
            </select>
            <span className="font-mono text-[10px] text-accent bg-accent/5 px-1.5 py-0.5">
              LIVE
            </span>
          </div>
          <div className="flex gap-5">
            <Link
              href="/stats"
              className="bg-transparent border-none font-body text-[13px] text-muted hover:text-ink transition-colors"
            >
              Stats
            </Link>
            {activeConv && activeConversation?.status === "active" && (
              <button
                onClick={() => void handleCancel()}
                className="bg-transparent border-none font-body text-[13px] text-muted hover:text-ink transition-colors cursor-pointer"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
        {selectedProvider?.requires_api_key && (
          <div className="px-6 py-2 border-b border-border bg-surface flex items-center gap-3">
            <label htmlFor="provider-api-key" className="font-mono text-[10px] uppercase text-muted">
              {selectedProvider.label} API key
            </label>
            <input
              id="provider-api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              className="flex-1 bg-paper border border-border px-2 py-1 text-xs"
              placeholder="Used only for this session"
            />
          </div>
        )}
        {error && <div className="px-6 py-2 text-sm text-error border-b border-border">{error}</div>}

        {/* Messages */}
        <div className="flex-1 overflow-auto px-6 pt-6">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-muted">
              <div className="mb-4">
                <PulseDot />
              </div>
              <p className="font-display text-xl font-medium text-ink mb-2">
                What are we debugging today?
              </p>
              <p className="text-sm">
                Start a conversation to trace inference calls across providers.
              </p>
            </div>
          ) : (
            messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
          )}
          {isLoading && messages[messages.length - 1]?.content === "" && <LoadingDots />}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-6 pt-4 pb-6 border-t border-border shrink-0">
          <div className="flex gap-3 items-end">
            <textarea
              className="chat-input flex-1 px-4 py-3 border border-border bg-paper font-body text-sm resize-none leading-relaxed"
              style={{ height: 52 }}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              disabled={isLoading || activeConversation?.status === "cancelled"}
              placeholder={
                activeConversation?.status === "cancelled"
                  ? "This conversation has been cancelled"
                  : "Ask about inference logs, compare models, debug latency..."
              }
            />
            <button
              onClick={() => void handleSend()}
              disabled={!input.trim() || isLoading || activeConversation?.status === "cancelled"}
              className="px-6 py-3.5 font-body text-[13px] font-semibold border-none cursor-pointer transition-colors"
              style={{
                background:
                  input.trim() && !isLoading && activeConversation?.status !== "cancelled"
                    ? "var(--accent)"
                    : "var(--surface)",
                color:
                  input.trim() && !isLoading && activeConversation?.status !== "cancelled"
                    ? "white"
                    : "var(--muted)",
                cursor:
                  input.trim() && !isLoading && activeConversation?.status !== "cancelled"
                    ? "pointer"
                    : "not-allowed",
              }}
            >
              Send
            </button>
          </div>
          <div className="font-mono text-[10px] text-muted mt-2 uppercase tracking-wider">
            Shift + Enter for new line · All requests logged
          </div>
        </div>
      </div>
    </div>
  );
}
