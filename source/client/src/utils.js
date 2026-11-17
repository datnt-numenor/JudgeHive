export const stateName = { queued: "Đang chờ", judging: "Đang chấm", done: "Hoàn tất", idle: "Rảnh" };
export const date = (value) => (value ? new Date(value).toLocaleString("vi-VN") : "—");
export const percent = (rate) => (rate === null ? "—" : `${Math.round(rate * 1000) / 10}%`);

// Độ khó do server trả (easy/medium/hard); rank dùng để sắp xếp Dễ → Khó.
export const difficulties = {
  easy: { label: "Dễ", rank: 0 },
  medium: { label: "Trung bình", rank: 1 },
  hard: { label: "Khó", rank: 2 },
};

// Trạng thái của chính người đang xem (server tính): đã AC, đã nộp nhưng chưa AC, chưa nộp.
export const problemStatus = {
  solved: { icon: "✓", label: "Đã giải" },
  attempted: { icon: "○", label: "Đã thử" },
};
