// The onsite site's one script, linked by site/layouts/head.ejs
// with `defer`. Every page reads and works without it. It adds two
// extras:
//
// - A Copy button on shell and YAML code blocks.
// - The Theme button in the masthead, which switches between light
//   and dark and remembers the choice.

// ---- Constants --------------------------------------------------------

const COPY_LABEL = "Copy";

const COPIED_LABEL = "Copied";

const FAILED_LABEL = "Select and copy";

const RESET_AFTER_MS = 2400;

const THEME_KEY = "hands-theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

// ---- Copy buttons -----------------------------------------------------

function selectText(source) {
  const range = document.createRange();

  range.selectNodeContents(source);

  const selection = getSelection();

  selection.removeAllRanges();
  selection.addRange(range);
}

async function copyFrom(button, source) {
  try {
    await navigator.clipboard.writeText(source.textContent.trimEnd());
    button.textContent = COPIED_LABEL;
    button.dataset.state = "done";
  } catch {
    selectText(source);
    button.textContent = FAILED_LABEL;
  }

  setTimeout(() => {
    button.textContent = COPY_LABEL;
    delete button.dataset.state;
  }, RESET_AFTER_MS);
}

function addCopyButton(block) {
  const source = block.querySelector("code");
  const button = document.createElement("button");

  button.type = "button";
  button.className = "code__copy";
  button.textContent = COPY_LABEL;
  button.addEventListener("click", () => copyFrom(button, source));

  block.append(button);
}

// ---- Theme ------------------------------------------------------------

function currentTheme() {
  const picked = document.documentElement.dataset.theme;

  if (picked) {
    return picked;
  }

  return matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

function describe(button) {
  const next = currentTheme() === "dark" ? "light" : "dark";

  button.setAttribute("aria-label", `Switch to the ${next} theme`);
}

function saveTheme(theme) {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // No storage: the choice lasts for this page only.
  }
}

function switchTheme(button) {
  const next = currentTheme() === "dark" ? "light" : "dark";

  document.documentElement.dataset.theme = next;

  saveTheme(next);
  describe(button);
}

function showThemeButton(button) {
  describe(button);

  button.hidden = false;
  button.addEventListener("click", () => switchTheme(button));
}

// ---- Run --------------------------------------------------------------

for (const block of document.querySelectorAll(".code[data-copy]")) {
  addCopyButton(block);
}

for (const button of document.querySelectorAll("[data-theme-toggle]")) {
  showThemeButton(button);
}
