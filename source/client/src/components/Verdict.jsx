import React from "react";

// Mỗi verdict một màu (CSS .v-ac, .v-wa, ...); bài chưa có kết quả hiện "—".
export function Verdict({ value }) {
  return <span className={`verdict v-${(value || "none").toLowerCase()}`}>{value || "—"}</span>;
}
