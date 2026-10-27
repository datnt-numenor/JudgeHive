// Nhãn hiển thị. Mọi chuỗi từ server vẫn đi qua JSX (escape mặc định), file này chỉ ánh xạ mã sang chữ.

export const LANGUAGES = [
  { value: "python", label: "Python" },
  { value: "cpp", label: "C++" },
  { value: "java", label: "Java" },
];

export const MAX_CODE_BYTES = 64 * 1024; // README 3.3; server mới là nơi quyết định (413 CODE_TOO_LARGE)

export function codeBytes(code) {
  return new TextEncoder().encode(code).length;
}

const VERDICTS = {
  AC: "Chấp nhận (AC)",
  WA: "Sai kết quả (WA)",
  TLE: "Quá thời gian (TLE)",
  MLE: "Quá bộ nhớ (MLE)",
  RE: "Lỗi lúc chạy (RE)",
  CE: "Lỗi biên dịch (CE)",
  SE: "Lỗi hệ thống chấm (SE)",
};

const RE_KINDS = {
  SIGSEGV: "truy cập bộ nhớ sai (SIGSEGV)",
  SIGFPE: "lỗi số học, ví dụ chia cho 0 (SIGFPE)",
  SIGABRT: "chương trình tự hủy (SIGABRT)",
  SIGKILL: "bị hệ thống dừng (SIGKILL)",
  OTHER_SIGNAL: "dừng bởi một tín hiệu khác",
  EXIT_NONZERO: "kết thúc với mã thoát khác 0",
};

export function verdictLabel(verdict) {
  return VERDICTS[verdict] ?? String(verdict);
}

export function reKindLabel(kind) {
  return RE_KINDS[kind] ?? String(kind);
}

export function stateLabel(state) {
  return { queued: "Đang chờ", judging: "Đang chấm", judged: "Đã chấm" }[state] ?? String(state);
}

export function formatTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("vi-VN");
}

/** KiB sang chuỗi dễ đọc. */
export function formatKib(kib) {
  return kib >= 1024 ? `${(kib / 1024).toFixed(1)} MiB` : `${kib} KiB`;
}

/** Thông báo cho lỗi API theo `code` (client rẽ nhánh theo code, README 3.2); `message` của server chỉ là phương án cuối. */
export function errorText(err) {
  if (!err) return "";
  if (err.name === "NetworkError") return err.kind === "timeout" ? "Máy chủ không trả lời kịp." : "Không kết nối được tới máy chủ.";
  switch (err.code) {
    case "INVALID_CREDENTIALS":
      return "Sai tên đăng nhập hoặc mật khẩu.";
    case "RATE_LIMITED":
      return err.retryAfter ? `Quá nhiều lần thử. Thử lại sau ${err.retryAfter} giây.` : "Quá nhiều lần thử. Thử lại sau.";
    case "CODE_TOO_LARGE":
      return "Mã nguồn vượt quá 64 KB.";
    case "BODY_TOO_LARGE":
      return "Dữ liệu gửi lên quá lớn.";
    case "PROBLEM_NOT_FOUND":
      return "Không tìm thấy đề bài.";
    case "SUBMISSION_NOT_FOUND":
      return "Không tìm thấy bài nộp.";
    case "INVALID_REQUEST":
      return "Dữ liệu không hợp lệ.";
    case "INTERNAL_ERROR":
      return "Máy chủ gặp lỗi. Thử lại sau.";
    default:
      return err.status >= 500 ? "Máy chủ gặp lỗi. Thử lại sau." : "Yêu cầu không thành công.";
  }
}
