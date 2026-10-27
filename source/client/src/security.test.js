import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// PLAN mục 5: frontend không dùng dangerouslySetInnerHTML và không ghi HTML thô vào DOM.
const SRC = join(process.cwd(), "src"); // vitest chạy với thư mục client/ làm gốc

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(jsx?|html)$/.test(name) && !/\.test\.jsx?$/.test(name) ? [path] : [];
  });
}

describe("quy tắc an toàn phía web", () => {
  const files = sources(SRC);

  it("quét được mã nguồn của client", () => {
    expect(files.length).toBeGreaterThan(8);
  });

  it.each(["dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("])(
    "không có %s trong src/ (không tính test)",
    (needle) => {
      const hits = files.filter((f) => readFileSync(f, "utf8").includes(needle));
      expect(hits).toEqual([]);
    },
  );
});
