import { api } from "./api.js";
import {
  esc,
  icon,
  linkify,
  formatTime,
  formatDayLabel,
  formatFullDate,
  formatSize,
  fileExt,
  fileCategory,
  isRasterImage,
  autoResize,
  openModal,
  confirmDialog,
  toast,
  copyText,
  openLightbox,
} from "./ui.js";

const MAX_FILES = 12;
const MAX_FILE_SIZE = 40 * 1024 * 1024;
const MAX_TOTAL_SIZE = 96 * 1024 * 1024;

let pendingCounter = 0;

export function createChat({ root, callbacks }) {
  const scroll = root.querySelector("#chat-scroll");
  const messagesEl = root.querySelector("#chat-messages");
  const countEl = root.querySelector("#chat-count");
  const input = root.querySelector("#chat-input");
  const sendButton = root.querySelector("#send-btn");
  const attachButton = root.querySelector("#attach-btn");
  const fileInput = root.querySelector("#file-input");
  const pendingEl = root.querySelector("#pending-files");
  const composer = root.querySelector("#chat-composer");
  const dropHint = root.querySelector("#chat-drop-hint");

  let messages = [];
  let hasMore = false;
  let pending = [];
  let sending = false;
  let loadMoreBusy = false;
  let nearBottom = true;
  let dragDepth = 0;

  /* -------------------------------------------------------------- */
  /* разметка сообщений                                             */
  /* -------------------------------------------------------------- */
  function imageHTML(attachment) {
    return `
      <button type="button" class="message-image" data-open-image="${esc(attachment.id)}"
              title="${esc(attachment.filename)} · ${formatSize(attachment.size)}">
        <img src="${api.attachmentUrl(attachment.id)}" alt="${esc(attachment.filename)}" loading="lazy">
        <span class="message-image-name">${esc(attachment.filename)}</span>
      </button>`;
  }

  function fileHTML(attachment) {
    const category = fileCategory(attachment.filename, attachment.mime);
    return `
      <a class="message-file" href="${api.attachmentUrl(attachment.id, true)}"
         download="${esc(attachment.filename)}" title="Скачать ${esc(attachment.filename)}">
        <span class="file-badge file-${category}">${esc(fileExt(attachment.filename))}</span>
        <span class="file-info">
          <span class="file-name">${esc(attachment.filename)}</span>
          <span class="file-size">${formatSize(attachment.size)}</span>
        </span>
        <span class="file-download">${icon("download")}</span>
      </a>`;
  }

  function messageHTML(message) {
    const attachments = message.attachments || [];
    const images = attachments.filter((attachment) => isRasterImage(attachment));
    const files = attachments.filter((attachment) => !isRasterImage(attachment));
    const edited = message.updated_at && message.updated_at !== message.created_at;
    return `
      <article class="message" data-message-id="${esc(message.id)}">
        <header class="message-head">
          <div class="message-stamp">
            <time datetime="${esc(message.created_at)}"
                  title="${esc(formatFullDate(message.created_at))}">${formatTime(message.created_at)}</time>
            ${edited ? '<span class="message-edited">изменено</span>' : ""}
          </div>
          <div class="message-actions">
            ${
              message.body
                ? `<button type="button" class="message-action" data-msg-action="copy" title="Скопировать текст" aria-label="Скопировать текст">${icon("copy")}</button>`
                : ""
            }
            <button type="button" class="message-action" data-msg-action="edit" title="Редактировать" aria-label="Редактировать">${icon("edit")}</button>
            <button type="button" class="message-action" data-msg-action="delete" title="Удалить" aria-label="Удалить">${icon("trash")}</button>
          </div>
        </header>
        ${message.body ? `<div class="message-body">${linkify(message.body)}</div>` : ""}
        ${images.length ? `<div class="message-images">${images.map(imageHTML).join("")}</div>` : ""}
        ${files.length ? `<div class="message-files">${files.map(fileHTML).join("")}</div>` : ""}
      </article>`;
  }

  function emptyHTML() {
    return `
      <div class="chat-empty">
        <div class="chat-empty-art" aria-hidden="true">
          <svg viewBox="0 0 132 112" fill="none">
            <rect x="13" y="18" width="78" height="72" rx="12" fill="var(--surface)" stroke="var(--border-strong)"/>
            <rect x="25" y="33" width="38" height="6" rx="3" fill="var(--accent-soft)"/>
            <rect x="25" y="46" width="52" height="4" rx="2" fill="var(--border)"/>
            <rect x="25" y="56" width="44" height="4" rx="2" fill="var(--border)"/>
            <rect x="25" y="66" width="30" height="4" rx="2" fill="var(--border)"/>
            <rect x="44" y="28" width="76" height="72" rx="12" fill="var(--surface)" stroke="var(--border)"/>
            <rect x="57" y="44" width="38" height="6" rx="3" fill="var(--accent-soft)"/>
            <rect x="57" y="57" width="50" height="4" rx="2" fill="var(--border)"/>
            <rect x="57" y="67" width="38" height="4" rx="2" fill="var(--border)"/>
            <circle cx="105" cy="91" r="14" fill="var(--accent)"/>
            <path d="M105 85v12M99 91h12" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>
          </svg>
        </div>
        <h2 class="chat-empty-title">Пока заметок нет</h2>
        <p class="chat-empty-text">Записывайте договорённости, ссылки и прикрепляйте файлы — всё останется в проекте.</p>
      </div>`;
  }

  function loadMoreHTML() {
    if (!hasMore) return "";
    return `
      <button type="button" class="load-more" data-load-more ${loadMoreBusy ? "disabled" : ""}>
        ${icon("chevron-down")}
        <span>${loadMoreBusy ? "Загружаем…" : "Показать более ранние"}</span>
      </button>`;
  }

  /* -------------------------------------------------------------- */
  /* рендер                                                         */
  /* -------------------------------------------------------------- */
  function render(nextMessages, options = {}) {
    const { stickToBottom = false, preserveScroll = false, hasMore: nextHasMore } = options;
    const previousHeight = scroll.scrollHeight;
    const previousTop = scroll.scrollTop;

    messages = Array.isArray(nextMessages) ? nextMessages : [];
    if (typeof nextHasMore === "boolean") hasMore = nextHasMore;

    if (!messages.length) {
      messagesEl.innerHTML = emptyHTML();
    } else {
      let lastDay = "";
      const parts = [loadMoreHTML()];
      for (const message of messages) {
        const day = formatDayLabel(message.created_at);
        if (day !== lastDay) {
          parts.push(`<div class="chat-day"><span>${esc(day)}</span></div>`);
          lastDay = day;
        }
        parts.push(messageHTML(message));
      }
      messagesEl.innerHTML = parts.join("");
    }

    const shouldScroll = stickToBottom || (nearBottom && !preserveScroll);
    if (preserveScroll) {
      requestAnimationFrame(() => {
        scroll.scrollTop = scroll.scrollHeight - previousHeight + previousTop;
        nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
      });
    } else if (shouldScroll) {
      requestAnimationFrame(() => {
        scroll.scrollTop = scroll.scrollHeight;
        nearBottom = true;
      });
    }
  }

  function renderLoading() {
    messagesEl.innerHTML = `
      <div class="chat-skeleton" aria-label="Загрузка заметок">
        <div class="skeleton-line" style="width: 42%"></div>
        <div class="skeleton-card"></div>
        <div class="skeleton-line" style="width: 30%"></div>
        <div class="skeleton-card skeleton-card-sm"></div>
      </div>`;
  }

  function reset() {
    messages = [];
    hasMore = false;
    nearBottom = true;
    clearPending();
    input.value = "";
    autoResize(input);
    updateSendState();
    renderLoading();
  }

  function setCount(count) {
    if (!countEl) return;
    countEl.textContent = String(count ?? 0);
  }

  /* -------------------------------------------------------------- */
  /* действия с сообщениями                                         */
  /* -------------------------------------------------------------- */
  function findMessage(id) {
    return messages.find((message) => message.id === id);
  }

  async function deleteMessage(message) {
    const confirmed = await confirmDialog({
      title: "Удалить заметку?",
      message: "Заметка и все прикреплённые к ней файлы будут удалены без возможности восстановления.",
      confirmLabel: "Удалить",
    });
    if (!confirmed) return;
    try {
      await callbacks.deleteMessage(message.id);
    } catch (error) {
      toast(error?.message || "Не удалось удалить заметку", "error");
    }
  }

  function editMessage(message) {
    const hasAttachments = (message.attachments || []).length > 0;
    const body = `
      <textarea class="textarea" name="text" rows="8" maxlength="20000"
                placeholder="Текст заметки…" autofocus>${esc(message.body)}</textarea>
      ${hasAttachments ? '<p class="form-note">Вложения останутся без изменений.</p>' : ""}`;
    const footer = `
      <span class="modal-footer-spacer"></span>
      <button type="button" class="btn btn-quiet" data-act="cancel">Отмена</button>
      <button type="button" class="btn btn-primary" data-act="save">Сохранить</button>`;

    openModal({
      title: "Редактировать заметку",
      subtitle: formatFullDate(message.created_at),
      className: "modal-message",
      body,
      footer,
      onMount({ overlay, close }) {
        const textarea = overlay.querySelector("textarea");
        const saveButton = overlay.querySelector('[data-act="save"]');
        let busy = false;

        const save = async () => {
          if (busy) return;
          const text = textarea.value.trim();
          if (!text && !hasAttachments) {
            toast("Текст заметки не может быть пустым", "error");
            textarea.focus();
            return;
          }
          busy = true;
          saveButton.disabled = true;
          saveButton.textContent = "Сохранение…";
          try {
            await callbacks.updateMessage(message.id, text);
            close();
          } catch (error) {
            toast(error?.message || "Не удалось сохранить заметку", "error");
            busy = false;
            saveButton.disabled = false;
            saveButton.textContent = "Сохранить";
          }
        };

        saveButton.addEventListener("click", save);
        overlay.querySelector('[data-act="cancel"]').addEventListener("click", close);
        textarea.addEventListener("keydown", (event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            save();
          }
        });
      },
    });
  }

  messagesEl.addEventListener("click", (event) => {
    const loadMoreButton = event.target.closest("[data-load-more]");
    if (loadMoreButton) {
      if (loadMoreBusy) return;
      loadMoreBusy = true;
      loadMoreButton.disabled = true;
      const label = loadMoreButton.querySelector("span");
      if (label) label.textContent = "Загружаем…";
      Promise.resolve(callbacks.loadMoreMessages())
        .catch((error) => {
          toast(error?.message || "Не удалось загрузить заметки", "error");
        })
        .finally(() => {
          loadMoreBusy = false;
          const current = messagesEl.querySelector("[data-load-more]");
          if (current) {
            current.disabled = false;
            const currentLabel = current.querySelector("span");
            if (currentLabel) currentLabel.textContent = "Показать более ранние";
          }
        });
      return;
    }

    const imageButton = event.target.closest("[data-open-image]");
    if (imageButton) {
      const messageEl = imageButton.closest(".message");
      const message = messageEl ? findMessage(messageEl.dataset.messageId) : null;
      const attachment = message?.attachments?.find(
        (item) => item.id === imageButton.dataset.openImage
      );
      if (attachment) openLightbox(attachment);
      return;
    }

    const actionButton = event.target.closest("[data-msg-action]");
    if (!actionButton) return;
    const messageEl = actionButton.closest(".message");
    const message = messageEl ? findMessage(messageEl.dataset.messageId) : null;
    if (!message) return;

    switch (actionButton.dataset.msgAction) {
      case "copy":
        copyText(message.body);
        break;
      case "edit":
        editMessage(message);
        break;
      case "delete":
        deleteMessage(message);
        break;
      default:
        break;
    }
  });

  /* -------------------------------------------------------------- */
  /* вложения перед отправкой                                       */
  /* -------------------------------------------------------------- */
  function updateSendState() {
    const hasText = Boolean(input.value.trim());
    sendButton.disabled = sending || (!hasText && !pending.length);
    sendButton.classList.toggle("is-busy", sending);
  }

  function totalPendingSize() {
    return pending.reduce((sum, item) => sum + item.file.size, 0);
  }

  function addFiles(fileList) {
    const files = [...fileList];
    if (!files.length || sending) return;
    const errors = [];
    for (const file of files) {
      if (pending.length >= MAX_FILES) {
        errors.push(`Можно прикрепить не больше ${MAX_FILES} файлов`);
        break;
      }
      if (file.size > MAX_FILE_SIZE) {
        errors.push(`Файл «${file.name}» больше 40 МБ`);
        continue;
      }
      if (totalPendingSize() + file.size > MAX_TOTAL_SIZE) {
        errors.push("Суммарный размер файлов больше 96 МБ");
        break;
      }
      const duplicate = pending.some(
        (item) =>
          item.file.name === file.name &&
          item.file.size === file.size &&
          item.file.lastModified === file.lastModified
      );
      if (duplicate) continue;
      pendingCounter += 1;
      pending.push({
        id: `pending-${Date.now()}-${pendingCounter}`,
        file,
        preview:
          file.type.startsWith("image/") && file.type !== "image/svg+xml"
            ? URL.createObjectURL(file)
            : null,
      });
    }
    if (errors.length) toast(errors[0], "error");
    renderPending();
    updateSendState();
  }

  function removePending(id) {
    const index = pending.findIndex((item) => item.id === id);
    if (index === -1) return;
    const [removed] = pending.splice(index, 1);
    if (removed.preview) URL.revokeObjectURL(removed.preview);
    renderPending();
    updateSendState();
    input.focus();
  }

  function clearPending() {
    for (const item of pending) {
      if (item.preview) URL.revokeObjectURL(item.preview);
    }
    pending = [];
    renderPending();
    updateSendState();
  }

  function renderPending() {
    if (!pendingEl) return;
    if (!pending.length) {
      pendingEl.hidden = true;
      pendingEl.innerHTML = "";
      return;
    }
    pendingEl.hidden = false;
    pendingEl.innerHTML = pending
      .map((item) => {
        const file = item.file;
        const category = fileCategory(file.name, file.type);
        return `
          <div class="pending-file" data-pending-id="${esc(item.id)}">
            ${
              item.preview
                ? `<img class="pending-thumb" src="${item.preview}" alt="">`
                : `<span class="file-badge file-${category}">${esc(fileExt(file.name))}</span>`
            }
            <span class="pending-info">
              <span class="pending-name">${esc(file.name)}</span>
              <span class="pending-size">${formatSize(file.size)}</span>
            </span>
            <button type="button" class="pending-remove" data-remove-pending="${esc(item.id)}"
                    title="Убрать файл" aria-label="Убрать файл">${icon("x")}</button>
          </div>`;
      })
      .join("");
  }

  pendingEl?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-pending]");
    if (!button) return;
    removePending(button.dataset.removePending);
  });

  /* -------------------------------------------------------------- */
  /* отправка                                                       */
  /* -------------------------------------------------------------- */
  async function send() {
    if (sending) return;
    const text = input.value.trim();
    if (!text && !pending.length) return;
    const files = pending.map((item) => item.file);
    sending = true;
    updateSendState();
    try {
      await callbacks.sendMessage(text, files);
      if (input.value.trim() === text) {
        input.value = "";
        autoResize(input);
      }
      clearPending();
      scroll.scrollTop = scroll.scrollHeight;
      nearBottom = true;
    } catch (error) {
      toast(error?.message || "Не удалось отправить заметку", "error");
    } finally {
      sending = false;
      updateSendState();
      input.focus();
    }
  }

  sendButton.addEventListener("click", send);
  input.addEventListener("input", () => {
    autoResize(input);
    updateSendState();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });

  input.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  });

  attachButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  scroll.addEventListener("scroll", () => {
    nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
  });

  /* -------------------------------------------------------------- */
  /* перетаскивание файлов в чат                                    */
  /* -------------------------------------------------------------- */
  const panel = root.querySelector("#chat-panel");

  function hasFiles(event) {
    return Boolean(
      event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")
    );
  }

  function showDropHint(show) {
    if (!dropHint) return;
    dropHint.hidden = !show;
    composer.classList.toggle("is-drop-active", show);
  }

  panel.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    showDropHint(true);
  });

  panel.addEventListener("dragover", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });

  panel.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) showDropHint(false);
  });

  panel.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = 0;
    showDropHint(false);
    addFiles(event.dataTransfer.files);
    input.focus();
  });

  window.addEventListener("dragend", () => {
    dragDepth = 0;
    showDropHint(false);
  });

  window.addEventListener("drop", () => {
    dragDepth = 0;
    showDropHint(false);
  });

  return {
    render,
    reset,
    setCount,
    focusInput() {
      input.focus();
    },
  };
}
