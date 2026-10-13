// Talk to the local server. Errors carry the server's message and any validation list.

export class ApiError extends Error {
  constructor(message, errors) {
    super(message);
    this.errors = errors || null;
  }
}

export async function api(path, body) {
  const options =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    if (detail && Array.isArray(detail.errors)) throw new ApiError(detail.errors.join("\n"), detail.errors);
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail || response.statusText));
  }
  return data;
}

export function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
