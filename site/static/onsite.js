// The onsite site's one script, linked by site/layouts/head.ejs
// with `defer`. Every page reads and works without it. It adds three
// extras:
//
// - A Copy button on shell and YAML code blocks.
// - The theme toggle in the masthead. It has three choices: follow
//   the system, light, or dark. Light and dark are remembered.
// - On a row of screenshots, the two step buttons, the arrow keys,
//   and a larger view of the picture a reader picks.

// ---- Constants --------------------------------------------------------

const COPY_LABEL = "Copy";

const COPIED_LABEL = "Copied";

const FAILED_LABEL = "Select and copy";

const RESET_AFTER_MS = 2400;

const THEME_KEY = "onsite-theme";

const THEME_SYSTEM = "system";

const THEME_PICKS = ["light", "dark"];

// A scroll position can rest a fraction of a pixel off a card's
// edge, so "past this card" allows for that much.
const SHOTS_SLACK_PX = 2;

// Held with a click, these mean "open the link somewhere else".
const CLICK_MODIFIERS = ["metaKey", "ctrlKey", "shiftKey", "altKey"];

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

// ---- Screenshots ------------------------------------------------------

// Where the row must be scrolled to for each card to sit at its
// left edge.
function shotStarts(row) {
  const edge = row.getBoundingClientRect().left - row.scrollLeft;

  return [...row.children].map((card) => {
    return card.getBoundingClientRect().left - edge;
  });
}

// No speed is given here. The stylesheet sets it, and sets none for
// a reader who asked for less motion.
function scrollShots(row, left) {
  if (left === undefined) {
    return;
  }

  row.scrollTo({ left });
}

function showNextShot(row) {
  const here = row.scrollLeft + SHOTS_SLACK_PX;

  scrollShots(row, shotStarts(row).find((start) => start > here));
}

function showPreviousShot(row) {
  const here = row.scrollLeft - SHOTS_SLACK_PX;

  scrollShots(row, shotStarts(row).findLast((start) => start < here));
}

// The buttons show only when there is more row than fits. At an end
// one is marked as spent, not disabled: a disabled button would drop
// the keyboard's focus the moment the row got there.
function markShotSteps(row, previous, next) {
  const end = row.scrollWidth - row.clientWidth;
  const fits = end <= SHOTS_SLACK_PX;
  const atStart = row.scrollLeft <= SHOTS_SLACK_PX;
  const atEnd = row.scrollLeft >= end - SHOTS_SLACK_PX;

  previous.hidden = fits;
  next.hidden = fits;
  previous.setAttribute("aria-disabled", String(atStart));
  next.setAttribute("aria-disabled", String(atEnd));
}

function focusShot(card) {
  const target = card?.querySelector("[data-shots-open], video");

  if (!target) {
    return;
  }

  target.focus();
}

// Left and right move from one picture to the next. A film keeps
// the arrow keys for itself: there they wind it back and forward.
function stepShotFocus(event) {
  if (!event.target.matches("[data-shots-open]")) {
    return;
  }

  const card = event.target.closest(".shots__card");

  if (event.key === "ArrowRight") {
    event.preventDefault();
    focusShot(card.nextElementSibling);
  }

  if (event.key === "ArrowLeft") {
    event.preventDefault();
    focusShot(card.previousElementSibling);
  }
}

function fillShotDialog(dialog, link) {
  const small = link.querySelector("img");
  const large = dialog.querySelector("[data-shots-large]");
  const words = dialog.querySelector("[data-shots-words]");
  const caption = link.closest("figure").querySelector("figcaption");

  large.src = link.href;
  large.alt = small.alt;
  large.setAttribute("width", small.getAttribute("width"));
  large.setAttribute("height", small.getAttribute("height"));
  words.textContent = caption.textContent.trim();
}

// A plain click or Enter opens the picture larger. A click with a
// key held is left alone, so "open in a new tab" still works.
function openShot(event, dialog, link) {
  if (CLICK_MODIFIERS.some((key) => event[key])) {
    return;
  }

  event.preventDefault();
  fillShotDialog(dialog, link);
  dialog.showModal();
}

// A click on the backdrop lands on the dialog itself. That and a
// click on the picture close it, as they do in the app. Escape and
// the Close button are the browser's own.
function closeShotOnClick(event, dialog) {
  if (event.target === dialog) {
    dialog.close();
  }

  if (event.target.matches("[data-shots-large]")) {
    dialog.close();
  }
}

function setUpShotDialog(shots) {
  const dialog = shots.querySelector("[data-shots-dialog]");

  // An old browser with no <dialog> keeps the plain links.
  if (typeof dialog.showModal !== "function") {
    return;
  }

  for (const link of shots.querySelectorAll("[data-shots-open]")) {
    link.addEventListener("click", (event) => {
      openShot(event, dialog, link);
    });
  }

  dialog.addEventListener("click", (event) => {
    closeShotOnClick(event, dialog);
  });
}

function setUpShots(shots) {
  const row = shots.querySelector("[data-shots-row]");
  const previous = shots.querySelector('[data-shots-step="previous"]');
  const next = shots.querySelector('[data-shots-step="next"]');
  const mark = () => markShotSteps(row, previous, next);

  previous.addEventListener("click", () => showPreviousShot(row));
  next.addEventListener("click", () => showNextShot(row));
  row.addEventListener("keydown", stepShotFocus);
  row.addEventListener("scroll", mark, { passive: true });
  addEventListener("resize", mark);

  mark();
  setUpShotDialog(shots);
}

// ---- Run --------------------------------------------------------------

for (const block of document.querySelectorAll(".code[data-copy]")) {
  addCopyButton(block);
}

for (const toggle of document.querySelectorAll("[data-theme-toggle]")) {
  showThemeToggle(toggle);
}

for (const shots of document.querySelectorAll("[data-shots]")) {
  setUpShots(shots);
}
