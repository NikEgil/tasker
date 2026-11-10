const ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

export function icon(name, className = "") {
  return `<svg class="icon ${className}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

const timeFormatter = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});
const shortDateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
});
const longDateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
});
const fullDateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function asDate(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function dayDifference(iso) {
  const date = asDate(iso);
  if (!date) return 99;
  const today = startOfDay(new Date());
  return Math.round((today.getTime() - startOfDay(date).getTime()) / 86400000);
}

export function formatTime(iso) {
  const date = asDate(iso);
  return date ? timeFormatter.format(date) : "";
}

export function formatShortDate(iso) {
  const date = asDate(iso);
  if (!date) return "";
  if (date.getFullYear() !== new Date().getFullYear()) {
    return `${shortDateFormatter.format(date)} ${date.getFullYear()}`;
  }
  return shortDateFormatter.format(date);
}

export function formatDayLabel(iso) {
  const date = asDate(iso);
  if (!date) return "";
  const diff = dayDifference(iso);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  if (date.getFullYear() !== new Date().getFullYear()) {
    return `${longDateFormatter.format(date)} ${date.getFullYear()}`;
  }
  return longDateFormatter.format(date);
}

export function formatFullDate(iso) {
  const date = asDate(iso);
  return date ? fullDateTimeFormatter.format(date) : "";
}

export function formatCardDate(iso) {
  const date = asDate(iso);
  if (!date) return "";
  const diff = dayDifference(iso);
  if (diff === 0) return `сегодня, ${timeFormatter.format(date)}`;
  if (diff === 1) return `вчера, ${timeFormatter.format(date)}`;
  return formatShortDate(iso);
}

export function plural(number, one, few, many) {
  const abs = Math.abs(number) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (last > 1 && last < 5) return few;
  if (last === 1) return one;
  return many;
}

export function formatSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) {
    return `${new Intl.NumberFormat("ru-RU").format(value)} Б`;
  }
  const units = ["КБ", "МБ", "ГБ", "ТБ"];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const formatted = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: size >= 100 ? 0 : 1,
  }).format(size);
  return `${formatted} ${units[unit]}`;
}

export function linkify(text) {
  const source = String(text ?? "");
  const urlPattern = /\bhttps?:\/\/[^\s<]+/gi;
  let output = "";
  let lastIndex = 0;
  let match;
  while ((match = urlPattern.exec(source)) !== null) {
    let url = match[0];
    let trailing = "";
    while (/[.,;:!?)\]}"']$/.test(url) && url.length > 8) {
      trailing = url.slice(-1) + trailing;
      url = url.slice(0, -1);
    }
    output += esc(source.slice(lastIndex, match.index)).replace(/\n/g, "<br>");
    output += `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`;
    output += esc(trailing);
    lastIndex = match.index + match[0].length;
  }
  output += esc(source.slice(lastIndex)).replace(/\n/g, "<br>");
  return output;
}

export function fileExt(filename) {
  const name = String(filename || "");
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "ФАЙЛ";
  return name.slice(dot + 1).toUpperCase().slice(0, 4);
}

const FILE_CATEGORIES = {
  pdf: "pdf",
  doc: "doc",
  docx: "doc",
  txt: "doc",
  md: "doc",
  rtf: "doc",
  odt: "doc",
  pages: "doc",
  xls: "sheet",
  xlsx: "sheet",
  csv: "sheet",
  ods: "sheet",
  numbers: "sheet",
  ppt: "slide",
  pptx: "slide",
  key: "slide",
  odp: "slide",
  zip: "archive",
  rar: "archive",
  "7z": "archive",
  gz: "archive",
  tar: "archive",
  png: "img",
  jpg: "img",
  jpeg: "img",
  gif: "img",
  webp: "img",
  avif: "img",
  heic: "img",
  bmp: "img",
  svg: "img",
  mp3: "audio",
  wav: "audio",
  ogg: "audio",
  m4a: "audio",
  flac: "audio",
  mp4: "video",
  mov: "video",
  mkv: "video",
  webm: "video",
  avi: "video",
  js: "code",
  ts: "code",
  py: "code",
  json: "code",
  html: "code",
  css: "code",
  sql: "code",
};

export function fileCategory(filename, mime = "") {
  const ext = fileExt(filename).toLowerCase();
  if (FILE_CATEGORIES[ext]) return FILE_CATEGORIES[ext];
  if (mime.startsWith("image/")) return "img";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "other";
}

export function isRasterImage(attachment) {
  return Boolean(attachment && attachment.is_image);
}

export function autoResize(textarea, maxHeight = 176) {
  if (!textarea) return;
  textarea.style.height = "auto";
  const height = Math.min(textarea.scrollHeight, maxHeight);
  textarea.style.height = `${height}px`;
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
}

/* ------------------------------------------------------------------ */
/* Тосты                                                               */
/* ------------------------------------------------------------------ */
export function toast(message, type = "info") {
  const root = document.getElementById("toast-root");
  if (!root) return;
  while (root.children.length >= 4) root.firstElementChild.remove();

  const element = document.createElement("div");
  element.className = `toast toast-${type}`;
  const iconName = type === "error" ? "alert" : type === "success" ? "check" : "info";
  element.innerHTML = `${icon(iconName)}<span class="toast-text">${esc(message)}</span>`;
  root.appendChild(element);
  requestAnimationFrame(() => element.classList.add("is-visible"));

  let removed = false;
  const remove = () => {
    if (removed) return;
    removed = true;
    element.classList.remove("is-visible");
    setTimeout(() => element.remove(), 180);
  };
  element.addEventListener("click", remove);
  setTimeout(remove, type === "error" ? 6000 : 3200);
}

/* ------------------------------------------------------------------ */
/* Модальные окна                                                      */
/* ------------------------------------------------------------------ */
const modalStack = [];
let modalCounter = 0;

function focusableElements(container) {
  return [...container.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )].filter((element) => element.getClientRects().length > 0);
}

function trapFocus(event, container) {
  const elements = focusableElements(container);
  if (!elements.length) return;
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function openModal({
  title,
  subtitle = "",
  body = "",
  footer = "",
  className = "",
  onMount,
  onClose,
} = {}) {
  const root = document.getElementById("modal-root");
  const previousFocus = document.activeElement;
  const titleId = `modal-title-${++modalCounter}`;

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal ${className}" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
      <header class="modal-head">
        <div class="modal-heading">
          <h2 class="modal-title" id="${titleId}">${esc(title || "")}</h2>
          ${subtitle ? `<p class="modal-subtitle">${esc(subtitle)}</p>` : ""}
        </div>
        <button type="button" class="icon-btn modal-close" aria-label="Закрыть">${icon("x")}</button>
      </header>
      <div class="modal-body"></div>
      ${footer ? `<footer class="modal-footer"></footer>` : ""}
    </div>`;

  const modal = overlay.querySelector(".modal");
  const bodyContainer = overlay.querySelector(".modal-body");
  const footerContainer = overlay.querySelector(".modal-footer");

  if (body instanceof Node) {
    bodyContainer.appendChild(body);
  } else {
    bodyContainer.innerHTML = body;
  }
  if (footerContainer) {
    if (footer instanceof Node) footerContainer.appendChild(footer);
    else footerContainer.innerHTML = footer;
  }

  root.appendChild(overlay);
  document.body.classList.add("modal-open");

  let closed = false;
  const controller = { overlay, modal, body: bodyContainer, footer: footerContainer, close };

  function close() {
    if (closed) return;
    closed = true;
    const index = modalStack.indexOf(controller);
    if (index !== -1) modalStack.splice(index, 1);
    document.removeEventListener("keydown", keyHandler, true);
    overlay.remove();
    if (!modalStack.length) document.body.classList.remove("modal-open");
    if (previousFocus && document.contains(previousFocus)) {
      try {
        previousFocus.focus({ preventScroll: true });
      } catch (error) {
        /* ignore */
      }
    }
    if (typeof onClose === "function") onClose();
  }

  function keyHandler(event) {
    if (modalStack[modalStack.length - 1] !== controller) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      trapFocus(event, overlay);
    }
  }

  overlay.querySelector(".modal-close").addEventListener("click", close);
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener("keydown", keyHandler, true);
  modalStack.push(controller);

  if (typeof onMount === "function") {
    onMount(controller);
  }

  const focusTarget =
    overlay.querySelector("[autofocus]") ||
    bodyContainer.querySelector("input:not([type='hidden']), textarea, select, button") ||
    overlay.querySelector(".modal-close");
  requestAnimationFrame(() => {
    if (focusTarget && document.contains(focusTarget)) {
      try {
        focusTarget.focus({ preventScroll: true });
      } catch (error) {
        /* ignore */
      }
    }
  });

  return controller;
}

export function confirmDialog({
  title = "Подтвердите действие",
  message = "",
  confirmLabel = "Удалить",
  cancelLabel = "Отмена",
  danger = true,
} = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    openModal({
      title,
      className: "modal-confirm",
      body: `<p class="confirm-text">${esc(message)}</p>`,
      footer: `
        <button type="button" class="btn btn-quiet" data-act="cancel" autofocus>${esc(cancelLabel)}</button>
        <button type="button" class="btn ${danger ? "btn-danger" : "btn-primary"}" data-act="ok">${esc(confirmLabel)}</button>`,
      onMount({ overlay, close }) {
        overlay.querySelector('[data-act="cancel"]').addEventListener("click", () => {
          finish(false);
          close();
        });
        overlay.querySelector('[data-act="ok"]').addEventListener("click", () => {
          finish(true);
          close();
        });
      },
      onClose() {
        finish(false);
      },
    });
  });
}

/* ------------------------------------------------------------------ */
/* Контекстные меню                                                    */
/* ------------------------------------------------------------------ */
export function showMenu(anchor, items) {
  if (!anchor || !items?.length) return () => {};

  const menu = document.createElement("div");
  menu.className = "popover-menu";
  menu.setAttribute("role", "menu");

  items.forEach((item, index) => {
    if (item.separator) {
      const separator = document.createElement("div");
      separator.className = "menu-separator";
      menu.appendChild(separator);
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = `menu-item${item.danger ? " is-danger" : ""}`;
    button.dataset.index = String(index);
    button.setAttribute("role", "menuitem");
    if (item.disabled) {
      button.disabled = true;
      button.classList.add("is-disabled");
    }
    const leading = item.icon ? icon(item.icon) : '<span class="menu-icon-spacer"></span>';
    const trailing = item.checked ? icon("check") : "";
    button.innerHTML = `${leading}<span class="menu-label">${esc(item.label)}</span>${trailing}`;
    menu.appendChild(button);
  });

  document.body.appendChild(menu);

  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  const menuWidth = menu.offsetWidth;
  const menuHeight = menu.offsetHeight;
  let left = Math.min(Math.max(margin, rect.right - menuWidth), window.innerWidth - menuWidth - margin);
  let top = rect.bottom + 6;
  if (top + menuHeight > window.innerHeight - margin) {
    top = Math.max(margin, rect.top - menuHeight - 6);
  }
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", close);
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("blur", close);
    menu.remove();
  }

  function onPointerDown(event) {
    if (!menu.contains(event.target) && !anchor.contains(event.target)) close();
  }

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const buttons = [...menu.querySelectorAll(".menu-item:not(.is-disabled)")];
    if (!buttons.length) return;
    event.preventDefault();
    const current = buttons.indexOf(document.activeElement);
    const next =
      event.key === "ArrowDown"
        ? (current + 1) % buttons.length
        : (current - 1 + buttons.length) % buttons.length;
    buttons[next].focus();
  }

  menu.addEventListener("click", (event) => {
    const button = event.target.closest(".menu-item");
    if (!button || button.disabled) return;
    const item = items[Number(button.dataset.index)];
    close();
    if (item && typeof item.action === "function") item.action();
  });

  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("resize", close);
  window.addEventListener("scroll", close, true);
  window.addEventListener("blur", close);

  const firstEnabled = menu.querySelector(".menu-item:not(.is-disabled)");
  if (firstEnabled) requestAnimationFrame(() => firstEnabled.focus({ preventScroll: true }));

  return close;
}

/* ------------------------------------------------------------------ */
/* Буфер обмена                                                        */
/* ------------------------------------------------------------------ */
export async function copyText(text) {
  if (!text) {
    toast("В заметке нет текста", "info");
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано", "success");
    return true;
  } catch (error) {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const ok = document.execCommand("copy");
      textarea.remove();
      if (ok) {
        toast("Скопировано", "success");
        return true;
      }
    } catch (fallbackError) {
      /* ignore */
    }
    toast("Не удалось скопировать", "error");
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* Просмотр изображений                                                */
/* ------------------------------------------------------------------ */
let activeLightboxClose = null;

export function openLightbox(attachment) {
  if (!attachment) return;
  if (activeLightboxClose) activeLightboxClose();

  const root = document.getElementById("lightbox-root");
  const overlay = document.createElement("div");
  overlay.className = "lightbox";
  overlay.innerHTML = `
    <div class="lightbox-backdrop" data-close-lightbox></div>
    <div class="lightbox-panel" role="dialog" aria-modal="true" aria-label="${esc(attachment.filename)}">
      <header class="lightbox-head">
        <div class="lightbox-title">
          <span class="lightbox-name">${esc(attachment.filename)}</span>
          <span class="lightbox-size">${formatSize(attachment.size)}</span>
        </div>
        <div class="lightbox-actions">
          <a class="icon-btn" href="/api/attachments/${encodeURIComponent(attachment.id)}?download=1"
             download="${esc(attachment.filename)}" title="Скачать" aria-label="Скачать">
            ${icon("download")}
          </a>
          <button type="button" class="icon-btn" data-close-lightbox title="Закрыть" aria-label="Закрыть">
            ${icon("x")}
          </button>
        </div>
      </header>
      <div class="lightbox-stage">
        <img src="/api/attachments/${encodeURIComponent(attachment.id)}" alt="${esc(attachment.filename)}">
      </div>
    </div>`;
  root.appendChild(overlay);
  document.body.classList.add("lightbox-open");

  const image = overlay.querySelector("img");
  image.addEventListener("error", () => {
    const stage = overlay.querySelector(".lightbox-stage");
    stage.innerHTML = `<p class="lightbox-error">Не удалось показать изображение. Скачайте файл.</p>`;
  });

  function close() {
    if (!overlay.parentNode) return;
    document.removeEventListener("keydown", onKeyDown, true);
    overlay.remove();
    document.body.classList.remove("lightbox-open");
    activeLightboxClose = null;
  }

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  overlay.addEventListener("click", (event) => {
    if (event.target.closest("[data-close-lightbox]")) close();
  });
  document.addEventListener("keydown", onKeyDown, true);
  const closeButton = overlay.querySelector(".lightbox-head [data-close-lightbox]");
  if (closeButton) closeButton.focus({ preventScroll: true });
  activeLightboxClose = close;
}
