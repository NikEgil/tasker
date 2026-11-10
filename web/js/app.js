import { api } from "./api.js";
import {
  esc,
  icon,
  plural,
  formatSize,
  formatFullDate,
  openModal,
  confirmDialog,
  toast,
  copyText,
} from "./ui.js";
import { createBoard } from "./board.js";
import { createChat } from "./chat.js";

const PROJECT_COLORS = [
  "#4c6ef5",
  "#7048e8",
  "#0c8599",
  "#2f9e44",
  "#e8590c",
  "#c2255c",
  "#5c7cfa",
  "#495057",
];

const state = {
  projects: [],
  activeId: null,
  activeProject: null,
  tasks: [],
  messages: [],
  hasMore: false,
  info: null,
  loadToken: 0,
};

const elements = {
  appShell: document.getElementById("app-shell"),
  sidebar: document.querySelector(".sidebar"),
  projectList: document.getElementById("project-list"),
  projectEmpty: document.getElementById("project-empty"),
  newProjectBtn: document.getElementById("new-project-btn"),
  newProjectForm: document.getElementById("new-project-form"),
  newProjectName: document.getElementById("new-project-name"),
  dbPill: document.getElementById("db-pill"),
  dbSize: document.getElementById("db-size"),
  themeBtn: document.getElementById("theme-btn"),
  welcome: document.getElementById("welcome"),
  welcomeCreate: document.getElementById("welcome-create"),
  projectView: document.getElementById("project-view"),
  projectTitle: document.getElementById("project-title"),
  projectDot: document.getElementById("project-dot"),
  projectMeta: document.getElementById("project-meta"),
  projectMenuBtn: document.getElementById("project-menu-btn"),
  addTaskBtn: document.getElementById("add-task-btn"),
  chatToggleBtn: document.getElementById("chat-toggle-btn"),
  chatCloseBtn: document.getElementById("chat-close-btn"),
  chatPanel: document.getElementById("chat-panel"),
  chatBackdrop: document.getElementById("chat-backdrop"),
  board: document.getElementById("board"),
};

let board = null;
let chat = null;
let mobileChatOpen = false;
let wasMobile = null;

/* ------------------------------------------------------------------ */
/* проекты и состояние                                                */
/* ------------------------------------------------------------------ */
function getProject(id) {
  return state.projects.find((project) => project.id === id) || null;
}

function setProject(project) {
  if (!project) return;
  const index = state.projects.findIndex((item) => item.id === project.id);
  if (index !== -1) state.projects[index] = project;
  if (state.activeId === project.id) state.activeProject = project;
}

function activeCount(project) {
  return (project.counts?.urgent || 0) + (project.counts?.later || 0);
}

function refreshChrome() {
  renderSidebar();
  renderProjectHeader();
  if (chat) chat.setCount(state.activeProject?.messages ?? 0);
}

function recomputeActiveCounts() {
  if (!state.activeProject) return;
  const counts = { urgent: 0, later: 0, done: 0 };
  for (const task of state.tasks) {
    if (counts[task.status] !== undefined) counts[task.status] += 1;
  }
  state.activeProject.counts = counts;
}

/* ------------------------------------------------------------------ */
/* сайдбар                                                            */
/* ------------------------------------------------------------------ */
function projectItemHTML(project) {
  const isActive = project.id === state.activeId;
  const pending = activeCount(project);
  return `
    <div class="project-item${isActive ? " is-active" : ""}" data-project-id="${esc(project.id)}"
         role="button" tabindex="0" aria-current="${isActive ? "true" : "false"}"
         aria-label="${esc(project.name)}${pending ? `, активных задач: ${pending}` : ""}">
      <span class="project-color" style="--project-color:${esc(project.color)}"></span>
      <span class="project-name" title="${esc(project.name)}">${esc(project.name)}</span>
      ${pending ? `<span class="project-badge" title="${pending} активных">${pending}</span>` : ""}
      <button type="button" class="icon-btn project-more" data-project-menu="${esc(project.id)}"
              title="Настройки проекта" aria-label="Настройки проекта">${icon("dots")}</button>
    </div>`;
}

function renderSidebar() {
  elements.projectList.innerHTML = state.projects.map(projectItemHTML).join("");
  elements.projectEmpty.hidden = state.projects.length > 0;
}

function renderDbSize() {
  if (!elements.dbSize) return;
  const size = state.info?.db?.size;
  elements.dbSize.textContent = size == null ? "—" : formatSize(size);
  if (state.info?.db?.path) elements.dbPill.title = state.info.db.path;
}

/* ------------------------------------------------------------------ */
/* шапка проекта                                                      */
/* ------------------------------------------------------------------ */
function renderProjectHeader() {
  const project = state.activeProject;
  if (!project) return;
  elements.projectTitle.textContent = project.name;
  elements.projectDot.style.backgroundColor = project.color;
  const counts = project.counts || { urgent: 0, later: 0, done: 0 };
  const total = counts.urgent + counts.later + counts.done;
  if (!total) {
    elements.projectMeta.textContent = "Пока нет задач — добавьте первую";
  } else {
    const active = counts.urgent + counts.later;
    elements.projectMeta.textContent = [
      `${total} ${plural(total, "задача", "задачи", "задач")}`,
      `${active} ${plural(active, "активная", "активные", "активных")}`,
      `${counts.done} готово`,
    ].join(" · ");
  }
  document.title = `${project.name} — Tasker`;
}

/* ------------------------------------------------------------------ */
/* видимость                                                          */
/* ------------------------------------------------------------------ */
function showWelcome() {
  state.activeId = null;
  state.activeProject = null;
  elements.projectView.hidden = true;
  elements.welcome.hidden = false;
  elements.chatBackdrop.hidden = true;
  document.title = "Tasker — проекты, задачи и заметки";
  if (location.hash) {
    window.history.replaceState({}, "", location.pathname + location.search);
  }
}

function showProject() {
  elements.welcome.hidden = true;
  elements.projectView.hidden = false;
  applyChatVisibility();
}

function isMobileView() {
  return window.matchMedia("(max-width: 1280px)").matches;
}

function applyChatVisibility() {
  if (!elements.projectView || elements.projectView.hidden) {
    elements.chatBackdrop.hidden = true;
    if (elements.chatPanel) elements.chatPanel.inert = true;
    return;
  }
  const mobile = isMobileView();
  if (mobile) {
    elements.projectView.classList.remove("chat-closed");
    elements.projectView.classList.toggle("chat-open", mobileChatOpen);
    elements.chatBackdrop.hidden = !mobileChatOpen;
  } else {
    elements.projectView.classList.remove("chat-open");
    const closed = localStorage.getItem("tasker-chat") === "closed";
    elements.projectView.classList.toggle("chat-closed", closed);
    elements.chatBackdrop.hidden = true;
    mobileChatOpen = false;
  }
  const visible = mobile
    ? mobileChatOpen
    : !elements.projectView.classList.contains("chat-closed");
  if (elements.chatPanel) {
    elements.chatPanel.inert = !visible;
    elements.chatPanel.setAttribute("aria-hidden", String(!visible));
  }
  elements.chatToggleBtn.classList.toggle("is-active", visible);
  elements.chatToggleBtn.setAttribute("aria-expanded", String(visible));
}

function toggleChat() {
  const mobile = isMobileView();
  if (mobile) {
    mobileChatOpen = !mobileChatOpen;
  } else {
    const willClose = !elements.projectView.classList.contains("chat-closed");
    elements.projectView.classList.toggle("chat-closed", willClose);
    localStorage.setItem("tasker-chat", willClose ? "closed" : "open");
  }
  applyChatVisibility();
  const stillOpen = mobile
    ? mobileChatOpen
    : !elements.projectView.classList.contains("chat-closed");
  if (!stillOpen) {
    elements.chatToggleBtn.focus();
  } else if (mobile) {
    requestAnimationFrame(() => document.getElementById("chat-input")?.focus());
  }
}

/* ------------------------------------------------------------------ */
/* выбор проекта                                                      */
/* ------------------------------------------------------------------ */
function projectHash(id) {
  return `#project/${encodeURIComponent(id)}`;
}

function parseProjectHash() {
  const match = /^#project\/([A-Za-z0-9_-]+)$/.exec(location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

async function selectProject(id, { history = "push" } = {}) {
  const project = getProject(id);
  if (!project) return;
  if (state.activeId === id && !elements.projectView.hidden) return;

  state.activeId = id;
  state.activeProject = project;
  state.tasks = [];
  state.messages = [];
  state.hasMore = false;
  const token = ++state.loadToken;

  showProject();
  renderSidebar();
  renderProjectHeader();
  board.render([], project);
  chat.reset();
  chat.setCount(project.messages ?? 0);
  mobileChatOpen = false;
  applyChatVisibility();

  if (history === "push") {
    window.history.pushState({ projectId: id }, "", projectHash(id));
  } else if (history === "replace") {
    window.history.replaceState({ projectId: id }, "", projectHash(id));
  }

  try {
    const [tasksResponse, messagesResponse] = await Promise.all([
      api.listTasks(id),
      api.listMessages(id),
    ]);
    if (token !== state.loadToken || state.activeId !== id) return;
    state.tasks = tasksResponse.tasks;
    state.messages = messagesResponse.messages;
    state.hasMore = Boolean(messagesResponse.has_more);
    setProject(tasksResponse.project);
    if (messagesResponse.project) setProject(messagesResponse.project);
    board.render(state.tasks, state.activeProject);
    chat.render(state.messages, {
      stickToBottom: true,
      hasMore: state.hasMore,
    });
    chat.setCount(state.activeProject.messages ?? 0);
    refreshChrome();
  } catch (error) {
    if (token !== state.loadToken) return;
    toast(error?.message || "Не удалось загрузить проект", "error");
    board.render([], project);
    chat.render([], { hasMore: false });
  }
}

/* ------------------------------------------------------------------ */
/* задачи                                                            */
/* ------------------------------------------------------------------ */
function replaceTask(task) {
  state.tasks = state.tasks.map((item) => (item.id === task.id ? task : item));
}

function byPosition(a, b) {
  return (
    (a.position ?? 0) - (b.position ?? 0) ||
    String(a.created_at).localeCompare(String(b.created_at))
  );
}

function applyLocalMove(id, status, index) {
  const task = state.tasks.find((item) => item.id === id);
  if (!task) return;
  const siblings = state.tasks
    .filter((item) => item.id !== id && item.status === status)
    .sort(byPosition);
  let position = index == null ? siblings.length : Number(index);
  if (!Number.isFinite(position)) position = siblings.length;
  position = Math.max(0, Math.min(position, siblings.length));

  task.status = status;
  task.updated_at = new Date().toISOString();
  if (status === "done") {
    if (!task.done_at) task.done_at = task.updated_at;
  } else {
    task.done_at = null;
  }
  siblings.splice(position, 0, task);
  siblings.forEach((item, positionIndex) => {
    item.position = positionIndex;
  });
}

async function moveTask(id, status, index) {
  const task = state.tasks.find((item) => item.id === id);
  if (!task) return;
  const projectId = state.activeId;
  applyLocalMove(id, status, index);
  recomputeActiveCounts();
  board.render(state.tasks, state.activeProject);
  refreshChrome();

  try {
    const response = await api.moveTask(id, status, index);
    if (state.activeId !== projectId) return;
    replaceTask(response.task);
    setProject(response.project);
    board.render(state.tasks, state.activeProject);
    refreshChrome();
  } catch (error) {
    toast(error?.message || "Не удалось переместить задачу", "error");
    await reloadTasks(projectId);
  }
}

async function reloadTasks(projectId = state.activeId) {
  if (!projectId) return;
  try {
    const response = await api.listTasks(projectId);
    if (state.activeId !== projectId) return;
    state.tasks = response.tasks;
    setProject(response.project);
    board.render(state.tasks, state.activeProject);
    refreshChrome();
  } catch (error) {
    toast(error?.message || "Не удалось обновить задачи", "error");
  }
}

async function saveTask({ id, title, description, status }) {
  const projectId = state.activeId;
  if (!projectId) throw new Error("Проект не выбран");
  const response = id
    ? await api.updateTask(id, { title, description, status })
    : await api.createTask(projectId, { title, description, status });
  if (state.activeId !== projectId) return;
  if (id) replaceTask(response.task);
  else state.tasks.push(response.task);
  setProject(response.project);
  board.render(state.tasks, state.activeProject);
  refreshChrome();
}

async function deleteTask(id) {
  const projectId = state.activeId;
  const response = await api.deleteTask(id);
  if (state.activeId !== projectId) return;
  state.tasks = state.tasks.filter((task) => task.id !== id);
  setProject(response.project);
  board.render(state.tasks, state.activeProject);
  refreshChrome();
}

/* ------------------------------------------------------------------ */
/* заметки                                                           */
/* ------------------------------------------------------------------ */
async function sendMessage(text, files) {
  const projectId = state.activeId;
  if (!projectId) throw new Error("Проект не выбран");
  const response = await api.sendMessage(projectId, text, files);
  if (state.activeId !== projectId) return;
  state.messages.push(response.message);
  setProject(response.project);
  chat.render(state.messages, { stickToBottom: true, hasMore: state.hasMore });
  refreshChrome();
  refreshInfo();
}

async function loadMoreMessages() {
  const projectId = state.activeId;
  if (!projectId || !state.hasMore || !state.messages.length) return;
  const oldest = state.messages[0];
  const response = await api.listMessages(projectId, { before: oldest.created_at });
  if (state.activeId !== projectId) return;
  const known = new Set(state.messages.map((message) => message.id));
  const older = response.messages.filter((message) => !known.has(message.id));
  state.hasMore = Boolean(response.has_more);
  state.messages = [...older, ...state.messages];
  setProject(response.project);
  chat.render(state.messages, {
    preserveScroll: true,
    hasMore: state.hasMore,
  });
  refreshChrome();
}

async function updateMessage(id, text) {
  const projectId = state.activeId;
  const response = await api.updateMessage(id, text);
  if (state.activeId !== projectId) return;
  state.messages = state.messages.map((message) =>
    message.id === id ? response.message : message
  );
  setProject(response.project);
  chat.render(state.messages, { preserveScroll: true, hasMore: state.hasMore });
  refreshChrome();
}

async function deleteMessage(id) {
  const projectId = state.activeId;
  const response = await api.deleteMessage(id);
  if (state.activeId !== projectId) return;
  state.messages = state.messages.filter((message) => message.id !== id);
  setProject(response.project);
  chat.render(state.messages, { preserveScroll: true, hasMore: state.hasMore });
  refreshChrome();
  refreshInfo();
}

/* ------------------------------------------------------------------ */
/* информация о базе                                                 */
/* ------------------------------------------------------------------ */
async function refreshInfo() {
  try {
    state.info = await api.info();
    renderDbSize();
  } catch (error) {
    /* размер не критичен */
  }
}

function downloadBackup() {
  const link = document.createElement("a");
  link.href = api.backupDownloadUrl;
  link.download = "tasker.db";
  document.body.appendChild(link);
  link.click();
  link.remove();
  toast("Файл базы выгружается — сохраните его в надёжном месте", "info");
}

function openDbModal() {
  api
    .info()
    .then((info) => {
      state.info = info;
      renderDbSize();
      const stats = info.stats || {};
      const body = `
        <div class="db-section">
          <div class="db-section-label">Файл базы</div>
          <div class="db-path-row">
            <code class="db-path">${esc(info.db.path)}</code>
            <button type="button" class="icon-btn" data-copy-path title="Скопировать путь" aria-label="Скопировать путь">${icon("copy")}</button>
          </div>
          <p class="db-meta">${formatSize(info.db.size)} · изменён ${esc(formatFullDate(info.db.modified_at))}</p>
        </div>
        <div class="db-stats">
          <div class="db-stat"><span class="db-stat-value">${stats.projects ?? 0}</span><span class="db-stat-label">${plural(stats.projects ?? 0, "проект", "проекта", "проектов")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.tasks ?? 0}</span><span class="db-stat-label">${plural(stats.tasks ?? 0, "задача", "задачи", "задач")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.messages ?? 0}</span><span class="db-stat-label">${plural(stats.messages ?? 0, "заметка", "заметки", "заметок")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.attachments ?? 0}</span><span class="db-stat-label">${plural(stats.attachments ?? 0, "файл", "файла", "файлов")}</span></div>
        </div>
        <div class="db-note">
          ${icon("database")}
          <p>В этом файле лежит всё: проекты, задачи, заметки и вложения.
          Для синхронизации копируйте файл при закрытом Tasker или сохраняйте резервную копию.</p>
        </div>`;

      const footer = `
        <button type="button" class="btn btn-quiet" data-act="folder">${icon("folder")}<span>Открыть папку</span></button>
        <button type="button" class="btn btn-quiet" data-act="backup">${icon("database")}<span>Сохранить копию</span></button>
        <span class="modal-footer-spacer"></span>
        <button type="button" class="btn btn-primary" data-act="download">${icon("download")}<span>Скачать БД</span></button>`;

      openModal({
        title: "Данные Tasker",
        subtitle: "Один файл SQLite — вся информация приложения",
        className: "modal-db",
        body,
        footer,
        onMount({ overlay, close }) {
          overlay.querySelector("[data-copy-path]").addEventListener("click", () => {
            copyText(info.db.path);
          });
          overlay.querySelector('[data-act="folder"]').addEventListener("click", async () => {
            try {
              await api.openDataFolder();
              toast("Папка с данными открыта", "success");
            } catch (error) {
              toast(error?.message || "Не удалось открыть папку", "error");
            }
          });
          overlay.querySelector('[data-act="backup"]').addEventListener("click", async () => {
            try {
              const result = await api.createBackup();
              toast(`Резервная копия сохранена: ${result.filename}`, "success");
              refreshInfo();
            } catch (error) {
              toast(error?.message || "Не удалось создать копию", "error");
            }
          });
          overlay.querySelector('[data-act="download"]').addEventListener("click", () => {
            downloadBackup();
            close();
          });
        },
      });
    })
    .catch((error) => {
      toast(error?.message || "Не удалось получить сведения о базе", "error");
    });
}

/* ------------------------------------------------------------------ */
/* настройки проекта                                                 */
/* ------------------------------------------------------------------ */
function openProjectSettings(projectId) {
  const project = getProject(projectId);
  if (!project) return;
  const counts = project.counts || { urgent: 0, later: 0, done: 0 };
  const total = counts.urgent + counts.later + counts.done;
  const formId = "project-settings-form";
  const body = `
    <form id="${formId}" class="project-form" novalidate>
      <label class="field">
        <span class="field-label">Название проекта</span>
        <input class="input" name="name" type="text" maxlength="120" autocomplete="off"
               value="${esc(project.name)}" autofocus>
      </label>
      <div class="field">
        <span class="field-label">Цвет</span>
        <div class="color-picker">
          ${PROJECT_COLORS.map(
            (color) => `
            <label class="color-swatch" style="--swatch:${color}" title="${color}">
              <input type="radio" name="color" value="${color}" aria-label="Цвет ${color}" ${project.color === color ? "checked" : ""}>
              <span class="color-swatch-dot"></span>
            </label>`
          ).join("")}
        </div>
      </div>
      <div class="project-facts">
        <span>${total} ${plural(total, "задача", "задачи", "задач")}</span>
        <span>${project.messages ?? 0} ${plural(project.messages ?? 0, "заметка", "заметки", "заметок")}</span>
        <span>создан ${esc(formatFullDate(project.created_at))}</span>
      </div>
    </form>`;

  const footer = `
    <button type="button" class="btn btn-danger-quiet" data-act="delete">${icon("trash")}<span>Удалить проект</span></button>
    <span class="modal-footer-spacer"></span>
    <button type="button" class="btn btn-quiet" data-act="cancel">Отмена</button>
    <button type="submit" form="${formId}" class="btn btn-primary" data-act="save">Сохранить</button>`;

  openModal({
    title: "Настройки проекта",
    className: "modal-project",
    body,
    footer,
    onMount({ overlay, close }) {
      const form = overlay.querySelector(`#${formId}`);
      const nameInput = form.querySelector('[name="name"]');
      const saveButton = overlay.querySelector('[data-act="save"]');
      let busy = false;

      nameInput.addEventListener("input", () => nameInput.classList.remove("is-invalid"));

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (busy) return;
        const name = nameInput.value.trim();
        if (!name) {
          nameInput.classList.add("is-invalid");
          nameInput.focus();
          toast("Введите название проекта", "error");
          return;
        }
        const selected = form.querySelector('input[name="color"]:checked');
        busy = true;
        saveButton.disabled = true;
        saveButton.textContent = "Сохранение…";
        try {
          const response = await api.updateProject(project.id, {
            name,
            color: selected ? selected.value : project.color,
          });
          setProject(response.project);
          refreshChrome();
          close();
        } catch (error) {
          toast(error?.message || "Не удалось сохранить проект", "error");
          busy = false;
          saveButton.disabled = false;
          saveButton.textContent = "Сохранить";
        }
      });

      overlay.querySelector('[data-act="cancel"]').addEventListener("click", close);

      overlay.querySelector('[data-act="delete"]').addEventListener("click", async () => {
        if (busy) return;
        const confirmed = await confirmDialog({
          title: "Удалить проект?",
          message: `Проект «${project.name}», его ${total} ${plural(total, "задача", "задачи", "задач")}, заметки и файлы будут удалены без возможности восстановления.`,
          confirmLabel: "Удалить проект",
        });
        if (!confirmed) return;
        busy = true;
        try {
          await api.deleteProject(project.id);
          const wasActive = state.activeId === project.id;
          state.projects = state.projects.filter((item) => item.id !== project.id);
          close();
          renderSidebar();
          refreshInfo();
          toast("Проект удалён", "success");
          if (wasActive) {
            if (state.projects.length) {
              await selectProject(state.projects[0].id, { history: "replace" });
            } else {
              showWelcome();
            }
          }
        } catch (error) {
          toast(error?.message || "Не удалось удалить проект", "error");
          busy = false;
        }
      });
    },
  });
}

/* ------------------------------------------------------------------ */
/* новый проект                                                      */
/* ------------------------------------------------------------------ */
function openNewProjectForm() {
  elements.newProjectForm.hidden = false;
  elements.newProjectBtn.setAttribute("aria-expanded", "true");
  elements.newProjectName.value = "";
  elements.newProjectName.focus();
}

function closeNewProjectForm() {
  elements.newProjectForm.hidden = true;
  elements.newProjectBtn.setAttribute("aria-expanded", "false");
  elements.newProjectName.value = "";
}

async function createProject(event) {
  event.preventDefault();
  const submitButton = elements.newProjectForm.querySelector('button[type="submit"]');
  if (submitButton.disabled) return;
  const name = elements.newProjectName.value.trim();
  if (!name) {
    elements.newProjectName.focus();
    toast("Введите название проекта", "error");
    return;
  }
  submitButton.disabled = true;
  try {
    const response = await api.createProject(name);
    state.projects.push(response.project);
    closeNewProjectForm();
    renderSidebar();
    refreshInfo();
    await selectProject(response.project.id);
    toast("Проект создан", "success");
  } catch (error) {
    toast(error?.message || "Не удалось создать проект", "error");
  } finally {
    submitButton.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* тема                                                              */
/* ------------------------------------------------------------------ */
function toggleTheme() {
  const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  document.documentElement.style.colorScheme = next;
  localStorage.setItem("tasker-theme", next);
}

/* ------------------------------------------------------------------ */
/* критическая ошибка                                                */
/* ------------------------------------------------------------------ */
function showFatal(message) {
  const overlay = document.createElement("div");
  overlay.className = "fatal-overlay";
  overlay.innerHTML = `
    <div class="fatal-card">
      <div class="fatal-mark">${icon("alert")}</div>
      <h1>Tasker не запущен</h1>
      <p>${esc(message)}</p>
      <p class="fatal-hint">Запустите <code>Tasker.bat</code> или <code>python server.py</code> и обновите страницу.</p>
      <button type="button" class="btn btn-primary" id="fatal-retry">${icon("refresh")}<span>Повторить</span></button>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector("#fatal-retry").addEventListener("click", () => location.reload());
}

/* ------------------------------------------------------------------ */
/* события                                                           */
/* ------------------------------------------------------------------ */
function bindEvents() {
  elements.newProjectBtn.addEventListener("click", () => {
    if (elements.newProjectForm.hidden) openNewProjectForm();
    else closeNewProjectForm();
  });
  elements.welcomeCreate.addEventListener("click", openNewProjectForm);
  elements.newProjectForm.addEventListener("submit", createProject);
  elements.newProjectForm
    .querySelector('[data-action="cancel"]')
    .addEventListener("click", closeNewProjectForm);
  elements.newProjectName.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeNewProjectForm();
    }
  });

  elements.projectList.addEventListener("click", (event) => {
    const menuButton = event.target.closest("[data-project-menu]");
    const item = event.target.closest("[data-project-id]");
    if (!item) return;
    if (menuButton) {
      event.stopPropagation();
      openProjectSettings(item.dataset.projectId);
      return;
    }
    selectProject(item.dataset.projectId);
  });

  elements.projectList.addEventListener("keydown", (event) => {
    const item = event.target.closest("[data-project-id]");
    if (!item || event.target !== item) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectProject(item.dataset.projectId);
    }
  });

  elements.projectList.addEventListener("contextmenu", (event) => {
    const item = event.target.closest("[data-project-id]");
    if (!item) return;
    event.preventDefault();
    openProjectSettings(item.dataset.projectId);
  });

  elements.projectMenuBtn.addEventListener("click", () => {
    if (state.activeId) openProjectSettings(state.activeId);
  });
  elements.projectTitle.addEventListener("click", () => {
    if (state.activeId) openProjectSettings(state.activeId);
  });
  elements.addTaskBtn.addEventListener("click", () => board.openNewTask("urgent"));

  elements.chatToggleBtn.addEventListener("click", toggleChat);
  elements.chatCloseBtn.addEventListener("click", toggleChat);
  elements.chatBackdrop.addEventListener("click", () => {
    mobileChatOpen = false;
    applyChatVisibility();
    elements.chatToggleBtn.focus();
  });

  elements.themeBtn.addEventListener("click", toggleTheme);
  elements.dbPill.addEventListener("click", openDbModal);

  document.getElementById("brand").addEventListener("click", (event) => {
    event.preventDefault();
  });

  window.addEventListener("popstate", () => {
    const id = parseProjectHash();
    if (id) {
      selectProject(id, { history: "none" });
    } else if (state.projects.length) {
      selectProject(state.projects[0].id, { history: "replace" });
    } else {
      showWelcome();
    }
  });

  window.addEventListener("resize", () => {
    const mobile = isMobileView();
    if (mobile !== wasMobile) {
      wasMobile = mobile;
      mobileChatOpen = false;
      applyChatVisibility();
    }
  });

  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")) {
      event.preventDefault();
    }
  });
  window.addEventListener("drop", (event) => {
    if (event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")) {
      event.preventDefault();
    }
  });
}

/* ------------------------------------------------------------------ */
/* старт                                                             */
/* ------------------------------------------------------------------ */
async function init() {
  bindEvents();
  wasMobile = isMobileView();

  board = createBoard({
    container: elements.board,
    callbacks: {
      saveTask,
      deleteTask,
      createTask: saveTask,
      moveTask,
      quickToggle: (task) => {
        if (task.status === "done") moveTask(task.id, "later", null);
        else moveTask(task.id, "done", null);
      },
    },
  });

  chat = createChat({
    root: elements.projectView,
    callbacks: {
      sendMessage,
      loadMoreMessages,
      updateMessage,
      deleteMessage,
    },
  });

  applyChatVisibility();

  try {
    const bootstrap = await api.bootstrap();
    state.projects = bootstrap.projects || [];
    state.info = bootstrap.info || null;
    renderSidebar();
    renderDbSize();

    const wanted = parseProjectHash();
    const initial =
      state.projects.find((project) => project.id === wanted) || state.projects[0];
    if (initial) {
      await selectProject(initial.id, {
        history: wanted === initial.id ? "none" : "replace",
      });
    } else {
      showWelcome();
    }
  } catch (error) {
    showFatal(error?.message || "Не удалось получить данные");
  }
}

init();
