"use client";

const LEVELS = ["", "Initial", "Repeatable", "Defined", "Managed", "Optimizing"];

// Canonical framework JSON is authored by hand and by the ingestion pipeline, so
// question_type arrives in mixed case ("yes_no" vs "YES_NO"). Normalise before
// dispatching, otherwise a lowercase type silently falls through to the free-text
// textarea and a yes/no question renders as a paragraph box.
const norm = (s) => String(s || "").trim().toUpperCase();

// Stored answers come from the AI pre-fill ("YES"/"NO"/"PARTIAL"), from imports,
// and from older records — compare case-insensitively so a saved answer always
// shows as selected instead of looking unanswered.
const same = (a, b) => String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase();

export default function QuestionInput({ question, value, onChange, readOnly }) {
  const type = norm(question.question_type) || "YES_NO";

  if (readOnly) {
    return (
      <div className="px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-600">
        {value || <span className="text-slate-400 italic">No response recorded</span>}
      </div>
    );
  }

  if (type === "YES_NO") {
    // PARTIAL is a first-class answer: the AI pre-fill emits it and the scorer
    // grades it 0.5, so it needs a control here or a pre-filled PARTIAL answer
    // would be invisible and unselectable.
    const OPTS = [
      { key: "YES",     on: "border-emerald-400 bg-emerald-50 text-emerald-700 shadow-sm" },
      { key: "PARTIAL", on: "border-amber-400 bg-amber-50 text-amber-700 shadow-sm" },
      { key: "NO",      on: "border-rose-400 bg-rose-50 text-rose-600 shadow-sm" },
    ];
    return (
      <div className="flex gap-3">
        {OPTS.map(({ key, on }) => (
          <button key={key} onClick={() => onChange(key)}
            className={`flex-1 py-3 rounded-xl border-2 text-sm font-bold transition-all duration-150 ${
              same(value, key)
                ? on
                : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:bg-slate-50"}`}>
            {key}
          </button>
        ))}
      </div>
    );
  }

  if (type === "MULTI_CHOICE" || type === "PRE_ASSESSMENT") {
    const choices = question.choices || ["Implemented", "Partially Implemented", "Not Implemented"];
    return (
      <div className="space-y-2">
        {choices.map((c) => (
          <button key={c} onClick={() => onChange(c)}
            className={`w-full text-left px-4 py-3 rounded-xl border text-sm transition-all duration-150 ${
              same(value, c)
                ? "border-blue-400 bg-blue-50 text-blue-800 font-semibold shadow-sm"
                : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50"}`}>
            <span className={`inline-block w-3.5 h-3.5 rounded-full border-2 mr-2.5 transition-all ${
              same(value, c) ? "border-blue-500 bg-blue-500" : "border-slate-300"}`} />
            {c}
          </button>
        ))}
      </div>
    );
  }

  if (type === "SCALE_1_5" || type === "SCALE") {
    const n = parseInt(value) || 0;
    return (
      <div className="space-y-3">
        <div className="flex gap-2">
          {[1, 2, 3, 4, 5].map((v) => (
            <button key={v} onClick={() => onChange(String(v))}
              className={`flex-1 py-3 rounded-xl border-2 font-bold text-sm transition-all duration-150 ${
                n === v
                  ? "border-blue-500 bg-blue-700 text-white shadow-sm shadow-blue-700/30"
                  : "border-slate-200 bg-white text-slate-500 hover:border-blue-200 hover:text-blue-600"}`}>
              {v}
            </button>
          ))}
        </div>
        {n > 0 && (
          <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 rounded-xl">
            <span className="text-[10px] font-bold text-blue-600 uppercase tracking-wider">Level {n}:</span>
            <span className="text-xs font-semibold text-blue-700">{LEVELS[n]}</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Describe your implementation…"
      rows={3}
      className="w-full rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 outline-none focus:border-blue-400 focus:bg-white resize-none leading-relaxed transition-all placeholder:text-slate-300"
    />
  );
}
