import { useEffect, useState } from "react";

/** Gọi `load(signal)` mỗi khi `key` đổi; hủy request khi rời trang. Trả `{data, error, loading}`. */
export function useFetch(load, key) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState({ data: null, error: null, loading: true });
    load(controller.signal).then(
      (data) => setState({ data, error: null, loading: false }),
      (error) => {
        if (error?.name === "AbortError") return;
        setState({ data: null, error, loading: false });
      },
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` được tạo lại mỗi lần render, chỉ `key` quyết định tải lại
  }, [key]);
  return state;
}
