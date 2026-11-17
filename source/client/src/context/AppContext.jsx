import React, { createContext, useContext, useEffect, useState } from "react";
import { api } from "../api";

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

// Một hook tải REST, chạy lại khi có sự kiện dữ liệu hoặc người dùng bấm tải lại.
export function useData(path) {
  const { version } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [path, version]);
  return { data, error };
}
