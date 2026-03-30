'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { ChatMessage } from '@/lib/types';
import { MessageCircle, Send, Loader2, RotateCcw } from 'lucide-react';
import Markdown from 'react-markdown';

interface Props {
  activityId: string;
}

export default function CoachChat({ activityId }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [expanded, setExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  // Load chat history on mount
  useEffect(() => {
    async function loadHistory() {
      try {
        const res = await fetch(`/api/activities/${activityId}/coach-chat`);
        if (res.ok) {
          const data = await res.json();
          if (data.messages && data.messages.length > 0) {
            setMessages(data.messages);
            setExpanded(true);
          }
        }
      } catch {
        // Silently fail — chat history is optional
      }
    }
    loadHistory();
  }, [activityId]);

  useEffect(() => {
    if (expanded) scrollToBottom();
  }, [messages, streamingText, expanded, scrollToBottom]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || streaming) return;

    setInput('');
    setStreaming(true);
    setStreamingText('');

    // Optimistic user message
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      activity_id: activityId,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);

    try {
      const res = await fetch(`/api/activities/${activityId}/coach-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        throw new Error(`Chat failed (${res.status})`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response stream');

      const decoder = new TextDecoder();
      let fullResponse = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        // Parse SSE data lines
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;
            fullResponse += data;
            setStreamingText(fullResponse);
          }
        }
      }

      // Finalize: replace streaming text with a proper message
      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        activity_id: activityId,
        role: 'assistant',
        content: fullResponse,
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, assistantMsg]);
      setStreamingText('');
    } catch (err) {
      const errorMsg: ChatMessage = {
        id: crypto.randomUUID(),
        activity_id: activityId,
        role: 'assistant',
        content: err instanceof Error ? err.message : 'Something went wrong. Please try again.',
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, errorMsg]);
      setStreamingText('');
    } finally {
      setStreaming(false);
    }
  };

  const resetChat = async () => {
    try {
      await fetch(`/api/activities/${activityId}/coach-chat`, { method: 'DELETE' });
      setMessages([]);
      setStreamingText('');
    } catch {
      // Silently fail
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  if (!expanded) {
    return (
      <button
        onClick={() => {
          setExpanded(true);
          setTimeout(() => inputRef.current?.focus(), 100);
        }}
        className="w-full bg-white rounded-xl shadow-sm border border-gray-200 p-4 hover:border-blue-300 hover:shadow-md transition-all flex items-center justify-center gap-2 text-gray-600 hover:text-blue-600"
      >
        <MessageCircle className="w-4 h-4" />
        <span className="text-sm font-medium">Ask your coach a follow-up question</span>
      </button>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <MessageCircle className="w-4 h-4 text-blue-600" />
          Chat with Coach
        </h3>
        <div className="flex items-center gap-2">
          {messages.length > 0 && !streaming && (
            <button
              onClick={resetChat}
              className="text-xs text-gray-400 hover:text-red-500 flex items-center gap-1 transition-colors"
              title="Clear chat history"
            >
              <RotateCcw className="w-3 h-3" />
              Reset
            </button>
          )}
          {messages.length === 0 && (
            <button
              onClick={() => setExpanded(false)}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Close
            </button>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="max-h-96 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !streaming && (
          <p className="text-sm text-gray-400 text-center py-4">
            Ask about your workout, training plan, or anything about this session.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'user' ? (
              <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap bg-blue-600 text-white">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-gray-100 text-gray-800">
                <div className="prose prose-sm prose-gray max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
                  <Markdown>{msg.content}</Markdown>
                </div>
              </div>
            )}
          </div>
        ))}
        {streaming && streamingText && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-gray-100 text-gray-800">
              <div className="prose prose-sm prose-gray max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-sm">
                <Markdown>{streamingText}</Markdown>
              </div>
              <span className="inline-block w-1.5 h-4 bg-gray-400 ml-0.5 animate-pulse" />
            </div>
          </div>
        )}
        {streaming && !streamingText && (
          <div className="flex justify-start">
            <div className="rounded-lg px-3 py-2 text-sm bg-gray-100 text-gray-500 flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-100 p-3">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask your coach..."
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={streaming}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || streaming}
            className="rounded-lg bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
