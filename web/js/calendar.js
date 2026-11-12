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

export function taskDurationDays(task) {
  const start = dateFromKey(task?.scheduled_date);
  if (!start) return 1;
  const end = dateFromKey(task?.scheduled_end_date) || start;
  if (end < start) return 1;
  let days = 1;
  let cursor = start;
  while (cursor < end && days <= 367) {
    cursor = addDays(cursor, 1);
    days += 1;
  }
  return days;
}

export function taskEndKey(task) {
  if (!task?.scheduled_date) return null;
  return task.scheduled_end_date && task.scheduled_end_date >= task.scheduled_date
    ? task.scheduled_end_date
    : task.scheduled_date;
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
  let draggingMode = null;
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
  function inboxTaskHTML(task) {
    const done = task.status === "done";
    const status = STATUS_LABELS[task.status] || "";
    return `
      <div class="calendar-task is-inbox-task${done ? " is-done" : ""}"
           draggable="true" data-calendar-task="${esc(task.id)}"
           style="--task-color:${esc(task.project_color || "#4c6ef5")}"
           title="${esc(task.title)} — ${esc(`${task.project_name || "Проект"} · ${status}`)}">
        <span class="calendar-task-dot"></span>
        <span class="calendar-task-copy">
          <span class="calendar-task-title">${esc(task.title)}</span>
          <span class="calendar-task-project">${esc(task.project_name || "")}</span>
        </span>
      </div>`;
  }

  function taskBarHTML(
    task,
    { startCol, endCol, track, orderIndex, orderCount, weekStart, weekEnd }
  ) {
    const canUp = orderIndex > 0;
    const canDown = orderIndex < orderCount - 1;
    const done = task.status === "done";
    const days = taskDurationDays(task);
    const multi = days > 1;
    const trueEnd = taskEndKey(task);
    const classes = ["calendar-task"];
    if (multi) classes.push("is-span");
    if (done) classes.push("is-done");
    if (task.scheduled_date < weekStart) classes.push("is-continued-start");
    if (trueEnd > weekEnd) classes.push("is-continued-end");
    const canResize = trueEnd <= weekEnd;
    const project = [task.project_name || "", multi ? `${days} дн.` : ""]
      .filter(Boolean)
      .join(" · ");
    return `
      <div class="${classes.join(" ")}"
           draggable="true" data-calendar-task="${esc(task.id)}"
           style="--task-color:${esc(task.project_color || "#4c6ef5")};
                  grid-column:${startCol} / ${endCol + 1};
                  grid-row:${track + 2}"
           title="${esc(task.title)} — ${esc(`${task.project_name || "Проект"}${multi ? ` · ${days} дн.` : ""}`)}">
        <span class="calendar-task-order" role="group" aria-label="Порядок задачи ${esc(task.title)}">
          <button type="button" data-calendar-order="up" ${canUp ? "" : "disabled"}
                  title="Поднять выше" aria-label="Поднять задачу выше">↑</button>
          <button type="button" data-calendar-order="down" ${canDown ? "" : "disabled"}
                  title="Опустить ниже" aria-label="Опустить задачу ниже">↓</button>
          <button type="button" class="calendar-task-order-remove" data-unschedule="${esc(task.id)}"
                  title="Убрать из календаря" aria-label="Убрать задачу ${esc(task.title)} из календаря">×</button>
        </span>
        <span class="calendar-task-grip" draggable="true" data-order-handle="${esc(task.id)}"
              title="Перетащите вверх или вниз, чтобы изменить порядок"
              aria-label="Изменить порядок задачи ${esc(task.title)}">⋮⋮</span>
        <span class="calendar-task-dot"></span>
        <span class="calendar-task-copy">
          <span class="calendar-task-title">${esc(task.title)}</span>
          <span class="calendar-task-project">${esc(project)}</span>
        </span>
        ${
          canResize
            ? `<span class="calendar-task-resize" draggable="true" data-resize-task="${esc(task.id)}"
                     title="Потяните вправо, чтобы растянуть задачу на другие дни"
                     aria-label="Растянуть задачу ${esc(task.title)} на другие дни"></span>`
            : ""
        }
      </div>`;
  }

  function dayCellHTML(date, isOutside, isToday, { column, last = false, week = false } = {}) {
    const key = dateKey(date);
    const classes = ["calendar-day"];
    if (isOutside) classes.push("is-outside");
    if (isToday) classes.push("is-today");
    if (week) classes.push("is-week-day");
    if (last) classes.push("is-last-column");
    if ((date.getDay() + 6) % 7 >= 5) classes.push("is-weekend");
    return `
      <div class="${classes.join(" ")}" data-calendar-date="${key}"
           style="grid-column:${column};grid-row:1 / -1"
           role="gridcell" aria-label="${esc(formatDateOnly(key, "full"))}">
        <div class="calendar-day-head">
          <span class="calendar-day-number">${date.getDate()}</span>
          ${isToday ? '<span class="calendar-day-today">сегодня</span>' : ""}
        </div>
      </div>`;
  }

  function weekHTML(start, dayCount, ordered, tracks, { week = false } = {}) {
    const dates = Array.from({ length: dayCount }, (_, index) => addDays(start, index));
    const weekStart = dateKey(dates[0]);
    const weekEnd = dateKey(dates[dates.length - 1]);
    const cells = dates
      .map((date, index) =>
        dayCellHTML(date, week ? false : date.getMonth() !== month, dateKey(date) === todayKey(), {
          column: index + 1,
          last: index === dates.length - 1,
          week,
        })
      )
      .join("");
    const bars = [];
    let maxTrack = 0;
    for (const task of ordered) {
      const first = dateFromKey(task.scheduled_date);
      const last = dateFromKey(taskEndKey(task)) || first;
      if (!first || !last) continue;
      const visible = dates
        .map((date, index) => (date >= first && date <= last ? index : -1))
        .filter((index) => index >= 0);
      if (!visible.length) continue;
      const track = tracks.get(task.id);
      if (track == null) continue;
      maxTrack = Math.max(maxTrack, track + 1);
      bars.push(
        taskBarHTML(task, {
          startCol: visible[0] + 1,
          endCol: visible[visible.length - 1] + 1,
          track,
          orderIndex: ordered.indexOf(task),
          orderCount: ordered.length,
          weekStart,
          weekEnd,
        })
      );
    }
    return `
      <div class="calendar-week${dayCount === 5 ? " is-no-weekends" : ""}"
           data-week-start="${weekStart}" data-day-count="${dayCount}"
           style="--calendar-tracks:${Math.max(maxTrack, 1)}">
        ${cells}
        ${bars.join("")}
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

  function packTaskRows(ordered) {
    const rows = [];
    const rowById = new Map();
    for (const task of ordered) {
      const start = dateFromKey(task.scheduled_date);
      const end = dateFromKey(taskEndKey(task)) || start;
      if (!start || !end) continue;
      let row = 0;
      while (
        rows[row]?.some(
          (interval) => start <= interval.end && end >= interval.start
        )
      ) {
        row += 1;
      }
      if (!rows[row]) rows[row] = [];
      rows[row].push({ start, end });
      rowById.set(task.id, row);
    }
    return rowById;
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
        .map((task) => inboxTaskHTML(task))
        .join("");
    }
    inbox.scrollTop = previousScroll;
  }

  function renderGrid() {
    const isWeek = mode === "week";
    const ordered = [...visibleTasks(scheduled)].sort(
      (a, b) =>
        (Number.isFinite(Number(a.calendar_order))
          ? Number(a.calendar_order)
          : Number.MAX_SAFE_INTEGER) -
          (Number.isFinite(Number(b.calendar_order))
            ? Number(b.calendar_order)
            : Number.MAX_SAFE_INTEGER) ||
        String(a.scheduled_date || "").localeCompare(String(b.scheduled_date || "")) ||
        (a.position ?? 0) - (b.position ?? 0)
    );
    const tracks = packTaskRows(ordered);

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

    if (isWeek) {
      const start = dateFromKey(weekStartDate) || new Date();
      const dayCount = showWeekends ? 7 : 5;
      grid.innerHTML = weekHTML(start, dayCount, ordered, tracks, { week: true });
      const title = weekTitle(weekStartDate, dayCount - 1);
      monthLabel.textContent = title;
      monthLabel.title = title;
      prevButton.setAttribute("aria-label", "Предыдущая неделя");
      prevButton.setAttribute("title", "Предыдущая неделя");
      nextButton.setAttribute("aria-label", "Следующая неделя");
      nextButton.setAttribute("title", "Следующая неделя");
    } else {
      const start = startOfGrid(year, month);
      const dayCount = showWeekends ? 7 : 5;
      const weeks = [];
      for (let row = 0; row < 6; row += 1) {
        weeks.push(weekHTML(addDays(start, row * 7), dayCount, ordered, tracks));
      }
      grid.innerHTML = weeks.join("");
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
    root.querySelectorAll(".is-drop-target, .is-order-target").forEach((element) => {
      element.classList.remove("is-drop-target", "is-order-target");
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
    draggingMode = null;
  }

  function dropTarget(event) {
    const inboxElement = event.target.closest("#calendar-inbox");
    if (inboxElement) return { type: "inbox", element: inboxElement };
    const week = event.target.closest(".calendar-week");
    if (week) {
      const rect = week.getBoundingClientRect();
      const count = Number(week.dataset.dayCount) || 7;
      const ratio = rect.width
        ? Math.max(0, Math.min(0.999, (event.clientX - rect.left) / rect.width))
        : 0;
      const date = addDaysKey(week.dataset.weekStart, Math.floor(ratio * count));
      const day = week.querySelector(`[data-calendar-date="${date}"]`);
      return { type: "day", date, element: day || week };
    }
    return null;
  }

  root.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-order-handle]");
    if (handle) {
      draggingId = handle.dataset.orderHandle;
      draggingMode = "order";
      root.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggingId);
      return;
    }
    const resize = event.target.closest("[data-resize-task]");
    if (resize) {
      draggingId = resize.dataset.resizeTask;
      draggingMode = "resize";
      root.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggingId);
      return;
    }
    const task = event.target.closest("[data-calendar-task]");
    if (
      !task ||
      event.target.closest(".calendar-task-remove") ||
      event.target.closest("[data-unschedule]") ||
      event.target.closest("[data-calendar-order]")
    ) {
      event.preventDefault();
      return;
    }
    draggingId = task.dataset.calendarTask;
    draggingMode = "move";
    task.classList.add("is-dragging");
    root.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggingId);
  });

  root.addEventListener("dragover", (event) => {
    if (!draggingId) return;
    if (draggingMode === "order") {
      const target = event.target.closest("[data-calendar-task]");
      if (!target || target.dataset.calendarTask === draggingId) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      if (dropZone !== target) {
        clearDropTargets();
        target.classList.add("is-order-target");
        dropZone = target;
      }
      return;
    }
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
    const taskId = draggingId;
    const mode = draggingMode;
    if (mode === "order") {
      const target = event.target.closest("[data-calendar-task]");
      if (!target || target.dataset.calendarTask === taskId) {
        cleanupDrag();
        return;
      }
      event.preventDefault();
      const rect = target.getBoundingClientRect();
      const before = event.clientY < rect.top + rect.height / 2;
      const targetId = target.dataset.calendarTask;
      cleanupDrag();
      callbacks.onReorder?.(taskId, { targetId, before });
      return;
    }
    const target = dropTarget(event);
    if (!target) {
      cleanupDrag();
      return;
    }
    event.preventDefault();
    const date = target.type === "day" ? target.date : null;
    cleanupDrag();
    if (mode === "resize") {
      if (date) callbacks.onResize?.(taskId, date);
    } else {
      callbacks.onSchedule(taskId, date);
    }
  });

  root.addEventListener("dragend", () => {
    cleanupDrag();
  });

  root.addEventListener("click", (event) => {
    if (event.target.closest("[data-resize-task]")) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (event.target.closest("[data-order-handle]")) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const order = event.target.closest("[data-calendar-order]");
    if (order) {
      event.preventDefault();
      event.stopPropagation();
      if (!order.disabled) {
        const task = order.closest("[data-calendar-task]");
        if (task) callbacks.onReorder?.(task.dataset.calendarTask, order.dataset.calendarOrder);
      }
      return;
    }
    const remove = event.target.closest("[data-unschedule]");
    if (!remove) return;
    event.preventDefault();
    event.stopPropagation();
    callbacks.onSchedule(remove.dataset.unschedule, null);
  });

  root.addEventListener("dblclick", (event) => {
    if (
      event.target.closest(
        "button, a, input, textarea, select, [data-resize-task], [data-order-handle]"
      )
    )
      return;
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
