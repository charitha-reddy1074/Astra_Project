"use client";

import { useState, useRef, useEffect } from "react";
import { Send, Sparkles, BookOpen } from "lucide-react";
import { api } from "@/lib/api";

const STATIC_SUGGESTIONS = [
  "What controls does NIST CSF require for access management?",
  "How does ISO 27001 A.5.9 relate to CIS Control 1?",
  "What evidence is typically needed for identity and credential management?",
  "Explain the difference between NIST Govern and Identify functions.",
];

function CitationChip({ citation }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-white border border-slate-200 rounded-lg text-[10px] text-slate-600 font-semibold shadow-sm">
      <BookOpen size={9} className="text-blue-500" />
      [{citation.index}] {citation.framework} · {citation.control_code}
    </span>
  );
}

function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 text-xs font-bold shadow-sm ${
        isUser ? "brand-tile text-white" : "icon-tile"}`}>
        {isUser ? "U" : <Sparkles size={13} />}
      </div>
      <div className={`max-w-[80%] space-y-2 ${isUser ? "items-end flex flex-col" : ""}`}>
        <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "brand-tile text-white rounded-tr-none"
            : "bg-white border border-slate-100 text-slate-800 rounded-tl-none shadow-sm"}`}>
          {msg.content}
        </div>
        {msg.citations?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1">
            {msg.citations.map((c) => <CitationChip key={c.index} citation={c} />)}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AskCyberAIView() {
  const [messages,    setMessages]    = useState([{
    role: "assistant",
    content: "Hi — I'm the Cyber Assessment Agent. Ask me anything about NIST CSF, ISO 27001, CIS Controls, or Market Assessment. I'll ground my answers in the framework documentation and cite my sources.",
    citations: [],
  }]);
  const [input,       setInput]       = useState("");
  const [loading,     setLoading]     = useState(false);
  const [suggestions, setSuggestions] = useState(STATIC_SUGGESTIONS);
  const bottomRef                     = useRef(null);

  useEffect(() => {
    Promise.allSettled([api.assessments.list(), api.frameworks.list()]).then(([a, f]) => {
      const asmts = a.status === "fulfilled" ? a.value : [];
      const fws   = f.status === "fulfilled" ? f.value : [];
      const dynamic = [];
      const inProg = asmts.filter(x => x.status === "in_progress");
      if (inProg.length > 0)   dynamic.push(`What are the key gaps in the "${inProg[0].name}" assessment?`);
      if (fws.length > 0)      dynamic.push(`Summarize ${fws[0].name} requirements for identity and access management.`);
      const done = asmts.filter(x => x.status === "completed");
      if (done.length > 0)     dynamic.push(`What findings are most critical in the "${done[0].name}" assessment?`);
      setSuggestions([...dynamic, ...STATIC_SUGGESTIONS].slice(0, 4));
    });
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const send = async (text) => {
    const msg = text || input;
    if (!msg.trim() || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      const res = await api.ai.chat(msg, history);
      setMessages((prev) => [...prev, { role: "assistant", content: res.answer, citations: res.citations || [] }]);
    } catch (e) {
      setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${e.message}`, citations: [] }]);
    } finally { setLoading(false); }
  };

  return (
    <div className="p-6 max-w-3xl mx-auto flex flex-col fade-in-up" style={{ height: "calc(100vh - 3.5rem)" }}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-5 shrink-0">
        <div className="w-9 h-9 brand-tile rounded-xl flex items-center justify-center shadow-sm">
          <Sparkles size={16} className="text-white" />
        </div>
        <div>
          <h2 className="text-sm font-bold text-slate-900">Ask the Agent</h2>
          <p className="text-[10px] text-slate-400">Grounded in NIST CSF · ISO 27001 · CIS Controls · Market Assessment</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-[10px] text-slate-400 font-medium">Online</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-5 pb-4 pr-1">
        {messages.map((m, i) => <Message key={i} msg={m} />)}
        {loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-xl brand-tile flex items-center justify-center shrink-0">
              <Sparkles size={13} className="text-white animate-pulse" />
            </div>
            <div className="bg-white border border-slate-100 rounded-2xl rounded-tl-none px-4 py-3 shadow-sm">
              <div className="flex items-center gap-1.5">
                {[0, 150, 300].map((delay) => (
                  <span key={delay} className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: `${delay}ms` }} />
                ))}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Suggestions */}
      {messages.length <= 1 && (
        <div className="grid grid-cols-2 gap-2 mb-3 shrink-0">
          {suggestions.map((s) => (
            <button key={s} onClick={() => send(s)}
              className="text-left p-3.5 rounded-xl border border-slate-200 bg-white hover:border-blue-200 hover:bg-blue-50/60 text-xs text-slate-600 transition-all leading-relaxed shadow-sm hover:shadow-md">
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="bg-white border border-slate-200 rounded-2xl flex items-end gap-3 p-4 shrink-0 shadow-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-50 transition-all">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Ask about any cybersecurity control, framework, or compliance requirement…"
          rows={1}
          className="flex-1 outline-none resize-none text-sm text-slate-800 bg-transparent leading-relaxed py-0.5 max-h-32 overflow-y-auto placeholder:text-slate-400"
        />
        <button
          onClick={() => send()}
          disabled={!input.trim() || loading}
          className="btn-primary shrink-0 p-2.5 text-xs"
          style={{ borderRadius: "12px" }}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
