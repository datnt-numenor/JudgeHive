import { describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "./api.js";
import { BusyError, createSubmitter } from "./submitter.js";

const P = { problem_id: "sum", language: "python", code: "print(1)" };

function setup(replies) {
  let n = 0;
  const post = vi.fn(async () => {
    const r = replies.shift();
    if (r instanceof Error) throw r;
    return r ?? { submission_id: 1 };
  });
  const submitter = createSubmitter({ post, newToken: () => `t${++n}` });
  const tokens = () => post.mock.calls.map(([payload]) => payload.client_token);
  return { submitter, post, tokens };
}

describe("client_token", () => {
  it("mỗi lần nộp thành công dùng token mới, kể cả khi nội dung giống hệt", async () => {
    const { submitter, tokens } = setup([{ submission_id: 1 }, { submission_id: 2 }]);
    await submitter.submit(P);
    await submitter.submit(P);
    expect(tokens()).toEqual(["t1", "t2"]);
  });

  it.each([
    ["lỗi mạng", () => new NetworkError("network")],
    ["hết giờ chờ", () => new NetworkError("timeout")],
    ["5xx", () => new ApiError(502, "BAD_RESPONSE", "m")],
  ])("%s rồi thử lại cùng nội dung thì dùng lại token", async (_name, makeError) => {
    const { submitter, tokens } = setup([makeError(), { submission_id: 9 }]);
    await expect(submitter.submit(P)).rejects.toBeTruthy();
    await submitter.submit(P);
    expect(tokens()).toEqual(["t1", "t1"]);
  });

  it("sau lỗi mạng mà sửa mã (hoặc đổi đề, ngôn ngữ) thì phải dùng token mới", async () => {
    const { submitter, tokens } = setup([new NetworkError("network"), new NetworkError("network"), {}, {}]);
    await expect(submitter.submit(P)).rejects.toBeTruthy();
    await expect(submitter.submit({ ...P, code: "print(2)" })).rejects.toBeTruthy();
    await expect(submitter.submit({ ...P, code: "print(2)" })).resolves.toBeTruthy(); // cùng nội dung mới: giữ t2
    await submitter.submit({ ...P, language: "cpp", code: "print(2)" });
    expect(tokens()).toEqual(["t1", "t2", "t2", "t3"]);
  });

  it.each([
    ["400", new ApiError(400, "INVALID_REQUEST", "m")],
    ["413", new ApiError(413, "CODE_TOO_LARGE", "m")],
    ["429", new ApiError(429, "RATE_LIMITED", "m", 5)],
    ["404", new ApiError(404, "PROBLEM_NOT_FOUND", "m")],
  ])("lỗi %s (chắc chắn không tạo bài) thì lần sau dùng token mới", async (_name, error) => {
    const { submitter, tokens } = setup([error, {}]);
    await expect(submitter.submit(P)).rejects.toBe(error);
    await submitter.submit(P);
    expect(tokens()).toEqual(["t1", "t2"]);
  });

  it("đang chờ phản hồi thì từ chối lần nộp thứ hai (khóa đồng bộ), chỉ một request đi ra", async () => {
    let release;
    const post = vi.fn(() => new Promise((resolve) => (release = resolve)));
    const submitter = createSubmitter({ post, newToken: () => "t1" });
    const first = submitter.submit(P);
    expect(submitter.busy).toBe(true);
    await expect(submitter.submit(P)).rejects.toBeInstanceOf(BusyError);
    release({ submission_id: 1 });
    await first;
    expect(post).toHaveBeenCalledTimes(1);
    expect(submitter.busy).toBe(false);
  });
});
