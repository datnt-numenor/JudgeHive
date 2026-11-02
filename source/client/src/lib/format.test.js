import { describe, expect, it } from "vitest";
import { ApiError } from "./api.js";
import { errorText, waitSeconds } from "./format.js";

describe("errorText cho lỗi nộp bài do tải (README 3.2, 3.5)", () => {
  it("TOO_MANY_PENDING nói lý do, gợi ý đợi một bài chấm xong, kèm thời gian khi có Retry-After", () => {
    const text = errorText(new ApiError(429, "TOO_MANY_PENDING", "m", 5));
    expect(text).toContain("quá nhiều bài chờ chấm");
    expect(text).toContain("đợi một bài chấm xong");
    expect(text).toContain("5 giây");
    expect(errorText(new ApiError(429, "TOO_MANY_PENDING", "m"))).not.toContain("giây");
  });

  it("QUEUE_FULL báo hàng đợi đầy, không gọi là lỗi máy chủ", () => {
    const text = errorText(new ApiError(503, "QUEUE_FULL", "m", 10));
    expect(text).toContain("quá nhiều bài chờ chấm");
    expect(text).toContain("10 giây");
    expect(text).not.toContain("lỗi");
    expect(errorText(new ApiError(503, "QUEUE_FULL", "m"))).toContain("Thử lại sau.");
  });

  it("503 trơn (BAD_RESPONSE) báo quá tải; 500 và 502 vẫn là lỗi máy chủ", () => {
    expect(errorText(new ApiError(503, "BAD_RESPONSE", "HTTP 503"))).toBe("Máy chủ đang quá tải. Thử lại sau.");
    expect(errorText(new ApiError(500, "INTERNAL_ERROR", "m"))).toBe("Máy chủ gặp lỗi. Thử lại sau.");
    expect(errorText(new ApiError(502, "BAD_RESPONSE", "m"))).toBe("Máy chủ gặp lỗi. Thử lại sau.");
  });

  it("RATE_LIMITED giữ nguyên thông báo cũ", () => {
    expect(errorText(new ApiError(429, "RATE_LIMITED", "m", 7))).toBe("Quá nhiều lần thử. Thử lại sau 7 giây.");
  });
});

describe("waitSeconds", () => {
  it("trả Retry-After cho ba mã, kể cả 429 và 503", () => {
    expect(waitSeconds(new ApiError(429, "RATE_LIMITED", "m", 7))).toBe(7);
    expect(waitSeconds(new ApiError(429, "TOO_MANY_PENDING", "m", 5))).toBe(5);
    expect(waitSeconds(new ApiError(503, "QUEUE_FULL", "m", 10))).toBe(10);
  });

  it("null khi không có Retry-After, hoặc khi là lỗi khác (kể cả 503 trơn có header)", () => {
    expect(waitSeconds(new ApiError(429, "TOO_MANY_PENDING", "m"))).toBeNull();
    expect(waitSeconds(new ApiError(503, "QUEUE_FULL", "m"))).toBeNull();
    expect(waitSeconds(new ApiError(503, "BAD_RESPONSE", "m", 3))).toBeNull();
    expect(waitSeconds(new ApiError(404, "PROBLEM_NOT_FOUND", "m"))).toBeNull();
    expect(waitSeconds(null)).toBeNull();
    expect(waitSeconds(new TypeError("x"))).toBeNull();
  });
});
