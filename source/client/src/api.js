export async function api(path, method = "GET", body) {
  const response = await fetch(`/api${path}`, {
    method, credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("unauthorized"));
    const message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    throw new Error(message || `HTTP ${response.status}`);
  }
  return data;
}
