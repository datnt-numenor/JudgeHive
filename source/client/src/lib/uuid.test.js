import { describe, expect, it } from "vitest";
import { uuidv4 } from "./uuid.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("uuidv4", () => {
  it("dùng crypto.randomUUID khi có", () => {
    expect(uuidv4({ randomUUID: () => "fixed" })).toBe("fixed");
  });

  it("không có randomUUID (context không an toàn) thì tự dựng UUID v4 từ getRandomValues", () => {
    const fake = { getRandomValues: (b) => b.fill(0xff) };
    expect(uuidv4(fake)).toMatch(UUID_RE);
    const zero = { getRandomValues: (b) => b.fill(0) };
    expect(uuidv4(zero)).toBe("00000000-0000-4000-8000-000000000000");
  });

  it("hai lần gọi dự phòng cho hai giá trị khác nhau với nguồn ngẫu nhiên thật", () => {
    const real = { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) };
    expect(uuidv4(real)).not.toBe(uuidv4(real));
  });

  it("không có nguồn ngẫu nhiên thì báo lỗi, không sinh token đoán được", () => {
    expect(() => uuidv4({})).toThrow();
  });
});
