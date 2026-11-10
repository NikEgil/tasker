export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, { method = "GET", body = null, headers = {} } = {}) {
  const options = {
    method,
    headers: { Accept: "application/json", ...headers },
    cache: "no-store",
  };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== null && body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new ApiError("Tasker не отвечает. Проверьте, что сервер запущен.", 0);
  }

  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
  }

  if (!response.ok) {
    const message =
      (payload && payload.error) || `Ошибка запроса (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return payload;
}

export const api = {
  bootstrap: () => request("/api/bootstrap"),
  info: () => request("/api/info"),

  listProjects: () => request("/api/projects"),
  createProject: (name, color = null) =>
    request("/api/projects", {
      method: "POST",
      body: color ? { name, color } : { name },
    }),
  updateProject: (id, patch) =>
    request(`/api/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: patch,
    }),
  deleteProject: (id) =>
    request(`/api/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reorderProjects: (ids) =>
    request("/api/projects/reorder", {
      method: "POST",
      body: { ids },
    }),

  listTasks: (projectId) =>
    request(`/api/projects/${encodeURIComponent(projectId)}/tasks`),
  calendar: (start, end) => {
    const params = new URLSearchParams({ start, end });
    return request(`/api/calendar?${params}`);
  },
  createTask: (projectId, data) =>
    request(`/api/projects/${encodeURIComponent(projectId)}/tasks`, {
      method: "POST",
      body: data,
    }),
  updateTask: (id, patch) =>
    request(`/api/tasks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: patch,
    }),
  deleteTask: (id) =>
    request(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  moveTask: (id, status, index = null) =>
    request(`/api/tasks/${encodeURIComponent(id)}/move`, {
      method: "POST",
      body: { status, index },
    }),

  listMessages: (projectId, { limit = 100, before = null } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (before) params.set("before", before);
    return request(
      `/api/projects/${encodeURIComponent(projectId)}/messages?${params}`
    );
  },
  sendMessage: (projectId, text, files = []) => {
    const form = new FormData();
    form.append("text", text);
    for (const file of files) form.append("files", file, file.name);
    return request(
      `/api/projects/${encodeURIComponent(projectId)}/messages`,
      { method: "POST", body: form }
    );
  },
  updateMessage: (id, text) =>
    request(`/api/messages/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: { text },
    }),
  deleteMessage: (id) =>
    request(`/api/messages/${encodeURIComponent(id)}`, { method: "DELETE" }),

  createBackup: () => request("/api/backup", { method: "POST" }),
  openDataFolder: () =>
    request("/api/open-data-folder", { method: "POST" }),
  browseDb: () => request("/api/db/browse", { method: "POST", body: {} }),
  switchDb: (path) =>
    request("/api/db/switch", { method: "POST", body: { path } }),
  sessionOpen: (sessionId) =>
    request("/api/session/open", {
      method: "POST",
      body: { session_id: sessionId },
    }),
  saveCalendarSettings: (preferences) =>
    request("/api/settings/calendar", {
      method: "POST",
      body: preferences,
    }),

  attachmentUrl: (id, download = false) =>
    `/api/attachments/${encodeURIComponent(id)}${download ? "?download=1" : ""}`,
  backupDownloadUrl: "/api/backup/download",
};
