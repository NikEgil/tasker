(function () {
  try {
    var params = new URLSearchParams(window.location.search);
    var forced = params.get("theme");
    if (forced === "dark" || forced === "light") {
      document.documentElement.dataset.theme = forced;
      document.documentElement.style.colorScheme = forced;
      return;
    }
    var saved = localStorage.getItem("tasker-theme");
    var theme =
      saved === "dark" || saved === "light"
        ? saved
        : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  } catch (error) {
    document.documentElement.dataset.theme = "light";
  }
})();
