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
import { openTaskEditor } from "./task-editor.js";
import {
  createCalendar,
  calendarRange,
  monthParam,
  parseMonthParam,
  addDaysKey,
  dateFromKey,
  dateKey,
  taskDurationDays,
  todayKey,
  weekStartKey,
  weekRange,
} from "./calendar.js";
import { startBrowserLifecycle } from "./lifecycle.js";

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
  view: "project",
  projects: [],
  activeId: null,
  activeProject: null,
  tasks: [],
  messages: [],
  hasMore: false,
  info: null,
  preferences: {
    show_project_name: true,
    show_completed: true,
    sort_projects_by_tasks: false,
    show_weekends: true,
  },
  loadToken: 0,
  calendarToken: 0,
  calendar: null,
};

const elements = {
  appShell: document.getElementById("app-shell"),
  sidebar: document.querySelector(".sidebar"),
  projectList: document.getElementById("project-list"),
  projectEmpty: document.getElementById("project-empty"),
  calendarNav: document.getElementById("calendar-nav"),
  calendarView: document.getElementById("calendar-view"),
  newProjectBtn: document.getElementById("new-project-btn"),
  newProjectForm: document.getElementById("new-project-form"),
  newProjectName: document.getElementById("new-project-name"),
  dbPill: document.getElementById("settings-pill"),
  dbSize: document.getElementById("settings-subtitle"),
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
  chatResizer: document.getElementById("chat-resizer"),
  chatBackdrop: document.getElementById("chat-backdrop"),
  board: document.getElementById("board"),
  syncVersionCard: document.getElementById("sync-version-card"),
  syncVersionNumber: document.getElementById("sync-version-number"),
  syncVersionMeta: document.getElementById("sync-version-meta"),
  syncVersionDot: document.getElementById("sync-version-dot"),
  quickSyncPull: document.getElementById("quick-sync-pull"),
  quickSyncPush: document.getElementById("quick-sync-push"),
};

let board = null;
let chat = null;
let calendar = null;
let mobileChatOpen = false;
let wasMobile = null;
let projectDragId = null;
let projectOrderBusy = false;
let quickSyncBusy = false;
let quickSyncRemoteRequest = 0;

const CHAT_WIDTH_KEY = "tasker-chat-width";
const DEFAULT_CHAT_WIDTH = 388;
const MIN_CHAT_WIDTH = 300;
let chatWidth = DEFAULT_CHAT_WIDTH;
let resizingChat = false;

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
  const isActive = state.view === "project" && project.id === state.activeId;
  const pending = activeCount(project);
  const sortable = !state.preferences.sort_projects_by_tasks;
  return `
    <div class="project-item${isActive ? " is-active" : ""}" data-project-id="${esc(project.id)}"
         draggable="${sortable}" role="button" tabindex="0" aria-current="${isActive ? "true" : "false"}"
         aria-label="${esc(project.name)}${pending ? `, активных задач: ${pending}` : ""}">
      <span class="project-color" style="--project-color:${esc(project.color)}"></span>
      <span class="project-name" title="${esc(project.name)}">${esc(project.name)}</span>
      ${pending ? `<span class="project-badge" title="${pending} активных">${pending}</span>` : ""}
      <button type="button" class="icon-btn project-more" data-project-menu="${esc(project.id)}"
              title="Настройки проекта" aria-label="Настройки проекта">${icon("dots")}</button>
    </div>`;
}

function projectPosition(project) {
  return Number.isFinite(project.position) ? project.position : 0;
}

function orderedProjects() {
  const projects = [...state.projects];
  projects.sort(
    (a, b) =>
      projectPosition(a) - projectPosition(b) ||
      String(a.created_at || "").localeCompare(String(b.created_at || ""))
  );
  if (state.preferences.sort_projects_by_tasks) {
    projects.sort(
      (a, b) =>
        activeCount(a) - activeCount(b) ||
        projectPosition(a) - projectPosition(b)
    );
  }
  return projects;
}

function renderSidebar() {
  elements.projectList.classList.toggle(
    "is-sortable",
    !state.preferences.sort_projects_by_tasks
  );
  elements.projectList.innerHTML = orderedProjects().map(projectItemHTML).join("");
  elements.projectEmpty.hidden = state.projects.length > 0;
  const calendarActive = state.view === "calendar";
  elements.calendarNav.classList.toggle("is-active", calendarActive);
  elements.calendarNav.setAttribute("aria-current", calendarActive ? "page" : "false");
}

function renderSettingsSubtitle() {
  if (!elements.dbSize) return;
  const size = state.info?.db?.size;
  elements.dbSize.textContent =
    size == null ? "Календарь и база данных" : `Календарь · база ${formatSize(size)}`;
  elements.dbPill.title = "Настройки";
}

function applyCalendarPreferences(preferences = {}) {
  state.preferences = {
    show_project_name: preferences.show_project_name !== false,
    show_completed: preferences.show_completed !== false,
    sort_projects_by_tasks: preferences.sort_projects_by_tasks === true,
    show_weekends: preferences.show_weekends !== false,
  };
  if (calendar) calendar.setPreferences(state.preferences);
  renderSidebar();
}

async function persistProjectOrder(ids) {
  if (projectOrderBusy) return;
  projectOrderBusy = true;
  const previous = state.projects;
  const positions = new Map(ids.map((id, index) => [id, index]));
  state.projects = state.projects
    .map((project) => ({ ...project, position: positions.get(project.id) ?? project.position }))
    .sort((a, b) => projectPosition(a) - projectPosition(b));
  renderSidebar();
  try {
    const result = await api.reorderProjects(ids);
    state.projects = result.projects || state.projects;
    renderSidebar();
  } catch (error) {
    state.projects = previous;
    renderSidebar();
    toast(error?.message || "Не удалось сохранить порядок проектов", "error");
  } finally {
    projectOrderBusy = false;
  }
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
  state.view = "welcome";
  state.activeId = null;
  state.activeProject = null;
  state.calendarToken += 1;
  elements.projectView.hidden = true;
  elements.calendarView.hidden = true;
  elements.welcome.hidden = false;
  elements.chatBackdrop.hidden = true;
  document.title = "Tasker — проекты, задачи и заметки";
  if (location.hash) {
    window.history.replaceState({}, "", location.pathname + location.search);
  }
  renderSidebar();
}

function showProject() {
  state.view = "project";
  elements.welcome.hidden = true;
  elements.calendarView.hidden = true;
  elements.projectView.hidden = false;
  applyChatVisibility();
}

function showCalendar() {
  state.view = "calendar";
  state.activeId = null;
  state.activeProject = null;
  state.calendarToken += 1;
  elements.welcome.hidden = true;
  elements.projectView.hidden = true;
  elements.calendarView.hidden = false;
  elements.chatBackdrop.hidden = true;
  document.title = "Календарь — Tasker";
  renderSidebar();
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
/* ширина панели заметок                                              */
/* ------------------------------------------------------------------ */
function maxChatWidth() {
  const sidebarWidth = elements.sidebar?.getBoundingClientRect().width || 264;
  return Math.max(MIN_CHAT_WIDTH, window.innerWidth - sidebarWidth - 470);
}

function applyChatWidth(width, { persist = false } = {}) {
  const max = maxChatWidth();
  const value = Math.round(Math.min(Math.max(Number(width) || DEFAULT_CHAT_WIDTH, MIN_CHAT_WIDTH), max));
  chatWidth = value;
  elements.projectView.style.setProperty("--chat-width", `${value}px`);
  elements.chatResizer.setAttribute("aria-valuenow", String(value));
  elements.chatResizer.setAttribute("aria-valuemax", String(Math.round(max)));
  if (persist) {
    try {
      localStorage.setItem(CHAT_WIDTH_KEY, String(value));
    } catch (error) {
      /* ignore */
    }
  }
  return value;
}

function initChatWidth() {
  let stored = 0;
  try {
    stored = Number(localStorage.getItem(CHAT_WIDTH_KEY));
  } catch (error) {
    stored = 0;
  }
  applyChatWidth(stored > 0 ? stored : DEFAULT_CHAT_WIDTH);
}

function finishChatResize(event) {
  if (!resizingChat) return;
  resizingChat = false;
  document.body.classList.remove("is-resizing-chat");
  applyChatWidth(chatWidth, { persist: true });
  if (event && event.pointerId != null && elements.chatResizer.hasPointerCapture?.(event.pointerId)) {
    try {
      elements.chatResizer.releasePointerCapture(event.pointerId);
    } catch (error) {
      /* ignore */
    }
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

function calendarHash(period) {
  if (period.mode === "week") {
    return `#calendar/week/${period.weekStart}`;
  }
  return `#calendar/${monthParam(period.year, period.month)}`;
}

function parseCalendarHash() {
  if (location.hash === "#calendar" || location.hash === "#calendar/") {
    const now = new Date();
    return { mode: "month", year: now.getFullYear(), month: now.getMonth() };
  }
  const monthMatch = /^#calendar\/(\d{4}-\d{2})$/.exec(location.hash);
  if (monthMatch) {
    const parsed = parseMonthParam(monthMatch[1]);
    return parsed ? { mode: "month", ...parsed } : null;
  }
  const weekMatch = /^#calendar\/week(?:\/(\d{4}-\d{2}-\d{2}))?$/.exec(location.hash);
  if (weekMatch) {
    const start = weekStartKey(weekMatch[1] || todayKey());
    return start ? { mode: "week", weekStart: start } : null;
  }
  return null;
}

function normalizeCalendarPeriod(period) {
  if (period?.mode === "week") {
    const start = weekStartKey(period.weekStart || todayKey());
    if (!start) return null;
    const date = dateFromKey(start);
    return {
      mode: "week",
      weekStart: start,
      year: date.getFullYear(),
      month: date.getMonth(),
    };
  }
  const now = new Date();
  const date = new Date(
    period?.year ?? now.getFullYear(),
    period?.month ?? now.getMonth(),
    1,
    12
  );
  return { mode: "month", year: date.getFullYear(), month: date.getMonth() };
}

function currentCalendarPeriod() {
  const current = state.calendar;
  if (!current) return null;
  return current.mode === "week"
    ? { mode: "week", weekStart: current.weekStart }
    : { mode: "month", year: current.year, month: current.month };
}

async function openCalendar(period, { history = "push" } = {}) {
  const target = normalizeCalendarPeriod(period);
  if (!target) return;

  showCalendar();
  state.loadToken += 1;
  state.calendar = { ...target, scheduled: [], unscheduled: [] };
  const token = ++state.calendarToken;
  calendar.renderLoading(target);

  const hash = calendarHash(target);
  if (history === "push") {
    window.history.pushState({ view: "calendar" }, "", hash);
  } else if (history === "replace") {
    window.history.replaceState({ view: "calendar" }, "", hash);
  }

  try {
    const range =
      target.mode === "week"
        ? weekRange(target.weekStart)
        : calendarRange(target.year, target.month);
    if (!range.start || !range.end) {
      throw new Error("Не удалось определить диапазон дат");
    }
    const response = await api.calendar(range.start, range.end);
    if (token !== state.calendarToken || state.view !== "calendar") return;
    state.calendar = {
      ...target,
      start: response.start,
      end: response.end,
      scheduled: response.scheduled || [],
      unscheduled: response.unscheduled || [],
    };
    calendar.render(state.calendar);
  } catch (error) {
    if (token !== state.calendarToken) return;
    toast(error?.message || "Не удалось загрузить календарь", "error");
    calendar.render(
      { ...target, scheduled: [], unscheduled: [] },
      { loading: false }
    );
  }
}

function changeCalendarMode(nextMode) {
  const current = state.calendar;
  if (!current || current.mode === nextMode) return;
  if (nextMode === "week") {
    let anchor = todayKey();
    if (current.mode === "month") {
      const now = new Date();
      if (current.year !== now.getFullYear() || current.month !== now.getMonth()) {
        anchor = dateKey(new Date(current.year, current.month, 1, 12));
      }
    }
    const start = weekStartKey(anchor);
    if (start) {
      openCalendar({ mode: "week", weekStart: start }, { history: "push" });
    }
    return;
  }
  const date = dateFromKey(current.weekStart || weekStartKey(todayKey()));
  if (!date) return;
  openCalendar(
    { mode: "month", year: date.getFullYear(), month: date.getMonth() },
    { history: "push" }
  );
}

function navigateCalendar(delta) {
  const current = state.calendar;
  if (!current) return;
  if (current.mode === "week") {
    const start = addDaysKey(current.weekStart, delta * 7);
    if (start) {
      openCalendar({ mode: "week", weekStart: start }, { history: "replace" });
    }
    return;
  }
  const date = new Date(current.year, current.month + delta, 1, 12);
  openCalendar(
    { mode: "month", year: date.getFullYear(), month: date.getMonth() },
    { history: "replace" }
  );
}

function goCalendarToday() {
  const now = new Date();
  if (state.calendar?.mode === "week") {
    openCalendar(
      { mode: "week", weekStart: weekStartKey(todayKey()) },
      { history: "replace" }
    );
  } else {
    openCalendar(
      { mode: "month", year: now.getFullYear(), month: now.getMonth() },
      { history: "replace" }
    );
  }
}

function findCalendarTask(taskId) {
  if (!state.calendar) return null;
  return (
    [...state.calendar.scheduled, ...state.calendar.unscheduled].find(
      (task) => task.id === taskId
    ) || null
  );
}

function calendarTaskOrder(task) {
  const order = Number(task?.calendar_order);
  return Number.isFinite(order) ? order : Number.MAX_SAFE_INTEGER;
}

function sortCalendarTasks(tasks) {
  return [...tasks].sort(
    (a, b) =>
      calendarTaskOrder(a) - calendarTaskOrder(b) ||
      String(a.scheduled_date || "").localeCompare(String(b.scheduled_date || "")) ||
      (a.position ?? 0) - (b.position ?? 0)
  );
}

async function scheduleCalendarTask(taskId, date) {
  if (state.view !== "calendar" || !state.calendar) return;
  const task = findCalendarTask(taskId);
  if (!task) return;
  const nextDate = date || null;
  if ((task.scheduled_date || null) === nextDate) return;
  const duration = taskDurationDays(task);
  const nextEnd = nextDate ? addDaysKey(nextDate, duration - 1) : null;

  const previous = state.calendar;
  const moved = {
    ...task,
    scheduled_date: nextDate,
    scheduled_end_date: nextEnd === nextDate ? null : nextEnd,
  };
  let scheduled = previous.scheduled.filter((item) => item.id !== taskId);
  let unscheduled = previous.unscheduled.filter((item) => item.id !== taskId);
  if (nextDate) {
    scheduled = sortCalendarTasks([...scheduled, moved]);
  } else {
    unscheduled = [...unscheduled, moved];
  }
  state.calendar = { ...previous, scheduled, unscheduled };
  calendar.render(state.calendar);

  try {
    await api.updateTask(taskId, {
      scheduled_date: nextDate,
      scheduled_end_date: nextEnd === nextDate ? null : nextEnd,
    });
  } catch (error) {
    toast(error?.message || "Не удалось изменить дату задачи", "error");
    if (state.view === "calendar" && state.calendar) {
      await refreshCalendarIfVisible();
    }
  }
}

async function resizeCalendarTask(taskId, endDate) {
  if (state.view !== "calendar" || !state.calendar) return;
  const task = findCalendarTask(taskId);
  if (!task?.scheduled_date) return;
  const nextEnd = endDate < task.scheduled_date ? task.scheduled_date : endDate;
  const previous = state.calendar;
  const changed = {
    ...task,
    scheduled_end_date: nextEnd === task.scheduled_date ? null : nextEnd,
  };
  state.calendar = {
    ...previous,
    scheduled: sortCalendarTasks(
      previous.scheduled.map((item) => (item.id === taskId ? changed : item))
    ),
  };
  calendar.render(state.calendar);

  try {
    await api.updateTask(taskId, {
      scheduled_end_date: changed.scheduled_end_date,
    });
  } catch (error) {
    toast(error?.message || "Не удалось растянуть задачу", "error");
    if (state.view === "calendar" && state.calendar) {
      await refreshCalendarIfVisible();
    }
  }
}

async function reorderCalendarTask(taskId, target) {
  if (state.view !== "calendar" || !state.calendar) return;
  const ordered = sortCalendarTasks(state.calendar.scheduled);
  const index = ordered.findIndex((task) => task.id === taskId);
  if (index < 0) return;

  if (typeof target === "string") {
    const next = index + (target === "up" ? -1 : 1);
    if (next < 0 || next >= ordered.length) return;
    [ordered[index], ordered[next]] = [ordered[next], ordered[index]];
  } else if (target && typeof target === "object") {
    const targetIndex = ordered.findIndex((task) => task.id === target.targetId);
    if (targetIndex < 0 || target.targetId === taskId) return;
    const [moved] = ordered.splice(index, 1);
    let insertAt = ordered.findIndex((task) => task.id === target.targetId);
    if (insertAt < 0) return;
    if (!target.before) insertAt += 1;
    ordered.splice(insertAt, 0, moved);
  } else {
    return;
  }

  ordered.forEach((task, position) => {
    task.calendar_order = position;
  });
  state.calendar = { ...state.calendar, scheduled: ordered };
  calendar.render(state.calendar);

  try {
    await api.reorderCalendar(ordered.map((task) => task.id));
  } catch (error) {
    toast(error?.message || "Не удалось изменить порядок задач", "error");
    if (state.view === "calendar" && state.calendar) {
      await refreshCalendarIfVisible();
    }
  }
}

let lastCalendarProjectId = null;

function refreshCalendarIfVisible() {
  const period = currentCalendarPeriod();
  if (state.view === "calendar" && period) {
    return openCalendar(period, { history: "none" });
  }
  return Promise.resolve();
}

function openCalendarTaskEditor(taskId = null, date = null) {
  const task = taskId ? findCalendarTask(taskId) : null;
  if (taskId && !task) {
    toast("Задача не найдена — обновите календарь", "error");
    return;
  }
  if (!task && !state.projects.length) {
    toast("Сначала создайте проект", "info");
    openNewProjectForm();
    return;
  }
  if (task?.project_id) lastCalendarProjectId = task.project_id;
  const defaultProjectId =
    task?.project_id || lastCalendarProjectId || state.projects[0]?.id || null;

  openTaskEditor({
    task,
    projects: state.projects,
    defaultDate: date,
    defaultProjectId,
    onSave: async (payload) => {
      if (task) {
        const response = await api.updateTask(task.id, {
          title: payload.title,
          description: payload.description,
          status: payload.status,
          scheduled_date: payload.scheduled_date,
          scheduled_end_date: payload.scheduled_end_date,
        });
        setProject(response.project);
        lastCalendarProjectId = task.project_id;
      } else {
        const response = await api.createTask(payload.projectId, {
          title: payload.title,
          description: payload.description,
          status: payload.status,
          scheduled_date: payload.scheduled_date,
          scheduled_end_date: payload.scheduled_end_date,
        });
        setProject(response.project);
        lastCalendarProjectId = payload.projectId;
      }
      renderSidebar();
      refreshInfo();
      await refreshCalendarIfVisible();
    },
    onDelete: async () => {
      if (!task) return;
      const response = await api.deleteTask(task.id);
      setProject(response.project);
      renderSidebar();
      refreshInfo();
      await refreshCalendarIfVisible();
    },
  });
}

async function selectProject(id, { history = "push" } = {}) {
  const project = getProject(id);
  if (!project) return;
  if (state.activeId === id && !elements.projectView.hidden) return;

  state.view = "project";
  state.calendarToken += 1;
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

async function saveTask({ id, title, description, status, scheduled_date, scheduled_end_date }) {
  const projectId = state.activeId;
  if (!projectId) throw new Error("Проект не выбран");
  const payload = {
    title,
    description,
    status,
    scheduled_date: scheduled_date || null,
    scheduled_end_date: scheduled_end_date || null,
  };
  const response = id
    ? await api.updateTask(id, payload)
    : await api.createTask(projectId, payload);
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
function syncProviderInfo(sync = state.info?.sync) {
  const mode = sync?.mode || "folder";
  if (mode === "drive" && sync?.drive?.connected) {
    return { configured: true, label: "Google Drive API", remote: sync.remote || null };
  }
  if (mode === "yandex" && sync?.yandex?.connected) {
    return { configured: true, label: "Яндекс Диск", remote: sync.remote || null };
  }
  if (mode === "folder" && sync?.folder) {
    return { configured: true, label: "Папка Google Диска", remote: sync.remote || null };
  }
  return { configured: false, label: "Синхронизация не настроена", remote: null };
}

function renderQuickSync() {
  if (!elements.syncVersionNumber) return;
  const sync = state.info?.sync || {};
  const provider = syncProviderInfo(sync);
  const remote = provider.remote;
  const last = sync.last_sync || {};
  const version = remote?.version ?? last?.version ?? null;
  const localWrite = state.info?.db?.last_write_at;
  const hasLocalChanges = Boolean(
    last.at && localWrite && Date.parse(localWrite) > Date.parse(last.at)
  );

  elements.quickSyncPush.disabled = !provider.configured || quickSyncBusy;
  elements.quickSyncPull.disabled = !provider.configured || quickSyncBusy;
  elements.syncVersionDot.classList.toggle(
    "is-online",
    provider.configured && Boolean(version) && !hasLocalChanges
  );
  elements.syncVersionDot.classList.toggle(
    "is-warning",
    provider.configured && (hasLocalChanges || !version)
  );
  elements.syncVersionMeta.classList.toggle("is-warning", hasLocalChanges);

  if (!provider.configured) {
    elements.syncVersionNumber.textContent = "—";
    elements.syncVersionMeta.textContent = "Синхронизация не настроена";
    elements.syncVersionCard.title = "Открыть настройки синхронизации";
    return;
  }

  elements.syncVersionNumber.textContent = version ? `v${version}` : "—";
  const device = remote?.device || last.device || "";
  const place = `${provider.label}${device ? ` · ${device}` : ""}`;
  if (hasLocalChanges) {
    elements.syncVersionMeta.textContent = `${place} · есть изменения`;
    elements.syncVersionCard.title = "Есть локальные изменения — отправьте их на Диск";
  } else if (version) {
    elements.syncVersionMeta.textContent = place;
    elements.syncVersionCard.title = remote?.updated_at
      ? `Версия на Диске: ${formatFullDate(remote.updated_at)}`
      : "Открыть настройки синхронизации";
  } else {
    elements.syncVersionMeta.textContent = "На Диске пока нет версии";
    elements.syncVersionCard.title = "Открыть настройки синхронизации";
  }
}

async function refreshQuickSync({ refreshRemote = false } = {}) {
  renderQuickSync();
  if (!refreshRemote) return;
  const provider = syncProviderInfo();
  if (!provider.configured) return;
  const request = ++quickSyncRemoteRequest;
  try {
    const sync = await api.syncStatus(true);
    if (request !== quickSyncRemoteRequest) return;
    state.info = { ...state.info, sync };
    renderQuickSync();
  } catch (error) {
    /* последняя известная версия остаётся в виджете */
  }
}

function setQuickSyncBusy(value) {
  quickSyncBusy = value;
  const pushLabel = elements.quickSyncPush.querySelector("span");
  const pullLabel = elements.quickSyncPull.querySelector("span");
  if (pushLabel) pushLabel.textContent = value ? "Отправляю…" : "На Диск";
  if (pullLabel) pullLabel.textContent = value ? "Скачиваю…" : "Скачать";
  renderQuickSync();
}

async function quickSyncSend() {
  if (quickSyncBusy) return;
  const sync = state.info?.sync;
  const provider = syncProviderInfo(sync);
  if (!provider.configured) {
    openSettingsModal();
    toast("Сначала настройте синхронизацию", "info");
    return;
  }
  const confirmed = await confirmDialog({
    title: "Отправить данные на Диск?",
    message: `Текстовая база и файлы будут отправлены на ${provider.label}.`,
    confirmLabel: "Отправить",
    danger: false,
  });
  if (!confirmed) return;
  setQuickSyncBusy(true);
  try {
    const result = await api.syncSend();
    state.info = { ...state.info, sync: result.sync || state.info.sync };
    toast("Данные отправлены на Диск", "success");
    await refreshInfo();
    await refreshQuickSync({ refreshRemote: true });
  } catch (error) {
    toast(error?.message || "Не удалось отправить данные", "error");
  } finally {
    setQuickSyncBusy(false);
  }
}

async function quickSyncReceive() {
  if (quickSyncBusy) return;
  const sync = state.info?.sync;
  const provider = syncProviderInfo(sync);
  if (!provider.configured) {
    openSettingsModal();
    toast("Сначала настройте синхронизацию", "info");
    return;
  }
  const version = sync?.remote?.version ?? sync?.last_sync?.version;
  const confirmed = await confirmDialog({
    title: "Скачать данные с Диска?",
    message: version
      ? `Локальная база и файлы будут заменены версией ${version} с Диска. Перед заменой будет создана резервная копия.`
      : "Локальная база и файлы будут заменены версией с Диска. Перед заменой будет создана резервная копия.",
    confirmLabel: "Скачать",
    danger: true,
  });
  if (!confirmed) return;
  setQuickSyncBusy(true);
  try {
    await api.syncReceive();
    toast("Данные скачаны — перезагружаю Tasker", "success");
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    toast(error?.message || "Не удалось скачать данные", "error");
    setQuickSyncBusy(false);
  }
}

async function refreshInfo() {
  try {
    state.info = await api.info();
    renderSettingsSubtitle();
    renderQuickSync();
  } catch (error) {
    /* размер не критичен */
  }
}

function downloadBackup() {
  const link = document.createElement("a");
  link.href = api.backupDownloadUrl;
  link.download = "tasker.zip";
  document.body.appendChild(link);
  link.click();
  link.remove();
  toast("Архив базы и файлов выгружается — сохраните его в надёжном месте", "info");
}

function openSettingsModal() {
  api
    .info()
    .then((info) => {
      state.info = info;
      renderSettingsSubtitle();
      const db = info.db || {};
      const stats = info.stats || {};
      const preferences = info.preferences || state.preferences;
      applyCalendarPreferences(preferences);
      const sync = info.sync || {};
      const remote = sync.remote || null;
      const lastSync = sync.last_sync || {};
      const drive = sync.drive || {};
      const yandex = sync.yandex || {};
      const syncMode =
        sync.mode === "drive" || sync.mode === "yandex" ? sync.mode : "folder";
      const remoteText = syncMode === "drive"
        ? drive.connected
          ? "Google Drive API подключён"
          : "Google Drive API не подключён"
        : syncMode === "yandex"
          ? yandex.connected
            ? "Яндекс Диск подключён"
            : "Яндекс Диск не подключён"
          : remote
            ? `На Диске: версия ${remote.version ?? "—"} · ${esc(remote.device || "—")} · ${
                remote.updated_at ? esc(formatFullDate(remote.updated_at)) : "—"
              }`
            : sync.folder
              ? "В выбранной папке пока нет данных Tasker"
              : "Папка Google Диска не выбрана";
      const lastText = lastSync.version
        ? `Последняя синхронизация: версия ${lastSync.version} · ${esc(
            lastSync.device || "—"
          )} · ${lastSync.at ? esc(formatFullDate(lastSync.at)) : "—"}`
        : "Синхронизация ещё не выполнялась";
      const savedAt = db.last_write_at || db.modified_at;
      const writable = Boolean(db.writable);
      const statusTitle = writable
        ? db.exists
          ? "Все изменения сохранены на диск"
          : "Новый файл будет создан"
        : "Файл защищён от записи";
      const statusText = writable
        ? db.exists
          ? `Запись включена · последнее сохранение: ${savedAt ? esc(formatFullDate(savedAt)) : "—"}`
          : "Файл появится после первого сохранения"
        : "Выберите другой файл или разрешите запись в папку";
      const body = `
        <div class="settings-calendar-section">
          <div class="db-section-label">Календарь</div>
          <label class="settings-check">
            <input type="checkbox" id="setting-show-project"
                   ${state.preferences.show_project_name ? "checked" : ""}>
            <span class="settings-check-copy">
              <strong>Показывать название проекта</strong>
              <span>Подпись проекта на карточках в календаре и общем списке.</span>
            </span>
          </label>
          <label class="settings-check">
            <input type="checkbox" id="setting-show-completed"
                   ${state.preferences.show_completed ? "checked" : ""}>
            <span class="settings-check-copy">
              <strong>Показывать выполненные задачи</strong>
              <span>Если выключить, завершённые задачи останутся на доске, но исчезнут из календаря.</span>
            </span>
          </label>
          <label class="settings-check">
            <input type="checkbox" id="setting-show-weekends"
                   ${state.preferences.show_weekends ? "checked" : ""}>
            <span class="settings-check-copy">
              <strong>Показывать выходные</strong>
              <span>Суббота и воскресенье в месяце и неделе. Если выключить, останутся только рабочие дни.</span>
            </span>
          </label>
          <p class="db-meta settings-hint">Настройки применяются сразу и сохраняются.</p>
        </div>
        <div class="settings-projects-section">
          <div class="db-section-label">Проекты</div>
          <label class="settings-check">
            <input type="checkbox" id="setting-sort-projects"
                   ${state.preferences.sort_projects_by_tasks ? "checked" : ""}>
            <span class="settings-check-copy">
              <strong>Автосортировка проектов по возрастанию</strong>
              <span>По числу активных задач. Выключено — перетаскивайте проекты в списке вручную.</span>
            </span>
          </label>
        </div>
        <div class="settings-sync-section">
          <div class="db-section-label">Синхронизация</div>
          <p class="settings-sync-hint">Можно использовать папку на компьютере, Google Drive API или Яндекс Диск. Обмен выполняется только по кнопкам.</p>
          <label class="field settings-sync-field">
            <span class="field-label">Способ синхронизации</span>
            <select class="input" id="sync-mode">
              <option value="folder" ${syncMode === "folder" ? "selected" : ""}>Папка Google Диска (Drive для компьютера)</option>
              <option value="drive" ${syncMode === "drive" ? "selected" : ""}>Google Drive API (без приложения)</option>
              <option value="yandex" ${syncMode === "yandex" ? "selected" : ""}>Яндекс Диск (токен)</option>
            </select>
          </label>
          <div id="sync-folder-fields" ${syncMode === "folder" ? "" : "hidden"}>
            <label class="field settings-sync-field">
              <span class="field-label">Папка синхронизации</span>
              <div class="db-path-input-row">
                <input class="input" id="sync-folder" type="text" spellcheck="false"
                       value="${esc(sync.folder || "")}" placeholder="Например, G:\\My Drive\\Tasker">
                <button type="button" class="btn btn-ghost" data-act="sync-browse">Обзор…</button>
              </div>
            </label>
          </div>
          <div id="sync-drive-fields" ${syncMode === "drive" ? "" : "hidden"}>
            <p class="settings-sync-hint">Создайте OAuth-клиент типа «Desktop app» в Google Cloud Console и включите Google Drive API. Нужен только scope drive.file.</p>
            <label class="field settings-sync-field">
              <span class="field-label">OAuth Client ID</span>
              <input class="input" id="drive-client-id" type="text" spellcheck="false"
                     value="${esc(drive.client_id || "")}" placeholder="....apps.googleusercontent.com">
            </label>
            <label class="field settings-sync-field">
              <span class="field-label">OAuth Client Secret</span>
              <input class="input" id="drive-client-secret" type="password" spellcheck="false"
                     placeholder="${drive.client_secret_set ? "Сохранён — оставьте пустым" : "Вставьте Client Secret"}">
            </label>
            <div class="drive-auth-row">
              <span class="drive-auth-status" id="drive-status">${drive.connected ? "Google Диск подключён" : "Google Диск не подключён"}</span>
              <button type="button" class="btn btn-quiet btn-sm" data-act="drive-disconnect" ${drive.connected ? "" : "hidden"}>Отключить</button>
              <button type="button" class="btn btn-primary btn-sm" data-act="drive-connect">Подключить</button>
            </div>
          </div>
          <div id="sync-yandex-fields" ${syncMode === "yandex" ? "" : "hidden"}>
            <p class="settings-sync-hint">Нужен OAuth-токен Яндекс ID с правами <code>cloud_api:disk.app_folder</code> и <code>cloud_api:disk.info</code>. Redirect URI приложения: <code>https://oauth.yandex.ru/verification_code</code>.</p>
            <label class="field settings-sync-field">
              <span class="field-label">OAuth-токен Яндекса</span>
              <input class="input" id="yandex-token" type="password" spellcheck="false"
                     placeholder="${yandex.token_set ? "Сохранён — оставьте пустым" : "Вставьте токен из Яндекс OAuth"}">
            </label>
            <div class="yandex-auth-row">
              <span class="yandex-auth-status" id="yandex-status">${yandex.connected ? `Подключён${yandex.total_space ? ` · ${formatSize(yandex.used_space || 0)} из ${formatSize(yandex.total_space)}` : ""}` : "Яндекс Диск не подключён"}</span>
              <button type="button" class="btn btn-quiet btn-sm" data-act="yandex-disconnect" ${yandex.connected ? "" : "hidden"}>Отключить</button>
              <button type="button" class="btn btn-primary btn-sm" data-act="yandex-connect">Подключить</button>
            </div>
            <p class="db-meta">Файлы хранятся в папке приложения на Яндекс Диске; доступ к остальному Диску не нужен.</p>
          </div>
          <label class="field settings-sync-field">
            <span class="field-label">Имя этого устройства</span>
            <input class="input" id="sync-device" type="text" maxlength="80"
                   value="${esc(sync.device_name || "")}">
          </label>
          <div class="sync-remote-state">
            <span class="sync-remote-dot"></span>
            <div>
              <strong id="sync-remote-title">${remoteText}</strong>
              <span id="sync-remote-last">${lastText}</span>
            </div>
          </div>
          <div class="sync-actions">
            <button type="button" class="btn btn-quiet" data-act="sync-receive">${icon("download")}<span>Скачать с Диска</span></button>
            <button type="button" class="btn btn-primary" data-act="sync-send">${icon("database")}<span>Отправить на Диск</span></button>
          </div>
          <p class="db-meta">Сейчас синхронизируются текстовая база и папка файлов. Версия на Диске и автор показаны выше.</p>
        </div>
        <div class="db-section">
          <div class="db-section-label">Текущая база</div>
          <div class="db-path-row">
            <code class="db-path">${esc(db.path || "")}</code>
            <button type="button" class="icon-btn" data-copy-path title="Скопировать путь" aria-label="Скопировать путь">${icon("copy")}</button>
          </div>
          <div class="db-status ${writable ? "is-ok" : "is-error"}">
            <span class="db-status-dot"></span>
            <div class="db-status-copy">
              <strong>${statusTitle}</strong>
              <span>${statusText}</span>
            </div>
          </div>
          <p class="db-meta">${formatSize(db.size || 0)} · изменён ${esc(formatFullDate(db.modified_at))}</p>
        </div>
        <div class="db-stats">
          <div class="db-stat"><span class="db-stat-value">${stats.projects ?? 0}</span><span class="db-stat-label">${plural(stats.projects ?? 0, "проект", "проекта", "проектов")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.tasks ?? 0}</span><span class="db-stat-label">${plural(stats.tasks ?? 0, "задача", "задачи", "задач")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.messages ?? 0}</span><span class="db-stat-label">${plural(stats.messages ?? 0, "заметка", "заметки", "заметок")}</span></div>
          <div class="db-stat"><span class="db-stat-value">${stats.attachments ?? 0}</span><span class="db-stat-label">${plural(stats.attachments ?? 0, "файл", "файла", "файлов")}</span></div>
        </div>
        <div class="db-switch">
          <div class="db-section-label">Выбрать другой файл базы</div>
          <div class="db-path-input-row">
            <input class="input" id="db-new-path" type="text" spellcheck="false"
                   value="${esc(db.path || "")}" aria-label="Путь к файлу базы">
            <button type="button" class="btn btn-ghost" data-act="browse">Обзор…</button>
          </div>
          <div class="db-path-actions">
            <span class="db-hint">Если файла нет, он будет создан. Текущая база останется на диске.</span>
            <button type="button" class="btn btn-quiet btn-sm" data-act="default">По умолчанию</button>
            <button type="button" class="btn btn-primary btn-sm" data-act="apply">Применить</button>
          </div>
          <p class="db-meta db-config">Путь запоминается в <code>${esc(db.config_path || "")}</code></p>
        </div>
        <div class="db-note">
          ${icon("database")}
          <p>Текстовая база и папка файлов лежат рядом с файлом базы:
          <code>${esc(db.files_dir || "")}</code>. Для резервной копии используйте
          «Сохранить копию» или «Скачать архив» — они включают и базу, и файлы.</p>
        </div>`;

      const footer = `
        <button type="button" class="btn btn-quiet" data-act="folder">${icon("folder")}<span>Открыть папку</span></button>
        <button type="button" class="btn btn-quiet" data-act="backup">${icon("database")}<span>Сохранить копию</span></button>
        <span class="modal-footer-spacer"></span>
        <button type="button" class="btn btn-primary" data-act="download">${icon("download")}<span>Скачать архив</span></button>`;

      openModal({
        title: "Настройки",
        subtitle: "Календарь, проекты, файлы и синхронизация",
        className: "modal-db modal-settings",
        body,
        footer,
        onMount({ overlay, close }) {
          const pathInput = overlay.querySelector("#db-new-path");
          const applyButton = overlay.querySelector('[data-act="apply"]');
          const browseButton = overlay.querySelector('[data-act="browse"]');
          const showProject = overlay.querySelector("#setting-show-project");
          const showCompleted = overlay.querySelector("#setting-show-completed");
          const showWeekends = overlay.querySelector("#setting-show-weekends");
          const sortProjects = overlay.querySelector("#setting-sort-projects");
          let busy = false;
          let savingPreferences = false;

          const savePreferences = async () => {
            if (savingPreferences) return;
            const previous = { ...state.preferences };
            const next = {
              show_project_name: showProject.checked,
              show_completed: showCompleted.checked,
              sort_projects_by_tasks: sortProjects.checked,
              show_weekends: showWeekends.checked,
            };
            const checkboxes = [showProject, showCompleted, showWeekends, sortProjects];
            savingPreferences = true;
            checkboxes.forEach((checkbox) => {
              checkbox.disabled = true;
            });
            applyCalendarPreferences(next);
            try {
              const result = await api.saveCalendarSettings(next);
              const saved = result.preferences || next;
              state.info = { ...state.info, preferences: saved };
              applyCalendarPreferences(saved);
              showProject.checked = state.preferences.show_project_name;
              showCompleted.checked = state.preferences.show_completed;
              showWeekends.checked = state.preferences.show_weekends;
              sortProjects.checked = state.preferences.sort_projects_by_tasks;
            } catch (error) {
              applyCalendarPreferences(previous);
              showProject.checked = previous.show_project_name;
              showCompleted.checked = previous.show_completed;
              showWeekends.checked = previous.show_weekends;
              sortProjects.checked = previous.sort_projects_by_tasks;
              toast(error?.message || "Не удалось сохранить настройки", "error");
            } finally {
              savingPreferences = false;
              checkboxes.forEach((checkbox) => {
                checkbox.disabled = false;
              });
            }
          };

          showProject.addEventListener("change", savePreferences);
          showCompleted.addEventListener("change", savePreferences);
          showWeekends.addEventListener("change", savePreferences);
          sortProjects.addEventListener("change", savePreferences);

          const syncModeInput = overlay.querySelector("#sync-mode");
          const syncFolderInput = overlay.querySelector("#sync-folder");
          const syncFolderFields = overlay.querySelector("#sync-folder-fields");
          const syncDriveFields = overlay.querySelector("#sync-drive-fields");
          const syncYandexFields = overlay.querySelector("#sync-yandex-fields");
          const syncDeviceInput = overlay.querySelector("#sync-device");
          const syncRemoteTitle = overlay.querySelector("#sync-remote-title");
          const syncRemoteLast = overlay.querySelector("#sync-remote-last");
          const syncBrowseButton = overlay.querySelector('[data-act="sync-browse"]');
          const syncSendButton = overlay.querySelector('[data-act="sync-send"]');
          const syncReceiveButton = overlay.querySelector('[data-act="sync-receive"]');
          const driveClientId = overlay.querySelector("#drive-client-id");
          const driveClientSecret = overlay.querySelector("#drive-client-secret");
          const driveStatus = overlay.querySelector("#drive-status");
          const driveConnectButton = overlay.querySelector('[data-act="drive-connect"]');
          const driveDisconnectButton = overlay.querySelector('[data-act="drive-disconnect"]');
          const yandexToken = overlay.querySelector("#yandex-token");
          const yandexStatus = overlay.querySelector("#yandex-status");
          const yandexConnectButton = overlay.querySelector('[data-act="yandex-connect"]');
          const yandexDisconnectButton = overlay.querySelector('[data-act="yandex-disconnect"]');
          let syncBusy = false;
          let drivePollTimer = null;

          const applySyncMode = () => {
            const mode =
              syncModeInput.value === "drive" || syncModeInput.value === "yandex"
                ? syncModeInput.value
                : "folder";
            syncFolderFields.hidden = mode !== "folder";
            syncDriveFields.hidden = mode !== "drive";
            syncYandexFields.hidden = mode !== "yandex";
          };

          const applySyncState = (sync = {}) => {
            state.info = { ...state.info, sync };
            const remote = sync.remote;
            const last = sync.last_sync || {};
            const isDrive = sync.mode === "drive";
            const isYandex = sync.mode === "yandex";
            const drive = sync.drive || {};
            const yandex = sync.yandex || {};
            syncRemoteTitle.textContent = isDrive
              ? drive.connected
                ? "Google Drive API подключён"
                : "Google Drive API не подключён"
              : isYandex
                ? yandex.connected
                  ? "Яндекс Диск подключён"
                  : "Яндекс Диск не подключён"
                : remote
                  ? `На Диске: версия ${remote.version ?? "—"} · ${remote.device || "—"} · ${
                      remote.updated_at ? formatFullDate(remote.updated_at) : "—"
                    }`
                  : sync.folder
                    ? "В выбранной папке пока нет данных Tasker"
                    : "Папка Google Диска не выбрана";
            syncRemoteLast.textContent = last.version
              ? `Последняя синхронизация: версия ${last.version} · ${last.device || "—"} · ${
                  last.at ? formatFullDate(last.at) : "—"
                }`
              : "Синхронизация ещё не выполнялась";
            if (driveStatus) {
              driveStatus.textContent = drive.connected
                ? "Google Диск подключён"
                : "Google Диск не подключён";
              driveDisconnectButton.hidden = !drive.connected;
            }
            if (yandexStatus) {
              yandexStatus.textContent = yandex.connected
                ? `Подключён${
                    yandex.total_space
                      ? ` · ${formatSize(yandex.used_space || 0)} из ${formatSize(yandex.total_space)}`
                      : ""
                  }`
                : "Яндекс Диск не подключён";
              yandexDisconnectButton.hidden = !yandex.connected;
            }
            renderQuickSync();
          };

          syncModeInput.addEventListener("change", applySyncMode);

          const persistSyncSettings = async () => {
            const mode =
              syncModeInput.value === "drive" || syncModeInput.value === "yandex"
                ? syncModeInput.value
                : "folder";
            const folder = syncFolderInput.value.trim();
            const deviceName = syncDeviceInput.value.trim();
            if (mode === "folder" && !folder) {
              toast("Сначала выберите папку Google Диска", "error");
              syncFolderInput.focus();
              return null;
            }
            const result = await api.saveSyncSettings(folder, deviceName, mode);
            applySyncState(result.sync || {});
            syncDeviceInput.value = state.info.sync?.device_name || deviceName;
            if (mode === "drive") {
              const clientId = driveClientId.value.trim();
              const clientSecret = driveClientSecret.value.trim();
              if (clientId) {
                const driveResult = await api.saveDriveSettings(clientId, clientSecret);
                applySyncState(driveResult.sync || {});
                driveClientSecret.value = "";
                driveClientSecret.placeholder = "Сохранён — оставьте пустым";
              }
            }
            if (mode === "yandex") {
              const token = yandexToken.value.trim();
              if (token) {
                const yandexResult = await api.yandexConnect(token);
                applySyncState(yandexResult.sync || {});
                yandexToken.value = "";
                yandexToken.placeholder = "Сохранён — оставьте пустым";
              }
            }
            return state.info.sync;
          };

          const setSyncBusy = (value) => {
            syncBusy = value;
            syncBrowseButton.disabled = value;
            syncSendButton.disabled = value;
            syncReceiveButton.disabled = value;
            syncFolderInput.disabled = value;
            syncDeviceInput.disabled = value;
            syncModeInput.disabled = value;
            driveClientId.disabled = value;
            driveClientSecret.disabled = value;
            driveConnectButton.disabled = value;
            driveDisconnectButton.disabled = value;
            yandexToken.disabled = value;
            yandexConnectButton.disabled = value;
            yandexDisconnectButton.disabled = value;
          };

          syncBrowseButton.addEventListener("click", async () => {
            try {
              const result = await api.browseSyncFolder();
              if (result?.path) {
                syncFolderInput.value = result.path;
                await persistSyncSettings();
              }
            } catch (error) {
              toast(error?.message || "Не удалось выбрать папку", "error");
            }
          });

          driveConnectButton.addEventListener("click", async () => {
            if (syncBusy) return;
            const clientId = driveClientId.value.trim();
            const clientSecret = driveClientSecret.value.trim();
            if (!clientId) {
              toast("Укажите OAuth Client ID", "error");
              driveClientId.focus();
              return;
            }
            setSyncBusy(true);
            try {
              await api.saveSyncSettings(
                syncFolderInput.value.trim(),
                syncDeviceInput.value.trim(),
                "drive"
              );
              const result = await api.startDriveAuth(clientId, clientSecret);
              driveClientSecret.value = "";
              driveClientSecret.placeholder = "Сохранён — оставьте пустым";
              driveStatus.textContent = "Ожидаю подтверждение в браузере…";
              let attempts = 0;
              if (drivePollTimer) window.clearInterval(drivePollTimer);
              drivePollTimer = window.setInterval(async () => {
                attempts += 1;
                try {
                  const sync = await api.syncStatus();
                  applySyncState(sync);
                  if (sync.drive?.connected) {
                    window.clearInterval(drivePollTimer);
                    drivePollTimer = null;
                    toast("Google Диск подключён", "success");
                  }
                } catch (error) {
                  /* продолжаем ждать */
                }
                if (attempts > 120) {
                  window.clearInterval(drivePollTimer);
                  drivePollTimer = null;
                }
              }, 1500);
            } catch (error) {
              toast(error?.message || "Не удалось начать подключение", "error");
            } finally {
              setSyncBusy(false);
            }
          });

          driveDisconnectButton.addEventListener("click", async () => {
            if (syncBusy) return;
            setSyncBusy(true);
            try {
              const result = await api.disconnectDrive();
              applySyncState(result.sync || {});
              toast("Google Диск отключён", "success");
            } catch (error) {
              toast(error?.message || "Не удалось отключить Google Диск", "error");
            } finally {
              setSyncBusy(false);
            }
          });

          yandexConnectButton.addEventListener("click", async () => {
            if (syncBusy) return;
            const token = yandexToken.value.trim();
            if (!token && !state.info.sync?.yandex?.token_set) {
              toast("Вставьте OAuth-токен Яндекс Диска", "error");
              yandexToken.focus();
              return;
            }
            setSyncBusy(true);
            try {
              await api.saveSyncSettings(
                syncFolderInput.value.trim(),
                syncDeviceInput.value.trim(),
                "yandex"
              );
              const result = await api.yandexConnect(token);
              applySyncState(result.sync || {});
              yandexToken.value = "";
              yandexToken.placeholder = "Сохранён — оставьте пустым";
              toast("Яндекс Диск подключён", "success");
            } catch (error) {
              toast(error?.message || "Не удалось подключить Яндекс Диск", "error");
            } finally {
              setSyncBusy(false);
            }
          });

          yandexDisconnectButton.addEventListener("click", async () => {
            if (syncBusy) return;
            setSyncBusy(true);
            try {
              const result = await api.yandexDisconnect();
              applySyncState(result.sync || {});
              yandexToken.value = "";
              yandexToken.placeholder = "Вставьте токен из Яндекс OAuth";
              toast("Яндекс Диск отключён", "success");
            } catch (error) {
              toast(error?.message || "Не удалось отключить Яндекс Диск", "error");
            } finally {
              setSyncBusy(false);
            }
          });

          syncSendButton.addEventListener("click", async () => {
            if (syncBusy) return;
            try {
              const sync = await persistSyncSettings();
              if (!sync) return;
              if (sync.mode === "drive" && !sync.drive?.connected) {
                toast("Сначала подключите Google Диск", "error");
                return;
              }
              if (sync.mode === "yandex" && !sync.yandex?.connected) {
                toast("Сначала подключите Яндекс Диск", "error");
                return;
              }
              const remote = sync.remote;
              const targetName =
                sync.mode === "yandex"
                  ? "Яндекс Диск"
                  : sync.mode === "drive"
                    ? "Google Диск"
                    : "папку Google Диска";
              const confirmed = await confirmDialog({
                title: "Отправить данные на Диск?",
                message: remote
                  ? `На Диске уже есть версия ${remote.version ?? "—"} (${remote.device || "—"}). Она будет заменена новой версией.`
                  : `Текстовая база и папка файлов будут отправлены на ${targetName}.`,
                confirmLabel: "Отправить",
                danger: false,
              });
              if (!confirmed) return;
              setSyncBusy(true);
              syncSendButton.querySelector("span").textContent = "Отправляю…";
              const result = await api.syncSend();
              applySyncState(result.sync || {});
              toast("Данные отправлены на Диск", "success");
            } catch (error) {
              toast(error?.message || "Не удалось отправить данные", "error");
            } finally {
              setSyncBusy(false);
              syncSendButton.querySelector("span").textContent = "Отправить на Диск";
            }
          });

          syncReceiveButton.addEventListener("click", async () => {
            if (syncBusy) return;
            try {
              const sync = await persistSyncSettings();
              if (!sync) return;
              if (sync.mode === "folder" && !sync.remote) {
                toast("В выбранной папке нет данных Tasker", "error");
                return;
              }
              if (sync.mode === "drive" && !sync.drive?.connected) {
                toast("Сначала подключите Google Диск", "error");
                return;
              }
              if (sync.mode === "yandex" && !sync.yandex?.connected) {
                toast("Сначала подключите Яндекс Диск", "error");
                return;
              }
              const version = sync.remote?.version ?? sync.last_sync?.version ?? "—";
              const confirmed = await confirmDialog({
                title: "Скачать данные с Диска?",
                message: `Локальная база и папка файлов будут заменены версией ${version} с Диска. Перед заменой будет создана резервная копия.`,
                confirmLabel: "Скачать",
                danger: true,
              });
              if (!confirmed) return;
              setSyncBusy(true);
              syncReceiveButton.querySelector("span").textContent = "Скачиваю…";
              await api.syncReceive();
              toast("Данные скачаны — перезагружаю Tasker", "success");
              close();
              window.setTimeout(() => window.location.reload(), 500);
            } catch (error) {
              toast(error?.message || "Не удалось скачать данные", "error");
            } finally {
              setSyncBusy(false);
              syncReceiveButton.querySelector("span").textContent = "Скачать с Диска";
            }
          });

          const setBusy = (value) => {
            busy = value;
            applyButton.disabled = value;
            browseButton.disabled = value;
            applyButton.textContent = value ? "Переключаюсь…" : "Применить";
          };

          const applyPath = async () => {
            if (busy) return;
            const path = pathInput.value.trim();
            if (!path) {
              toast("Укажите путь к файлу базы", "error");
              pathInput.focus();
              return;
            }
            setBusy(true);
            try {
              const result = await api.switchDb(path);
              try {
                sessionStorage.setItem(
                  "tasker-flash-success",
                  `База открыта: ${result.path}`
                );
                if (result.warning) {
                  sessionStorage.setItem("tasker-flash-warning", result.warning);
                }
              } catch (error) {
                /* ignore */
              }
              window.location.assign("/");
            } catch (error) {
              toast(error?.message || "Не удалось открыть базу", "error");
              setBusy(false);
            }
          };

          overlay.querySelector("[data-copy-path]").addEventListener("click", () => {
            copyText(db.path);
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
          overlay.querySelector('[data-act="default"]').addEventListener("click", () => {
            pathInput.value = db.default_path || "";
            pathInput.focus();
          });
          browseButton.addEventListener("click", async () => {
            if (busy) return;
            browseButton.disabled = true;
            try {
              const result = await api.browseDb();
              if (!result.cancelled) {
                pathInput.value = result.path;
                pathInput.focus();
              }
            } catch (error) {
              toast(
                error?.message ||
                  "Не удалось открыть системный диалог — введите путь вручную",
                "error"
              );
              pathInput.focus();
            } finally {
              browseButton.disabled = false;
            }
          });
          applyButton.addEventListener("click", applyPath);
          pathInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              applyPath();
            }
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
          refreshCalendarIfVisible();
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
              await selectProject(orderedProjects()[0].id, { history: "replace" });
            } else {
              showWelcome();
            }
          } else {
            refreshCalendarIfVisible();
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

  const clearProjectDropHints = (keepDragging = false) => {
    elements.projectList
      .querySelectorAll(".is-drop-before, .is-drop-after")
      .forEach((item) => item.classList.remove("is-drop-before", "is-drop-after"));
    if (!keepDragging) {
      elements.projectList
        .querySelectorAll(".is-dragging")
        .forEach((item) => item.classList.remove("is-dragging"));
    }
  };

  elements.projectList.addEventListener("dragstart", (event) => {
    if (state.preferences.sort_projects_by_tasks) {
      event.preventDefault();
      return;
    }
    const item = event.target.closest("[data-project-id]");
    if (!item) return;
    projectDragId = item.dataset.projectId;
    item.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", projectDragId);
  });

  elements.projectList.addEventListener("dragover", (event) => {
    if (!projectDragId || state.preferences.sort_projects_by_tasks) return;
    const item = event.target.closest("[data-project-id]");
    if (!item) return;
    if (item.dataset.projectId === projectDragId) {
      clearProjectDropHints(true);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const rect = item.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    elements.projectList
      .querySelectorAll(".is-drop-before, .is-drop-after")
      .forEach((row) => row.classList.remove("is-drop-before", "is-drop-after"));
    item.classList.add(after ? "is-drop-after" : "is-drop-before");
  });

  elements.projectList.addEventListener("drop", (event) => {
    if (!projectDragId || state.preferences.sort_projects_by_tasks) return;
    const target = event.target.closest("[data-project-id]");
    if (!target || target.dataset.projectId === projectDragId) return;
    event.preventDefault();
    const after = target.classList.contains("is-drop-after");
    const ids = orderedProjects().map((project) => project.id);
    const from = ids.indexOf(projectDragId);
    const targetId = target.dataset.projectId;
    let targetIndex = ids.indexOf(targetId);
    if (from === -1 || targetIndex === -1) return;
    ids.splice(from, 1);
    if (from < targetIndex) targetIndex -= 1;
    if (after) targetIndex += 1;
    ids.splice(targetIndex, 0, projectDragId);
    projectDragId = null;
    clearProjectDropHints();
    persistProjectOrder(ids);
  });

  elements.projectList.addEventListener("dragend", () => {
    projectDragId = null;
    clearProjectDropHints();
  });

  elements.projectMenuBtn.addEventListener("click", () => {
    if (state.activeId) openProjectSettings(state.activeId);
  });
  elements.projectTitle.addEventListener("click", () => {
    if (state.activeId) openProjectSettings(state.activeId);
  });
  elements.addTaskBtn.addEventListener("click", () => board.openNewTask("urgent"));

  elements.calendarNav.addEventListener("click", () => {
    const now = new Date();
    openCalendar({ mode: "month", year: now.getFullYear(), month: now.getMonth() });
  });

  elements.chatToggleBtn.addEventListener("click", toggleChat);
  elements.chatCloseBtn.addEventListener("click", toggleChat);
  elements.chatBackdrop.addEventListener("click", () => {
    mobileChatOpen = false;
    applyChatVisibility();
    elements.chatToggleBtn.focus();
  });

  elements.chatResizer.addEventListener("pointerdown", (event) => {
    if (isMobileView() || event.button !== 0) return;
    resizingChat = true;
    document.body.classList.add("is-resizing-chat");
    elements.chatResizer.focus({ preventScroll: true });
    try {
      elements.chatResizer.setPointerCapture(event.pointerId);
    } catch (error) {
      /* ignore */
    }
    event.preventDefault();
  });

  elements.chatResizer.addEventListener("pointermove", (event) => {
    if (!resizingChat) return;
    const right = elements.projectView.getBoundingClientRect().right;
    const halfHandle = elements.chatResizer.offsetWidth / 2;
    chatWidth = applyChatWidth(right - event.clientX - halfHandle);
  });

  elements.chatResizer.addEventListener("pointerup", finishChatResize);
  elements.chatResizer.addEventListener("pointercancel", finishChatResize);
  window.addEventListener("pointerup", finishChatResize);
  window.addEventListener("pointercancel", finishChatResize);

  elements.chatResizer.addEventListener("keydown", (event) => {
    if (isMobileView()) return;
    let delta = 0;
    if (event.key === "ArrowLeft") delta = 24;
    else if (event.key === "ArrowRight") delta = -24;
    else if (event.key === "Home") {
      event.preventDefault();
      applyChatWidth(DEFAULT_CHAT_WIDTH, { persist: true });
      return;
    }
    if (!delta) return;
    event.preventDefault();
    applyChatWidth(chatWidth + delta, { persist: true });
  });

  elements.chatResizer.addEventListener("dblclick", () => {
    if (isMobileView()) return;
    applyChatWidth(DEFAULT_CHAT_WIDTH, { persist: true });
  });

  elements.themeBtn.addEventListener("click", toggleTheme);
  elements.dbPill.addEventListener("click", openSettingsModal);
  elements.syncVersionCard.addEventListener("click", openSettingsModal);
  elements.quickSyncPush.addEventListener("click", quickSyncSend);
  elements.quickSyncPull.addEventListener("click", quickSyncReceive);

  document.getElementById("brand").addEventListener("click", (event) => {
    event.preventDefault();
  });

  window.addEventListener("popstate", () => {
    const calendarTarget = parseCalendarHash();
    if (calendarTarget) {
      openCalendar(calendarTarget, { history: "none" });
      return;
    }
    const id = parseProjectHash();
    if (id) {
      selectProject(id, { history: "none" });
    } else if (state.projects.length) {
      selectProject(orderedProjects()[0].id, { history: "replace" });
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
    if (!mobile) {
      applyChatWidth(chatWidth);
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
function showPendingFlash() {
  try {
    const success = sessionStorage.getItem("tasker-flash-success");
    const warning = sessionStorage.getItem("tasker-flash-warning");
    sessionStorage.removeItem("tasker-flash-success");
    sessionStorage.removeItem("tasker-flash-warning");
    if (success) toast(success, "success");
    if (warning) toast(warning, "error");
  } catch (error) {
    /* ignore */
  }
}

async function init() {
  bindEvents();
  startBrowserLifecycle();
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

  calendar = createCalendar({
    root: elements.calendarView,
    preferences: state.preferences,
    callbacks: {
      onNavigate: navigateCalendar,
      onToday: goCalendarToday,
      onModeChange: changeCalendarMode,
      onSchedule: scheduleCalendarTask,
      onResize: resizeCalendarTask,
      onReorder: reorderCalendarTask,
      onEditTask: (taskId) => openCalendarTaskEditor(taskId),
      onCreateTask: (date) => openCalendarTaskEditor(null, date),
    },
  });

  initChatWidth();
  applyChatVisibility();

  try {
    const bootstrap = await api.bootstrap();
    state.projects = bootstrap.projects || [];
    state.info = bootstrap.info || null;
    applyCalendarPreferences(state.info?.preferences || state.preferences);
    renderSidebar();
    renderSettingsSubtitle();
    renderQuickSync();
    refreshQuickSync({ refreshRemote: true });
    showPendingFlash();

    const wantedCalendar = parseCalendarHash();
    if (wantedCalendar) {
      await openCalendar(wantedCalendar, { history: "replace" });
      return;
    }

    const wanted = parseProjectHash();
    const ordered = orderedProjects();
    const initial =
      ordered.find((project) => project.id === wanted) || ordered[0];
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
