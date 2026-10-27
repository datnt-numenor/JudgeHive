import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, setUnauthenticatedHandler } from "./lib/api.js";

const AuthContext = createContext(null);

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth phải nằm trong AuthProvider");
  return value;
}

/**
 * Giữ người dùng hiện tại. Danh tính chỉ lấy từ `GET /api/me` / `POST /api/login` (server theo phiên), không bao giờ
 * từ thứ client tự lưu. `status`: "loading" khi đang hỏi `/api/me`, "anon" hoặc "user".
 */
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null });
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    api
      .me({ signal: controller.signal })
      .then((user) => setState({ status: "user", user }))
      .catch((e) => {
        if (e?.name === "AbortError") return;
        // 401 là chưa đăng nhập. Lỗi khác (mạng, 5xx) cũng đưa về trang đăng nhập; đăng nhập lại sẽ cho biết lỗi.
        setState({ status: "anon", user: null });
      });
    return () => controller.abort();
  }, []);

  // Mọi 401 UNAUTHENTICATED ở bất kỳ request nào: hết phiên, về trang đăng nhập.
  useEffect(() => {
    setUnauthenticatedHandler(() => {
      setState({ status: "anon", user: null });
      navigate("/login", { replace: true });
    });
    return () => setUnauthenticatedHandler(null);
  }, [navigate]);

  const login = useCallback(async (username, password) => {
    const user = await api.login(username, password);
    setState({ status: "user", user });
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch (e) {
      // 401 nghĩa là phiên đã mất, coi như đã đăng xuất (README 3.2). Lỗi khác (mạng, 5xx): phiên có thể còn sống ở
      // server, nên báo lỗi cho người dùng thay vì giả vờ đã đăng xuất.
      if (!(e instanceof ApiError && e.status === 401)) throw e;
    }
    setState({ status: "anon", user: null });
  }, []);

  const value = useMemo(() => ({ ...state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
