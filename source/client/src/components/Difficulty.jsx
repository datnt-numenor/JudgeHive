import React from "react";
import { difficulties } from "../utils";

export function Difficulty({ value }) {
  return <span className={`difficulty difficulty-${value}`}>{difficulties[value]?.label ?? value}</span>;
}
