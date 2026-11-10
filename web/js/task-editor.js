import {
  esc,
  icon,
  formatFullDate,
  openModal,
  confirmDialog,
  toast,
} from "./ui.js";

const STATUSES = [
  { id: "urgent", title: "Срочно" },
  { id: "later", title: "Потом" },
  { id: "done", title: "Сделано" },
];

export function openTaskEditor({
  task = null,
  projects = [],
  defaultDate = null,
  defaultProjectId = null,
  projectLabel = "",
  onSave,
  onDelete,
}) {
  const isNew = !task;
  if (isNew && !projects.length) {
    toast("Сначала создайте проект", "error");
    return;
  }

  const projectId = task?.project_id || defaultProjectId || projects[0]?.id;
  const selectedProject =
    projects.find((project) => project.id === projectId) ||
    projects.find((project) => project.id === task?.project_id) ||
    null;
  const status = task?.status || "urgent";
  const formId = "calendar-task-editor";
  const dateValue = task?.scheduled_date || defaultDate || "";
  const projectName = task?.project_name || selectedProject?.name || projectLabel;
  const projectColor =
    task?.project_color || selectedProject?.color || "#4c6ef5";

  const body = `
    <form class="task-form" id="${formId}" novalidate>
      ${
        isNew
          ? `<label class="field">
               <span class="field-label">Проект</span>
               <select class="input select-input" name="project_id">
                 ${projects
                   .map(
                     (project) =>
                       `<option value="${esc(project.id)}" ${project.id === projectId ? "selected" : ""}>${esc(project.name)}</option>`
                   )
                   .join("")}
               </select>
             </label>`
          : `<div class="field">
               <span class="field-label">Проект</span>
               <div class="task-project-static">
                 <span class="calendar-task-dot" style="--task-color:${esc(projectColor)}"></span>
                 <span>${esc(projectName || "Проект")}</span>
               </div>
             </div>`
      }
      <label class="field">
        <span class="field-label">Название</span>
        <input class="input" name="title" type="text" maxlength="300" autocomplete="off"
               placeholder="Например, подготовить отчёт" value="${esc(task?.title || "")}" autofocus>
      </label>
      <label class="field">
        <span class="field-label">Описание <span class="field-hint">необязательно</span></span>
        <textarea class="textarea" name="description" rows="4" maxlength="20000"
                  placeholder="Детали, ссылки, критерии готовности…">${esc(task?.description || "")}</textarea>
      </label>
      <div class="field">
        <span class="field-label">Колонка</span>
        <div class="segmented">
          ${STATUSES.map(
            (item) => `
            <label class="segment">
              <input type="radio" name="status" value="${item.id}" ${item.id === status ? "checked" : ""}>
              <span class="segment-label">
                <span class="status-dot status-${item.id}"></span>${item.title}
              </span>
            </label>`
          ).join("")}
        </div>
      </div>
      <div class="field">
        <label class="field-label" for="calendar-task-date">Дата в календаре <span class="field-hint">необязательно</span></label>
        <div class="date-input-row">
          <input class="input" id="calendar-task-date" type="date" name="scheduled_date"
                 value="${esc(dateValue)}">
          <button type="button" class="btn btn-quiet btn-sm" data-act="clear-date">Убрать</button>
        </div>
      </div>
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
    subtitle: "Календарь",
    className: "modal-task modal-calendar-task",
    body,
    footer,
    onMount({ overlay, close }) {
      const form = overlay.querySelector(`#${formId}`);
      const titleInput = form.querySelector('[name="title"]');
      const dateInput = form.querySelector('[name="scheduled_date"]');
      const saveButton = overlay.querySelector('[data-act="save"]');
      const deleteButton = overlay.querySelector('[data-act="delete"]');
      let busy = false;

      const setBusy = (value) => {
        busy = value;
        saveButton.disabled = value;
        saveButton.textContent = value ? "Сохранение…" : "Сохранить";
      };

      titleInput.addEventListener("input", () => titleInput.classList.remove("is-invalid"));
      overlay.querySelector('[data-act="clear-date"]').addEventListener("click", () => {
        dateInput.value = "";
        dateInput.focus();
      });

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
        const selected = form.querySelector('input[name="status"]:checked');
        const projectSelect = form.querySelector('[name="project_id"]');
        const targetProjectId = isNew
          ? projectSelect?.value || projectId
          : task.project_id;
        setBusy(true);
        try {
          await onSave({
            projectId: targetProjectId,
            title,
            description: form.querySelector('[name="description"]').value,
            status: selected ? selected.value : status,
            scheduled_date: dateInput.value || null,
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
            await onDelete();
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
