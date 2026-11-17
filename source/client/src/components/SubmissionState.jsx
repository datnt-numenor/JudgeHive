import React from "react";
import { stateName } from "../utils";

// Trạng thái bài nộp; "Đang chờ/Đang chấm" có hiệu ứng nhấp nháy nhẹ.
export function SubmissionState({ value }) {
  return <span className={`state state-${value}`}>{stateName[value]}</span>;
}
