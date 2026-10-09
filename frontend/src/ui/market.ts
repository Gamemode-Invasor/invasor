// The module market, in a big window (⚙ Settings › Install module › Open the Market): a card per
// module of the organisation's repositories, and a button to install or update it. The backend
// (backend/invasor/market.py) lists them and does the download, checksum and install.

import type { Api } from "../api";
import { ui } from "./controls";

export interface MarketCard {
  repo: string;
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  min_core: string | null;
  installed_version: string | null;
  /** What installing it does: null (not installed), same, update or downgrade. */
  change: "same" | "update" | "downgrade" | null;
  compatible: boolean;
  problem: string | null;
}

interface MarketList {
  modules: MarketCard[];
  notes: string[];
  /** Where the list came from: the published catalog file, or GitHub asked directly. */
  source?: "file" | "live";
  /** When the catalog file was made (null when GitHub was asked directly). */
  generated?: string | null;
}

export interface MarketDeps {
  api: Api;
  toast(message: string, kind?: "ok" | "error"): void;
  /** Modules were installed: rebuild the module tabs. */
  onModulesChanged(): Promise<unknown>;
  confirm?(question: string, o: { ok: string }): Promise<boolean>;
}

const errorText = (e: unknown) => (e as Error)?.message ?? String(e);

/** What the card's button does, from what is installed. */
export function actionFor(card: MarketCard): { label: string; verb: string } {
  switch (card.change) {
    case null:
      return { label: "Install", verb: "Install" };
    case "same":
      return { label: "Reinstall", verb: "Reinstall" };
    case "downgrade":
      return { label: `Downgrade to ${card.version}`, verb: "Downgrade" };
    default:
      return { label: `Update to ${card.version}`, verb: "Update" };
  }
}

export async function renderMarket(el: HTMLElement, deps: MarketDeps) {
  const { api, toast } = deps;
  const confirm = deps.confirm ?? ((question, o) => ui.confirm(question, o));
  let busy = false;

  async function load(refresh = false) {
    el.replaceChildren(ui.info("Looking for modules…"));
    let res: MarketList;
    try {
      res = await api.call("core", "market_list", { refresh });
    } catch (e) {
      el.replaceChildren(
        ui.info(`Couldn't load the market: ${errorText(e)}`),
        ui.button({ label: "Try again", onClick: () => void load(true) }),
      );
      return;
    }
    draw(res);
  }

  async function install(card: MarketCard) {
    if (busy) return;
    const { verb } = actionFor(card);
    const by = card.author ? ` by ${card.author}` : "";
    const question =
      card.change === null
        ? `Install ${card.name} ${card.version}${by}, from ${card.repo}? It runs with your user's permissions.`
        : `${verb} ${card.name} ${card.installed_version} → ${card.version}${by}, from ${card.repo}?`;
    if (!(await confirm(question, { ok: verb }))) return;
    busy = true;
    toast(`Downloading ${card.name}…`);
    try {
      await api.call("core", "market_install", { repo: card.repo });
      toast(`${card.name} ${card.version} installed`);
    } catch (e) {
      busy = false;
      return toast(`Not installed: ${errorText(e)}`, "error");
    }
    busy = false;
    await deps.onModulesChanged();
    await load();
  }

  function draw(res: MarketList) {
    const top = ui.button({ label: "Refresh", onClick: () => void load(true) });
    const grid = document.createElement("div");
    grid.className = "market-grid";
    for (const card of res.modules) {
      const box = document.createElement("div");
      box.className = "market-card";
      const title = document.createElement("h3");
      title.textContent = card.name;
      const meta = document.createElement("p");
      meta.className = "market-meta";
      meta.textContent = [`v${card.version}`, card.author, card.repo].filter(Boolean).join(" · ");
      const text = document.createElement("p");
      text.className = "market-text";
      text.textContent = card.description || "No description.";
      const status = document.createElement("p");
      status.className = "market-status";
      status.textContent = card.problem
        ? card.problem
        : card.change === null
          ? ""
          : card.change === "same"
            ? `Installed (${card.installed_version})`
            : card.change === "downgrade"
              ? `Installed: ${card.installed_version}, newer than the market's`
              : `Installed: ${card.installed_version}`;
      const act = actionFor(card);
      const button = ui.button({ label: act.label, onClick: () => void install(card), hint: `A ${act.verb.toLowerCase()}` });
      if (!card.compatible) button.setDisabled(true, card.problem ?? "Needs a newer Invasor");
      box.append(title, meta, text, status, button);
      grid.append(box);
    }
    const rows: HTMLElement[] = [top];
    if (res.modules.length) rows.push(grid);
    else rows.push(ui.info("No modules available right now."));
    if (res.notes.length) rows.push(ui.info(`Not shown: ${res.notes.join("; ")}`));
    const made = res.generated ? new Date(res.generated) : null;
    if (made && !Number.isNaN(made.getTime())) rows.push(ui.info(`Catalog updated ${made.toLocaleString()}`));
    el.replaceChildren(...rows);
  }

  await load();
}
