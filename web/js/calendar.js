import { esc, icon, formatDateOnly } from "./ui.js";

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const GRID_DAYS = 42;
const STATUS_LABELS = { urgent: "Срочно", later: "Потом", done: "Сделано" };
const INBOX_STATE_KEY = "tasker-calendar-inbox";

const monthFormatter = new Intl.DateTimeFormat("ru-RU", {
  month: "long",
  year: "numeric",
});
const dayMonthFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
});

function pad(value) {
  return String(value).padStart(2, "0");
}

export function dateKey(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function dateFromKey(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  const date = new Date(year, month, day, 12);
  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month ||
    date.getDate() !== day
  ) {
    return null;
  }
  return date;
}

export function todayKey() {
  return dateKey(new Date());
}

function startOfGrid(year, month) {
  const first = new Date(year, month, 1, 12);
  const mondayOffset = (first.getDay() + 6) % 7;
  return new Date(year, month, 1 - mondayOffset, 12);
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, 12);
}

export function addDaysKey(value, days) {
  const date = dateFromKey(value);
  return date ? dateKey(addDays(date, days)) : null;
}

export function weekStartKey(value = new Date()) {
  const date = value instanceof Date ? new Date(value) : dateFromKey(value);
  if (!date || Number.isNaN(date.getTime())) return null;
  const offset = (date.getDay() + 6) % 7;
  return dateKey(addDays(date, -offset));
}

export function weekRange(value) {
  const start = weekStartKey(value);
  return start
    ? { start, end: addDaysKey(start, 6) }
    : { start: null, end: null };
}

export function calendarRange(year, month) {
  const start = startOfGrid(year, month);
  return { start: dateKey(start), end: dateKey(addDays(start, GRID_DAYS - 1)) };
}

export function monthParam(year, month) {
  return `${year}-${pad(month + 1)}`;
}

export function parseMonthParam(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1;
  if (year < 1970 || year > 2100 || monthIndex < 0 || monthIndex > 11) return null;
  return { year, month: monthIndex };
}

function capitalize(value) {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

function cleanDateText(value) {
  return capitalize(String(value || "").replace(/\s*г\.$/g, ""));
}

function monthTitle(year, month) {
  return cleanDateText(monthFormatter.format(new Date(year, month, 1, 12)));
}

function weekTitle(startKey, endOffset = 6) {
  const start = dateFromKey(startKey);
  if (!start) return "";
  const end = addDays(start, endOffset);
  const startMonth = start.getMonth();
  const endMonth = end.getMonth();
  const startYear = start.getFullYear();
  const endYear = end.getFullYear();

  if (startYear === endYear && startMonth === endMonth) {
    const monthName = String(dayMonthFormatter.format(start))
      .replace(/^\d+\s*/, "")
      .replace(/\s*г\.$/g, "");
    return `${start.getDate()}–${end.getDate()} ${monthName} ${startYear}`;
  }
  if (startYear === endYear) {
    return `${cleanDateText(dayMonthFormatter.format(start))} – ${cleanDateText(
      dayMonthFormatter.format(end)
    )} ${startYear}`;
  }
  return `${cleanDateText(dayMonthFormatter.format(start))} ${startYear} – ${cleanDateText(
    dayMonthFormatter.format(end)
  )} ${endYear}`;
}

export function createCalendar({ root, callbacks, preferences = {} }) {
  const monthLabel = root.querySelector("#calendar-month");
  const weekdays = root.querySelector("#calendar-weekdays");
  const grid = root.querySelector("#calendar-grid");
  const board = root.querySelector(".calendar-board");
  const inbox = root.querySelector("#calendar-inbox");
  const inboxCount = root.querySelector("#calendar-inbox-count");
  const inboxToggle = root.querySelector("#calendar-inbox-toggle");
  const loading = root.querySelector("#calendar-loading");
  const prevButton = root.querySelector("#calendar-prev");
  const nextButton = root.querySelector("#calendar-next");
  const todayButton = root.querySelector("#calendar-today");
  const modeButtons = [...root.querySelectorAll("[data-calendar-mode]")];

  let showProjectName = preferences.show_project_name !== false;
  let showCompleted = preferences.show_completed !== false;
  let showWeekends = preferences.show_weekends !== false;
  root.classList.toggle("hide-project-names", !showProjectName);

  const now = new Date();
  let mode = "month";
  let year = now.getFullYear();
  let month = now.getMonth();
  let weekStartDate = weekStartKey(now);
  let scheduled = [];
  let unscheduled = [];
  let draggingId = null;
  let dropZone = null;

  function setInboxCollapsed(collapsed, { persist = true } = {}) {
    root.classList.toggle("inbox-collapsed", collapsed);
    const label = collapsed ? "Показать задачи без даты" : "Скрыть задачи без даты";
    inboxToggle.setAttribute("aria-expanded", String(!collapsed));
    inboxToggle.setAttribute("aria-label", label);
    inboxToggle.setAttribute("title", label);
    if (persist) {
      try {
        localStorage.setItem(INBOX_STATE_KEY, collapsed ? "collapsed" : "open");
      } catch (error) {
        /* ignore */
      }
    }
  }

  try {
    setInboxCollapsed(localStorage.getItem(INBOX_STATE_KEY) === "collapsed", {
      persist: false,
    });
  } catch (error) {
    /* ignore */
  }

  inboxToggle.addEventListener("click", () => {
    setInboxCollapsed(!root.classList.contains("inbox-collapsed"));
  });

  /* -------------------------------------------------------------- */
  /* разметка задач                                                 */
  /* -------------------------------------------------------------- */
  function taskChipHTML(task, { inbox = false } = {}) {
    const done = task.status === "done";
    const status = STATUS_LABELS[task.status] || "";
    const title = `${task.project_name || "Проект"} · ${status}`;
    return `
      <div class="calendar-task${done ? " is-done" : ""}${inbox ? " is-inbox-task" : ""}"
           draggable="true" data-calendar-task="${esc(task.id)}"
           style="--task-color:${esc(task.project_color || "#4c6ef5")}"
           title="${esc(task.title)} — ${esc(title)}">
        <span class="calendar-task-dot"></span>
        <span class="calendar-task-copy">
          <span class="calendar-task-title">${esc(task.title)}</span>
          <span class="calendar-task-project">${esc(task.project_name || "")}</span>
        </span>
        ${
          inbox
            ? ""
            : `<button type="button" class="calendar-task-remove" data-unschedule="${esc(task.id)}"
                       title="Убрать из календаря" aria-label="Убрать задачу ${esc(task.title)} из календаря">
                 ${icon("x")}
               </button>`
        }
      </div>`;
  }

  function dayCellHTML(date, isOutside, isToday, tasks, { week = false } = {}) {    const key = dateKey(date);
    const classes = ["calendar-day"];
    if (isOutside) classes.push("is-outside");
    if (isToday) classes.push("is-today");
    if (week) classes.push("is-week-day");
    if ((date.getDay() + 6) % 7 >= 5) classes.push("is-weekend");
    const head = week
      ? ""
      : `<div class="calendar-day-head">
           <span class="calendar-day-number">${date.getDate()}</span>
           ${isToday ? '<span class="calendar-day-today">сегодня</span>' : ""}
         </div>`;
    return `
      <div class="${classes.join(" ")}" data-calendar-date="${key}"
           role="gridcell" aria-label="${esc(formatDateOnly(key, "full"))}: ${tasks.length}">
        ${head}
        <div class="calendar-day-tasks">
          ${tasks.map((task) => taskChipHTML(task)).join("")}
        </div>
      </div>`;
  }

  function renderWeekdays() {
    const today = todayKey();
    const days = showWeekends ? WEEKDAYS : WEEKDAYS.slice(0, 5);
    weekdays.classList.toggle("is-no-weekends", !showWeekends);
    if (mode === "week") {
      const start = dateFromKey(weekStartDate) || new Date();
      weekdays.innerHTML = days.map((day, index) => {
        const date = addDays(start, index);
        const key = dateKey(date);
        return `<div class="calendar-weekday is-week-date${index >= 5 ? " is-weekend" : ""}${
          key === today ? " is-today" : ""
        }">
          <span>${day}</span><span class="calendar-weekday-date">${date.getDate()}</span>
        </div>`;
      }).join("");
      return;
    }
    weekdays.innerHTML = days.map(
      (day, index) =>
        `<div class="calendar-weekday${index >= 5 ? " is-weekend" : ""}">${day}</div>`
    ).join("");
  }

  function visibleTasks(tasks) {
    return showCompleted
      ? tasks
      : tasks.filter((task) => task.status !== "done");
  }

  function renderInbox() {
    const previousScroll = inbox.scrollTop;
    const visibleUnscheduled = visibleTasks(unscheduled);
    const visibleScheduled = visibleTasks(scheduled);
    inboxCount.textContent = String(visibleUnscheduled.length);
    if (!visibleUnscheduled.length) {
      const hiddenDone =
        !showCompleted && [...scheduled, ...unscheduled].some((task) => task.status === "done");
      const message = visibleScheduled.length
        ? "Все задачи распределены по дням"
        : hiddenDone
          ? "Выполненные задачи скрыты"
          : "Нет задач без даты";
      inbox.innerHTML = `
        <div class="calendar-inbox-empty">
          ${icon("check")}
          <span>${message}</span>
        </div>`;
    } else {
      inbox.innerHTML = visibleUnscheduled
        .map((task) => taskChipHTML(task, { inbox: true }))
        .join("");
    }
    inbox.scrollTop = previousScroll;
  }

  function renderGrid() {
    const today = todayKey();
    const tasksByDay = new Map();
    for (const task of visibleTasks(scheduled)) {
      if (!task.scheduled_date) continue;
      if (!tasksByDay.has(task.scheduled_date)) tasksByDay.set(task.scheduled_date, []);
      tasksByDay.get(task.scheduled_date).push(task);
    }

    const isWeek = mode === "week";
    board.classList.toggle("is-week-view", isWeek);
    board.classList.toggle("is-no-weekends", !showWeekends);
    board.setAttribute("aria-label", isWeek ? "Неделя" : "Месяц");
    grid.classList.toggle("is-week", isWeek);
    grid.classList.toggle("is-no-weekends", !showWeekends);
    modeButtons.forEach((button) => {
      const active = button.dataset.calendarMode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    const cells = [];
    if (isWeek) {
      const start = dateFromKey(weekStartDate) || new Date();
      const dayCount = showWeekends ? 7 : 5;
      for (let index = 0; index < dayCount; index += 1) {
        const date = addDays(start, index);
        const key = dateKey(date);
        cells.push(
          dayCellHTML(date, false, key === today, tasksByDay.get(key) || [], {
            week: true,
          })
        );
      }
      grid.innerHTML = cells.join("");
      const title = weekTitle(weekStartDate, dayCount - 1);
      monthLabel.textContent = title;
      monthLabel.title = title;
      prevButton.setAttribute("aria-label", "Предыдущая неделя");
      prevButton.setAttribute("title", "Предыдущая неделя");
      nextButton.setAttribute("aria-label", "Следующая неделя");
      nextButton.setAttribute("title", "Следующая неделя");
    } else {
      const start = startOfGrid(year, month);
      const rows = showWeekends ? GRID_DAYS : 30;
      for (let index = 0; index < rows; index += 1) {
        const date = showWeekends
          ? addDays(start, index)
          : addDays(start, Math.floor(index / 5) * 7 + (index % 5));
        const key = dateKey(date);
        cells.push(
          dayCellHTML(
            date,
            date.getMonth() !== month,
            key === today,
            tasksByDay.get(key) || []
          )
        );
      }
      grid.innerHTML = cells.join("");
      const title = monthTitle(year, month);
      monthLabel.textContent = title;
      monthLabel.title = title;
      prevButton.setAttribute(
        "aria-label",
        `Предыдущий месяц: ${monthTitle(year, month - 1)}`
      );
      prevButton.setAttribute("title", "Предыдущий месяц");
      nextButton.setAttribute(
        "aria-label",
        `Следующий месяц: ${monthTitle(year, month + 1)}`
      );
      nextButton.setAttribute("title", "Следующий месяц");
    }
    renderWeekdays();
  }

  function render(data, options = {}) {
    if (data?.mode) mode = data.mode === "week" ? "week" : "month";
    if (mode === "week") {
      if (data?.weekStart) {
        const start = weekStartKey(data.weekStart);
        if (start) weekStartDate = start;
      }
    } else {
      if (data?.year != null) year = data.year;
      if (data?.month != null) month = data.month;
    }
    if (Array.isArray(data?.scheduled)) scheduled = data.scheduled;
    if (Array.isArray(data?.unscheduled)) unscheduled = data.unscheduled;
    const isLoading = Boolean(options.loading);
    loading.hidden = !isLoading;
    root.classList.toggle("is-loading", isLoading);
    renderGrid();
    renderInbox();
  }

  function renderLoading(period) {
    mode = period.mode === "week" ? "week" : "month";
    if (mode === "week") {
      weekStartDate = weekStartKey(period.weekStart) || weekStartDate;
    } else {
      year = period.year;
      month = period.month;
    }
    scheduled = [];
    unscheduled = [];
    render(
      { ...period, scheduled: [], unscheduled: [] },
      { loading: true }
    );
  }

  /* -------------------------------------------------------------- */
  /* перетаскивание                                                 */
  /* -------------------------------------------------------------- */
  function clearDropTargets() {
    root.querySelectorAll(".is-drop-target").forEach((element) => {
      element.classList.remove("is-drop-target");
    });
    dropZone = null;
  }

  function cleanupDrag() {
    root.classList.remove("is-dragging");
    root.querySelectorAll(".calendar-task.is-dragging").forEach((element) => {
      element.classList.remove("is-dragging");
    });
    clearDropTargets();
    draggingId = null;
  }

  function dropTarget(event) {
    const inboxElement = event.target.closest("#calendar-inbox");
    if (inboxElement) return { type: "inbox", element: inboxElement };
    const day = event.target.closest("[data-calendar-date]");
    if (day) return { type: "day", date: day.dataset.calendarDate, element: day };
    return null;
  }

  root.addEventListener("dragstart", (event) => {
    const task = event.target.closest("[data-calendar-task]");
    if (!task || event.target.closest(".calendar-task-remove")) {
      if (event.target.closest(".calendar-task-remove")) event.preventDefault();
      return;
    }
    draggingId = task.dataset.calendarTask;
    task.classList.add("is-dragging");
    root.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggingId);
  });

  root.addEventListener("dragover", (event) => {
    if (!draggingId) return;
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (dropZone !== target.element) {
      clearDropTargets();
      target.element.classList.add("is-drop-target");
      dropZone = target.element;
    }
  });

  root.addEventListener("dragleave", (event) => {
    if (!draggingId) return;
    if (event.relatedTarget && root.contains(event.relatedTarget)) return;
    clearDropTargets();
  });

  root.addEventListener("drop", (event) => {
    if (!draggingId) return;
    const target = dropTarget(event);
    if (!target) {
      cleanupDrag();
      return;
    }
    event.preventDefault();
    const taskId = draggingId;
    const date = target.type === "day" ? target.date : null;
    cleanupDrag();
    callbacks.onSchedule(taskId, date);
  });

  root.addEventListener("dragend", () => {
    cleanupDrag();
  });

  root.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-unschedule]");
    if (!remove) return;
    event.preventDefault();
    event.stopPropagation();
    callbacks.onSchedule(remove.dataset.unschedule, null);
  });

  root.addEventListener("dblclick", (event) => {
    if (event.target.closest("button, a, input, textarea, select")) return;
    const task = event.target.closest("[data-calendar-task]");
    if (task) {
      event.preventDefault();
      callbacks.onEditTask(task.dataset.calendarTask);
      return;
    }
    const day = event.target.closest("[data-calendar-date]");
    if (day) {
      event.preventDefault();
      callbacks.onCreateTask(day.dataset.calendarDate);
      return;
    }
    if (event.target.closest("#calendar-inbox")) {
      event.preventDefault();
      callbacks.onCreateTask(null);
    }
  });

  /* -------------------------------------------------------------- */
  /* навигация и вид                                                */
  /* -------------------------------------------------------------- */
  function setPreferences(next = {}) {
    if ("show_project_name" in next) {
      showProjectName = next.show_project_name !== false;
    }
    if ("show_completed" in next) {
      showCompleted = next.show_completed !== false;
    }
    if ("show_weekends" in next) {
      showWeekends = next.show_weekends !== false;
    }
    root.classList.toggle("hide-project-names", !showProjectName);
    renderGrid();
    renderInbox();
  }

  prevButton.addEventListener("click", () => callbacks.onNavigate(-1));
  nextButton.addEventListener("click", () => callbacks.onNavigate(1));
  todayButton.addEventListener("click", () => callbacks.onToday());
  modeButtons.forEach((button) => {
    button.addEventListener("click", () => callbacks.onModeChange(button.dataset.calendarMode));
  });

  return { render, renderLoading, setPreferences };
}
