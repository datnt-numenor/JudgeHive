import React from "react";

// Mỗi verdict một màu (CSS .v-ac, .v-wa, ...); bài chưa có kết quả hiện "—".
export function Verdict({ value, full = false }) {
  const names = { AC: "✓ Accepted", WA: "Wrong Answer", TLE: "Time Limit Exceeded", MLE: "Memory Limit Exceeded", CE: "Compilation Error", RE: "Runtime Error", SE: "System Error" };
  return <span className={`verdict v-${(value || "none").toLowerCase()} ${full ? "verdict-full" : ""}`}>{full ? names[value] ?? "Chưa có kết quả" : value || "—"}</span>;
}
