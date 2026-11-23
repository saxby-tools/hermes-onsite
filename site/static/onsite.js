// The onsite site's one script, linked by site/layouts/head.ejs
// with `defer`. Every page reads and works without it. It adds two
// extras:
//
// - A Copy button on shell and YAML code blocks.
// - The theme toggle in the masthead. It has three choices: follow
//   the system, light, or dark. Light and dark are remembered.

// ---- Constants --------------------------------------------------------

const COPY_LABEL = "Copy";

const COPIED_LABEL = "Copied";

const FAILED_LABEL = "Select and copy";

const RESET_AFTER_MS = 2400;

const THEME_KEY = "onsite-theme";

const THEME_SYSTEM = "system";

const THEME_PICKS = ["light", "dark"];

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

// What the reader last picked. Anything else, or nothing, means the
// page follows the system.
function savedTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);

    if (THEME_PICKS.includes(saved)) {
      return saved;
    }
  } catch {
    // No storage: fall through to the system's theme.
  }

  return THEME_SYSTEM;
}

function saveTheme(choice) {
  try {
    if (choice === THEME_SYSTEM) {
      localStorage.removeItem(THEME_KEY);

      return;
    }

    localStorage.setItem(THEME_KEY, choice);
  } catch {
    // No storage: the choice lasts for this page only.
  }
}

// Light and dark set `data-theme`. System removes it, and the
// stylesheet's light-dark() colours follow the device again.
function applyTheme(choice) {
  if (choice === THEME_SYSTEM) {
    delete document.documentElement.dataset.theme;

    return;
  }

  document.documentElement.dataset.theme = choice;
}

function markPressed(toggle, choice) {
  for (const option of toggle.querySelectorAll("[data-theme-choice]")) {
    const pressed = option.dataset.themeChoice === choice;

    option.setAttribute("aria-pressed", String(pressed));
  }
}

function pickTheme(toggle, choice) {
  applyTheme(choice);
  saveTheme(choice);
  markPressed(toggle, choice);
}

function showThemeToggle(toggle) {
  markPressed(toggle, savedTheme());

  toggle.hidden = false;

  for (const option of toggle.querySelectorAll("[data-theme-choice]")) {
    const choice = option.dataset.themeChoice;

    option.addEventListener("click", () => pickTheme(toggle, choice));
  }
}

// ---- Run --------------------------------------------------------------

for (const block of document.querySelectorAll(".code[data-copy]")) {
  addCopyButton(block);
}

for (const toggle of document.querySelectorAll("[data-theme-toggle]")) {
  showThemeToggle(toggle);
}
