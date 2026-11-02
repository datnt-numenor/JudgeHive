import { Outlet } from "react-router-dom";
import { useAuth } from "../auth.jsx";

/**
 * Chặn route admin theo `user.role` của `/api/me`. Đây chỉ là để người thường không thấy trang trống: quyền thật do
 * server quyết (mọi route `/api/admin/` trả 403 cho người không phải admin). Người thường thấy đúng nội dung của một
 * đường dẫn không tồn tại, nên không biết trang admin có hay không.
 */
export default function AdminRoute() {
  const { user } = useAuth();
  if (user?.role !== "admin") return <p>Không tìm thấy trang.</p>;
  return <Outlet />;
}
