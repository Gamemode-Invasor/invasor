// On-screen keyboard, rendered inside the panel. Pure DOM + our own gamepad events:
// deliberately not Steam's keyboard, so it keeps working whatever Steam changes.
//
// Driven by the owning control through handle(button): D-pad moves, A types,
// X deletes, Y toggles caps, B (or the OK key) finishes.

// Two layers, switched with the "#+=" / "abc" key. Caps also applies to accents.
const LETTERS = ["1234567890", "qwertyuiop", "asdfghjklñ", "zxcvbnm,.-"];
const SYMBOLS = ["!¡?¿@#$%&*", "-_.,:;'\"/\\", "()[]{}=+<>", "áéíóúüç€~|"];

export interface MiniKeyboard {
  el: HTMLElement;
  /** Handle a gamepad button; returns true if consumed. */
  handle(button: string): boolean;
}

export function createKeyboard(
  input: HTMLInputElement,
  onType: () => void,
  onDone: () => void,
  /** Optional extra key on the bottom row (the password field's show/hide). */
  extra?: { label: () => string; run: () => void },
): MiniKeyboard {
  const el = document.createElement("div");
  el.className = "kbd";
  let caps = false;
  let symbols = false;
  let row = 1;
  let col = 0;
  const grid: HTMLElement[][] = [];

  function render() {
    el.innerHTML = "";
    grid.length = 0;
    const rows = symbols ? SYMBOLS : LETTERS;
    [...rows.map((r) => [...r]), special()].forEach((keys, r) => {
      const line = document.createElement("div");
      line.className = "kbd-row";
      grid.push(
        keys.map((k, c) => {
          const key = document.createElement("span");
          key.className = "kbd-key" + (k.length > 1 ? " wide" : "");
          key.textContent = k.length === 1 && caps ? k.toUpperCase() : k;
          // Touch works too.
          key.addEventListener("click", (ev) => {
            ev.stopPropagation();
            row = r;
            col = c;
            press();
          });
          line.appendChild(key);
          return key;
        }),
      );
      el.appendChild(line);
    });
    highlight();
  }

  function special(): string[] {
    return ["⇧", symbols ? "abc" : "#+=", "space", "⌫", ...(extra ? [extra.label()] : []), "OK"];
  }

  function highlight() {
    grid.flat().forEach((k) => k.classList.remove("on"));
    col = Math.min(col, grid[row].length - 1);
    grid[row][col].classList.add("on");
  }

  function type(text: string) {
    // Setting .value from code ignores maxlength: enforce it here.
    if (input.maxLength > 0 && input.value.length >= input.maxLength) return;
    input.value += text;
    onType();
  }

  function backspace() {
    input.value = input.value.slice(0, -1);
    onType();
  }

  function press() {
    const rows = symbols ? SYMBOLS : LETTERS;
    const k = row < rows.length ? [...rows[row]][col] : special()[col];
    if (k === "⇧") {
      caps = !caps;
      return render();
    }
    if (k === "#+=" || k === "abc") {
      symbols = !symbols;
      return render();
    }
    if (k === "space") return type(" ");
    if (k === "⌫") return backspace();
    if (k === "OK") return onDone();
    if (extra && k === extra.label()) {
      extra.run();
      return render();
    }
    type(caps ? k.toUpperCase() : k);
  }

  render();

  return {
    el,
    handle(button) {
      switch (button) {
        case "UP":
          row = Math.max(0, row - 1);
          break;
        case "DOWN":
          row = Math.min(grid.length - 1, row + 1);
          break;
        case "LEFT":
          col = Math.max(0, col - 1);
          break;
        case "RIGHT":
          col = Math.min(grid[row].length - 1, col + 1);
          break;
        case "A":
          press();
          return true;
        case "X":
          backspace();
          return true;
        case "Y":
          caps = !caps;
          render();
          return true;
        case "B":
          onDone();
          return true;
        default:
          return false;
      }
      highlight();
      return true;
    },
  };
}
