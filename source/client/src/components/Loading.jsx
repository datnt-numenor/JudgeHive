import React from "react";
import { ErrorText } from "./ErrorText";

export function Loading({ result, children }) {
  if (result.error) return <ErrorText message={result.error} />;
  return result.data === null ? <p>Đang tải…</p> : children(result.data);
}
