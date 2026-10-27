// Quản lý `client_token` của một lần nộp (README 3.3, D2).
//  - Mỗi lần nộp mới dùng một token mới.
//  - Lỗi mạng, hết giờ chờ hoặc 5xx (chưa biết server đã nhận bài chưa): thử lại thì dùng lại token, NHƯNG chỉ khi nội
//    dung nộp y hệt. Sửa mã, đổi đề hay đổi ngôn ngữ là một lần nộp khác; dùng lại token cũ thì server trả bài cũ và
//    bỏ qua nội dung mới.
//  - Nhận được `submission_id` hoặc lỗi 4xx (chắc chắn không tạo bài) thì lần sau dùng token mới.
//  - Đang chờ phản hồi thì từ chối nộp tiếp (khóa đồng bộ, không dựa vào state của React vốn cập nhật bất đồng bộ).
import { isUnknownOutcome } from "./api.js";
import { uuidv4 } from "./uuid.js";

export class BusyError extends Error {
  constructor() {
    super("đang nộp bài");
    this.name = "BusyError";
  }
}

export function createSubmitter({ post, newToken = uuidv4 }) {
  let pending = null; // { key, token }
  let busy = false;
  return {
    get busy() {
      return busy;
    },
    async submit({ problem_id, language, code }) {
      if (busy) throw new BusyError();
      busy = true;
      try {
        const key = JSON.stringify([problem_id, language, code]);
        if (!pending || pending.key !== key) pending = { key, token: newToken() };
        const result = await post({ problem_id, language, code, client_token: pending.token });
        pending = null;
        return result;
      } catch (e) {
        if (!isUnknownOutcome(e)) pending = null;
        throw e;
      } finally {
        busy = false;
      }
    },
  };
}
