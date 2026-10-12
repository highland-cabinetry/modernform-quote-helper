const BASE = import.meta.env.VITE_API_BASE || "";

async function responseJson(response) {
  if (response.ok) return response.json();
  let message = `Request failed (${response.status})`;
  let detail = null;
  try {
    const payload = await response.json();
    detail = payload.detail ?? null;
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (Array.isArray(payload.detail)) {
      const issues = payload.detail.slice(0, 6).map((issue) => {
        const location = issue.loc || [];
        const lineIndex = location.indexOf("lines");
        const lineLabel = lineIndex >= 0 && Number.isInteger(location[lineIndex + 1])
          ? `Line ${location[lineIndex + 1] + 1}`
          : "Request";
        const field = String(location.at(-1) || "value").replaceAll("_", " ");
        return `${lineLabel} ${field}: ${issue.msg}`;
      });
      const remaining = payload.detail.length - issues.length;
      message = `${issues.join(" · ")}${remaining > 0 ? ` · ${remaining} more issue${remaining === 1 ? "" : "s"}` : ""}`;
    } else if (payload.detail?.message) {
      message = payload.detail.message;
    }
  } catch {
    // Preserve the status-based message.
  }
  const error = new Error(message);
  error.status = response.status;
  error.detail = detail;
  throw error;
}

export async function getJson(path) {
  return responseJson(await fetch(`${BASE}${path}`));
}

export async function sendJson(path, method, payload, { signal } = {}) {
  const options = { method, headers: { "Content-Type": "application/json" }, signal };
  if (payload !== undefined) options.body = JSON.stringify(payload);
  return responseJson(
    await fetch(`${BASE}${path}`, options),
  );
}

export async function uploadFiles(path, files, field = "files", fields = {}) {
  const form = new FormData();
  for (const file of files) form.append(field, file);
  for (const [name, value] of Object.entries(fields)) form.append(name, value);
  return responseJson(await fetch(`${BASE}${path}`, { method: "POST", body: form }));
}

export function exportUrl(shipmentId, format) {
  return `${BASE}/api/shipments/${shipmentId}/export/${format}`;
}
