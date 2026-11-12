import {
  esc,
  icon,
  formatFullDate,
  openModal,
  confirmDialog,
  toast,
} from "./ui.js";
import { addDaysKey, dateKey, taskDurationDays } from "./calendar.js";

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
  const endValue = task?.scheduled_end_date || "";
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
        <span class="field-label">Даты в календаре <span class="field-hint">необязательно</span></span>
        <div class="date-range-row">
          <label class="date-range-field">
            <span>Начало</span>
            <input class="input" id="calendar-task-date" type="date" name="scheduled_date"
                   value="${esc(dateValue)}">
          </label>
          <span class="date-range-dash" aria-hidden="true">—</span>
          <label class="date-range-field">
            <span>Конец</span>
            <input class="input" id="calendar-task-end-date" type="date" name="scheduled_end_date"
                   value="${esc(endValue)}">
          </label>
        </div>
        <div class="duration-presets" role="group" aria-label="Длительность задачи">
          <span class="duration-presets-label">Длительность:</span>
          <button type="button" class="duration-chip" data-duration="1">1 день</button>
          <button type="button" class="duration-chip" data-duration="2">2 дня</button>
          <button type="button" class="duration-chip" data-duration="3">3 дня</button>
          <button type="button" class="duration-chip" data-duration="4">4 дня</button>
          <button type="button" class="duration-chip" data-duration="5">5 дней</button>
          <button type="button" class="duration-chip" data-duration="6">6 дней</button>
        </div>
        <div class="date-range-row date-range-actions">
          <span class="date-range-hint" id="calendar-task-duration">Один день</span>
          <button type="button" class="btn btn-quiet btn-sm" data-act="clear-date">Убрать из календаря</button>
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
      const endDateInput = form.querySelector('[name="scheduled_end_date"]');
      const durationLabel = overlay.querySelector("#calendar-task-duration");
      const durationButtons = [...overlay.querySelectorAll("[data-duration]")];
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
            ? "Без даты — задача будет в списке без даты"
            : days === 1
              ? "Один день"
              : `${days} ${days < 5 ? "дня" : "дней"}`;
        durationLabel.classList.toggle("is-invalid", invalid);
        durationButtons.forEach((button) => {
          button.classList.toggle(
            "is-active",
            Number(button.dataset.duration) === days
          );
        });
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
      durationButtons.forEach((button) => {
        button.addEventListener("click", () => {
          const days = Number(button.dataset.duration);
          if (!dateInput.value) {
            dateInput.value = dateKey(new Date());
          }
          endDateInput.value =
            days <= 1 ? "" : addDaysKey(dateInput.value, days - 1) || "";
          updateDuration();
        });
      });
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
        const selected = form.querySelector('input[name="status"]:checked');
        const projectSelect = form.querySelector('[name="project_id"]');
        const targetProjectId = isNew
          ? projectSelect?.value || projectId
          : task.project_id;
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
          await onSave({
            projectId: targetProjectId,
            title,
            description: form.querySelector('[name="description"]').value,
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
