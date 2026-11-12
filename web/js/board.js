import {
  esc,
  icon,
  formatCardDate,
  formatFullDate,
  formatDateOnly,
  openModal,
  confirmDialog,
  toast,
} from "./ui.js";
import { taskDurationDays } from "./calendar.js";

const COLUMNS = [
  { id: "urgent", title: "Срочно", hint: "Сделать как можно скорее" },
  { id: "later", title: "Потом", hint: "Можно отложить на время" },
  { id: "done", title: "Сделано", hint: "Готовые задачи" },
];

const COLUMN_EMPTY = {
  urgent: "Пока пусто. Добавьте срочную задачу или перетащите карточку сюда.",
  later: "Здесь задачи, которые можно отложить.",
  done: "Выполненные задачи будут собираться здесь.",
};

const STATUS_BY_ID = Object.fromEntries(COLUMNS.map((column) => [column.id, column]));

function byPosition(a, b) {
  return (
    (a.position ?? 0) - (b.position ?? 0) ||
    String(a.created_at).localeCompare(String(b.created_at))
  );
}

export function createBoard({ container, callbacks }) {
  let tasks = [];
  let project = null;
  let composerStatus = null;
  let draggingId = null;
  let dragCard = null;
  let currentDropList = null;

  /* -------------------------------------------------------------- */
  /* разметка                                                       */
  /* -------------------------------------------------------------- */
  function cardHTML(task) {
    const done = task.status === "done";
    const dateIso = done ? task.done_at || task.updated_at : task.updated_at;
    const title = done ? "Вернуть в работу" : "Отметить выполненной";
    return `
      <article class="task-card${done ? " is-done" : ""}" data-task-id="${esc(task.id)}"
               draggable="true" tabindex="0" aria-label="${esc(task.title)}">
        <div class="task-card-top">
          <button type="button" class="task-check${done ? " is-checked" : ""}"
                  data-check-task="${esc(task.id)}" title="${title}" aria-label="${title}">
            ${icon("check")}
          </button>
          <h3 class="task-title">${esc(task.title)}</h3>
        </div>
        ${task.description ? `<p class="task-description">${esc(task.description)}</p>` : ""}
        <footer class="task-card-foot">
          <time class="task-date" datetime="${esc(dateIso || task.created_at)}"
                title="${esc(formatFullDate(dateIso || task.created_at))}">
            ${done ? "готово " : ""}${esc(formatCardDate(dateIso))}
          </time>
          ${
            task.scheduled_date
              ? (() => {
                  const days = taskDurationDays(task);
                  const range = task.scheduled_end_date
                    ? `${formatDateOnly(task.scheduled_date)}–${formatDateOnly(task.scheduled_end_date)}`
                    : formatDateOnly(task.scheduled_date);
                  return `<span class="task-scheduled" title="В календаре: ${esc(
                    formatDateOnly(task.scheduled_date, "full")
                  )}${days > 1 ? ` — ${days} дн.` : ""}">
                   ${icon("calendar")} ${esc(range)}${days > 1 ? ` · ${days} дн.` : ""}
                 </span>`;
                })()
              : ""
          }
        </footer>
      </article>`;
  }

  function columnHTML(column, items) {
    return `
      <section class="column" data-status="${column.id}" aria-label="${column.title}">
        <header class="column-head">
          <div class="column-heading">
            <span class="status-dot status-${column.id}"></span>
            <h2 class="column-title">${column.title}</h2>
            <span class="column-count">${items.length}</span>
          </div>
          <button type="button" class="icon-btn column-add" data-add-task="${column.id}"
                  title="Добавить задачу" aria-label="Добавить задачу в колонку «${column.title}»">
            ${icon("plus")}
          </button>
        </header>
        <div class="column-body">
          <form class="task-composer" data-composer="${column.id}" hidden>
            <textarea class="task-composer-input" rows="2" maxlength="300"
                      placeholder="Что нужно сделать?" aria-label="Название задачи"></textarea>
            <div class="task-composer-foot">
              <span class="task-composer-hint">Enter — добавить · Esc — закрыть</span>
              <button type="submit" class="btn btn-primary btn-xs">Добавить</button>
            </div>
          </form>
          <div class="column-list" data-status="${column.id}">
            ${
              items.length
                ? items.map(cardHTML).join("")
                : `<div class="column-empty">${COLUMN_EMPTY[column.id]}</div>`
            }
          </div>
        </div>
      </section>`;
  }

  /* -------------------------------------------------------------- */
  /* рендер                                                         */
  /* -------------------------------------------------------------- */
  function render(nextTasks, nextProject) {
    const openForm = container.querySelector(".task-composer:not([hidden])");
    const draft = openForm
      ? {
          status: openForm.dataset.composer,
          value: openForm.querySelector("textarea").value,
          focused: openForm.contains(document.activeElement),
        }
      : null;

    cleanupDrag();
    tasks = Array.isArray(nextTasks) ? nextTasks.slice() : [];
    project = nextProject || null;

    const grouped = { urgent: [], later: [], done: [] };
    for (const task of tasks) {
      if (grouped[task.status]) grouped[task.status].push(task);
    }
    for (const column of COLUMNS) grouped[column.id].sort(byPosition);

    container.innerHTML = COLUMNS.map((column) => columnHTML(column, grouped[column.id])).join("");

    if (composerStatus) {
      openComposer(composerStatus, {
        value: draft && draft.status === composerStatus ? draft.value : "",
        focus: Boolean(draft && draft.focused),
        silent: true,
      });
    }
  }

  /* -------------------------------------------------------------- */
  /* работа с задачей в модальном окне                              */
  /* -------------------------------------------------------------- */
  function taskModal(task = null, defaults = {}) {
    const isNew = !task;
    const status = task?.status || defaults.status || "urgent";
    const formId = "task-editor-form";
    const body = `
      <form class="task-form" id="${formId}" novalidate>
        <label class="field">
          <span class="field-label">Название</span>
          <input class="input" name="title" type="text" maxlength="300" autocomplete="off"
                 placeholder="Например, подготовить отчёт" value="${esc(task?.title || "")}">
        </label>
        <label class="field">
          <span class="field-label">Описание <span class="field-hint">необязательно</span></span>
          <textarea class="textarea" name="description" rows="5" maxlength="20000"
                    placeholder="Детали, ссылки, критерии готовности…">${esc(task?.description || "")}</textarea>
        </label>
        <div class="field">
          <span class="field-label">Даты в календаре <span class="field-hint">необязательно</span></span>
          <div class="date-range-row">
            <label class="date-range-field">
              <span>Начало</span>
              <input class="input" id="task-scheduled-date" type="date" name="scheduled_date"
                     value="${esc(task?.scheduled_date || "")}">
            </label>
            <span class="date-range-dash" aria-hidden="true">—</span>
            <label class="date-range-field">
              <span>Конец</span>
              <input class="input" id="task-scheduled-end-date" type="date" name="scheduled_end_date"
                     value="${esc(task?.scheduled_end_date || "")}">
            </label>
          </div>
          <div class="date-range-row date-range-actions">
            <span class="date-range-hint" id="task-duration">Один день</span>
            <button type="button" class="btn btn-quiet btn-sm" data-act="clear-date">Убрать из календаря</button>
          </div>
        </div>
        <fieldset class="field field-inline">
          <legend class="field-label">Колонка</legend>
          <div class="segmented">
            ${COLUMNS.map(
              (column) => `
              <label class="segment">
                <input type="radio" name="status" value="${column.id}" ${column.id === status ? "checked" : ""}>
                <span class="segment-label">
                  <span class="status-dot status-${column.id}"></span>${column.title}
                </span>
              </label>`
            ).join("")}
          </div>
        </fieldset>
        ${
          task
            ? `<p class="form-note">Создана ${esc(formatFullDate(task.created_at))}${
                task.updated_at !== task.created_at
                  ? ` · изменена ${esc(formatFullDate(task.updated_at))}`
                  : ""
              }</p>`
            : ""
        }
      </form>`;

    const footer = `
      ${
        task
          ? `<button type="button" class="btn btn-danger-quiet" data-act="delete">${icon("trash")}<span>Удалить</span></button>`
          : ""
      }
      <span class="modal-footer-spacer"></span>
      <button type="button" class="btn btn-quiet" data-act="cancel">Отмена</button>
      <button type="submit" form="${formId}" class="btn btn-primary" data-act="save">Сохранить</button>`;

    openModal({
      title: isNew ? "Новая задача" : "Задача",
      subtitle: project ? project.name : "",
      className: "modal-task",
      body,
      footer,
      onMount({ overlay, close }) {
        const form = overlay.querySelector(`#${formId}`);
        const titleInput = form.querySelector('[name="title"]');
        const dateInput = form.querySelector('[name="scheduled_date"]');
        const endDateInput = form.querySelector('[name="scheduled_end_date"]');
        const durationLabel = overlay.querySelector("#task-duration");
        const saveButton = overlay.querySelector('[data-act="save"]');
        const deleteButton = overlay.querySelector('[data-act="delete"]');
        let busy = false;

        const setBusy = (value) => {
          busy = value;
          saveButton.disabled = value;
          saveButton.textContent = value ? "Сохранение…" : "Сохранить";
        };

        const updateDuration = () => {
          endDateInput.min = dateInput.value || "";
          const days = dateInput.value
            ? taskDurationDays({
                scheduled_date: dateInput.value,
                scheduled_end_date: endDateInput.value || null,
              })
            : 0;
          const invalid =
            Boolean(dateInput.value) &&
            Boolean(endDateInput.value) &&
            endDateInput.value < dateInput.value;
          durationLabel.textContent = invalid
            ? "Конец раньше начала"
            : !dateInput.value
              ? "Без даты"
              : days === 1
                ? "Один день"
                : `${days} ${days < 5 ? "дня" : "дней"}`;
          durationLabel.classList.toggle("is-invalid", invalid);
        };

        titleInput.addEventListener("input", () => titleInput.classList.remove("is-invalid"));
        dateInput.addEventListener("change", () => {
          if (!dateInput.value) {
            endDateInput.value = "";
          } else if (endDateInput.value && endDateInput.value < dateInput.value) {
            endDateInput.value = "";
          }
          updateDuration();
        });
        endDateInput.addEventListener("change", updateDuration);
        overlay.querySelector('[data-act="clear-date"]').addEventListener("click", () => {
          dateInput.value = "";
          endDateInput.value = "";
          updateDuration();
          dateInput.focus();
        });
        updateDuration();

        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (busy) return;
          const title = titleInput.value.trim();
          if (!title) {
            titleInput.classList.add("is-invalid");
            titleInput.focus();
            toast("Введите название задачи", "error");
            return;
          }
          const description = form.querySelector('[name="description"]').value;
          const selected = form.querySelector('input[name="status"]:checked');
          const startDate = dateInput.value || null;
          let endDate = endDateInput.value || null;
          if (!startDate) endDate = null;
          if (startDate && endDate && endDate < startDate) {
            toast("Дата окончания не может быть раньше даты начала", "error");
            endDateInput.focus();
            return;
          }
          if (endDate === startDate) endDate = null;
          setBusy(true);
          try {
            await callbacks.saveTask({
              id: task?.id,
              title,
              description,
              status: selected ? selected.value : status,
              scheduled_date: startDate,
              scheduled_end_date: endDate,
            });
            close();
          } catch (error) {
            toast(error?.message || "Не удалось сохранить задачу", "error");
            setBusy(false);
          }
        });

        form.addEventListener("keydown", (event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            form.requestSubmit();
          }
        });

        overlay.querySelector('[data-act="cancel"]').addEventListener("click", close);

        if (deleteButton) {
          deleteButton.addEventListener("click", async () => {
            if (busy) return;
            const confirmed = await confirmDialog({
              title: "Удалить задачу?",
              message: `«${task.title}» будет удалена без возможности восстановления.`,
              confirmLabel: "Удалить",
            });
            if (!confirmed) return;
            setBusy(true);
            try {
              await callbacks.deleteTask(task.id);
              close();
            } catch (error) {
              toast(error?.message || "Не удалось удалить задачу", "error");
              setBusy(false);
            }
          });
        }

        requestAnimationFrame(() => titleInput.focus());
        const length = titleInput.value.length;
        try {
          titleInput.setSelectionRange(length, length);
        } catch (error) {
          /* ignore */
        }
      },
    });
  }

  /* -------------------------------------------------------------- */
  /* быстрый ввод в колонке                                         */
  /* -------------------------------------------------------------- */
  function openComposer(status, { value = null, focus = true, silent = false } = {}) {
    composerStatus = status;
    container.querySelectorAll(".task-composer").forEach((form) => {
      const target = form.dataset.composer === status;
      form.hidden = !target;
      if (!target) return;
      form.classList.remove("is-busy");
      const textarea = form.querySelector("textarea");
      const button = form.querySelector('button[type="submit"]');
      button.disabled = false;
      if (value !== null) textarea.value = value;
      if (focus) {
        requestAnimationFrame(() => {
          if (!form.isConnected) return;
          textarea.focus();
          autoGrowComposer(textarea);
          textarea.setSelectionRange(textarea.value.length, textarea.value.length);
        });
      } else {
        autoGrowComposer(textarea);
      }
    });
    if (!silent) {
      const form = container.querySelector(`.task-composer[data-composer="${status}"]`);
      if (form) form.scrollIntoView({ block: "nearest" });
    }
  }

  function closeComposer() {
    composerStatus = null;
    container.querySelectorAll(".task-composer").forEach((form) => {
      form.hidden = true;
      form.querySelector("textarea").value = "";
    });
  }

  function autoGrowComposer(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`;
  }

  async function submitComposer(form) {
    if (form.classList.contains("is-busy")) return;
    const status = form.dataset.composer;
    const textarea = form.querySelector("textarea");
    const title = textarea.value.trim();
    if (!title) {
      textarea.focus();
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    form.classList.add("is-busy");
    try {
      await callbacks.createTask({ title, status });
      if (composerStatus === status) {
        openComposer(status, { value: "", focus: true, silent: true });
      }
    } catch (error) {
      toast(error?.message || "Не удалось добавить задачу", "error");
      if (form.isConnected) {
        button.disabled = false;
        form.classList.remove("is-busy");
        textarea.focus();
      }
    }
  }

  /* -------------------------------------------------------------- */
  /* drag & drop                                                    */
  /* -------------------------------------------------------------- */
  function cleanupDrag() {
    if (dragCard) dragCard.classList.remove("is-dragging");
    container.classList.remove("is-dragging");
    container.querySelectorAll(".is-drop-target").forEach((element) => {
      element.classList.remove("is-drop-target");
    });
    clearDropIndicator();
    draggingId = null;
    dragCard = null;
    currentDropList = null;
  }

  function clearDropIndicator() {
    container.querySelectorAll(".drop-before, .drop-after").forEach((element) => {
      element.classList.remove("drop-before", "drop-after");
    });
  }

  function showDropIndicator(list, index) {
    clearDropIndicator();
    const cards = [...list.querySelectorAll(".task-card")].filter(
      (card) => card.dataset.taskId !== draggingId
    );
    if (!cards.length) return;
    if (index < cards.length) cards[index].classList.add("drop-before");
    else cards[cards.length - 1].classList.add("drop-after");
  }

  function insertionIndex(list, clientY) {
    const cards = [...list.querySelectorAll(".task-card")].filter(
      (card) => card.dataset.taskId !== draggingId
    );
    for (let index = 0; index < cards.length; index += 1) {
      const rect = cards[index].getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return index;
    }
    return cards.length;
  }

  container.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".task-card");
    if (!card) return;
    if (event.target.closest("button, a, input, textarea")) {
      event.preventDefault();
      return;
    }
    const task = tasks.find((item) => item.id === card.dataset.taskId);
    if (!task) return;
    draggingId = task.id;
    dragCard = card;
    card.classList.add("is-dragging");
    container.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", task.id);
    try {
      event.dataTransfer.setDragImage(card, 24, 24);
    } catch (error) {
      /* ignore */
    }
  });

  container.addEventListener("dragover", (event) => {
    if (!draggingId) return;
    const list = event.target.closest(".column-list");
    if (!list) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";

    const column = list.closest(".column");
    if (currentDropList !== list) {
      container.querySelectorAll(".is-drop-target").forEach((element) => {
        element.classList.remove("is-drop-target");
      });
      if (column) column.classList.add("is-drop-target");
      currentDropList = list;
    }

    showDropIndicator(list, insertionIndex(list, event.clientY));
  });

  container.addEventListener("dragleave", (event) => {
    if (!draggingId) return;
    if (event.relatedTarget && container.contains(event.relatedTarget)) return;
    container.querySelectorAll(".is-drop-target").forEach((element) => {
      element.classList.remove("is-drop-target");
    });
    clearDropIndicator();
    currentDropList = null;
  });

  container.addEventListener("drop", (event) => {
    if (!draggingId) return;
    const list = event.target.closest(".column-list");
    if (!list) {
      cleanupDrag();
      return;
    }
    event.preventDefault();
    const taskId = draggingId;
    const status = list.dataset.status;
    const index = insertionIndex(list, event.clientY);
    cleanupDrag();
    if (STATUS_BY_ID[status]) {
      callbacks.moveTask(taskId, status, index);
    }
  });

  container.addEventListener("dragend", () => {
    cleanupDrag();
  });

  /* -------------------------------------------------------------- */
  /* события                                                        */
  /* -------------------------------------------------------------- */
  container.addEventListener("click", (event) => {
    const addButton = event.target.closest("[data-add-task]");
    if (addButton) {
      const status = addButton.dataset.addTask;
      if (composerStatus === status) closeComposer();
      else openComposer(status, { focus: true });
      return;
    }

    const checkButton = event.target.closest("[data-check-task]");
    if (checkButton) {
      event.stopPropagation();
      const task = tasks.find((item) => item.id === checkButton.dataset.checkTask);
      if (task) callbacks.quickToggle(task);
      return;
    }
  });

  container.addEventListener("dblclick", (event) => {
    if (event.target.closest("button, a, input, textarea, .task-composer")) return;
    const card = event.target.closest(".task-card");
    if (!card) return;
    const task = tasks.find((item) => item.id === card.dataset.taskId);
    if (task) taskModal(task);
  });

  container.addEventListener("keydown", (event) => {
    const card = event.target.closest(".task-card");
    if (card && event.target === card && event.key === "Enter") {
      event.preventDefault();
      const task = tasks.find((item) => item.id === card.dataset.taskId);
      if (task) taskModal(task);
      return;
    }
    const form = event.target.closest(".task-composer");
    if (!form) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeComposer();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  container.addEventListener("input", (event) => {
    if (event.target.classList.contains("task-composer-input")) {
      autoGrowComposer(event.target);
    }
  });

  container.addEventListener("submit", (event) => {
    const form = event.target.closest(".task-composer");
    if (!form) return;
    event.preventDefault();
    submitComposer(form);
  });

  return {
    render,
    openNewTask(status = "urgent") {
      taskModal(null, { status });
    },
    openTask(task) {
      taskModal(task);
    },
  };
}
