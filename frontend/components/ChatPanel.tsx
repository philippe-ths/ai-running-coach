'use client';

import { useState } from 'react';
import { Send, MessageSquare } from 'lucide-react';

interface Props {
  activityId: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function ChatPanel({ activityId }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMsg = input.trim();
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, activity_id: activityId })
      });
      
      if (!res.ok) throw new Error("Chat failed");
      
      const data = await res.json();
      setMessages(prev => [...prev, { role: 'assistant', content: data.reply }]);
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { role: 'assistant', content: "Sorry, I couldn't reach the coach right now." }]);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) {
    return (
      <button 
        onClick={() => setIsOpen(true)}
        className="fixed bottom-6 right-6 bg-blue-600 text-white p-4 rounded-full shadow-lg hover:bg-blue-700 transition-colors z-50 flex items-center gap-2"
      >
        <MessageSquare size={24} />
        <span className="font-medium">Ask Coach</span>
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 w-full max-w-sm bg-white rounded-xl shadow-2xl border border-gray-200 flex flex-col z-50 h-[500px]">
      
      {/* Header */}
      <div className="bg-blue-600 text-white p-4 rounded-t-xl flex justify-between items-center">
        <h3 className="font-bold flex items-center gap-2">
          <MessageSquare size={18} /> Coach Chat
        </h3>
        <button onClick={() => setIsOpen(false)} className="hover:text-blue-200 text-xl font-bold">×</button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-gray-50">
        {messages.length === 0 && (
          <p className="text-center text-gray-500 text-sm mt-4">
            Ask me about this run, your metrics, or recovery tips!
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] p-3 rounded-lg text-sm ${
              m.role === 'user' 
                ? 'bg-blue-600 text-white rounded-br-none' 
                : 'bg-white border text-gray-800 rounded-bl-none shadow-sm'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
             <div className="bg-gray-200 text-gray-500 p-2 rounded-lg text-xs animate-pulse">Typing...</div>
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleSend} className="p-3 border-t bg-white rounded-b-xl flex gap-2">
        <input 
          type="text" 
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button 
          type="submit" 
          disabled={loading || !input.trim()}
          className="bg-blue-600 text-white p-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
            <Send size={18} />
        </button>
      </form>
    </div>
  );
}
