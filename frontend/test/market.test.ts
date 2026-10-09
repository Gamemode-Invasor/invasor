import { settle } from "./dom";
import assert from "node:assert/strict";
import { test } from "node:test";
import { actionFor, renderMarket, type MarketCard, type MarketDeps } from "../src/ui/market";

const card = (over: Partial<MarketCard> = {}): MarketCard => ({
  repo: "invasor-patito",
  id: "patito",
  name: "Patito",
  version: "0.2.1",
  description: "Frame generation profiles",
  author: "Someone",
  min_core: null,
  installed_version: null,
  change: null,
  compatible: true,
  problem: null,
  ...over,
});

function setup(list: unknown, o: { confirm?: boolean; installError?: string } = {}) {
  const calls: string[] = [];
  const toasts: string[] = [];
  let changed = 0;
  const deps: MarketDeps = {
    api: {
      call: async (_f: string, method: string, args?: Record<string, unknown>) => {
        calls.push(`${method}${args ? " " + JSON.stringify(args) : ""}`);
        if (method === "market_list") {
          if (list instanceof Error) throw list;
          return list;
        }
        if (o.installError) throw new Error(o.installError);
        return {};
      },
    } as unknown as MarketDeps["api"],
    toast: (m) => void toasts.push(m),
    onModulesChanged: async () => void changed++,
    confirm: async () => o.confirm ?? true,
  };
  const el = document.createElement("div");
  return { el, deps, calls, toasts, changed: () => changed };
}

const buttons = (el: HTMLElement) => [...el.querySelectorAll<HTMLButtonElement>("button")];

test("a card per module, with its name, version, author and description", async () => {
  const { el, deps } = setup({ modules: [card(), card({ id: "pescao", repo: "invasor-pescao", name: "Pescao" })], notes: [] });
  await renderMarket(el, deps);
  const cards = [...el.querySelectorAll(".market-card")];
  assert.equal(cards.length, 2);
  assert.match(cards[0].textContent ?? "", /Patito/);
  assert.match(cards[0].textContent ?? "", /v0\.2\.1 · Someone · invasor-patito/);
  assert.match(cards[0].textContent ?? "", /Frame generation profiles/);
});

test("the button says install, reinstall, update or downgrade", () => {
  assert.equal(actionFor(card()).label, "Install");
  assert.equal(actionFor(card({ installed_version: "0.2.1", change: "same" })).label, "Reinstall");
  assert.equal(actionFor(card({ installed_version: "0.2.0", change: "update" })).label, "Update to 0.2.1");
  assert.equal(actionFor(card({ installed_version: "0.3.0", change: "downgrade" })).label, "Downgrade to 0.2.1");
});

test("a newer installed version says so and asks before going back", async () => {
  const { el, deps } = setup({ modules: [card({ installed_version: "0.3.0", change: "downgrade" })], notes: [] });
  const asked: string[] = [];
  deps.confirm = async (q) => (asked.push(q), false);
  await renderMarket(el, deps);
  assert.match(el.querySelector(".market-status")?.textContent ?? "", /0\.3\.0, newer than the market's/);
  buttons(el).find((b) => b.textContent === "Downgrade to 0.2.1")!.click();
  await settle();
  assert.match(asked[0], /Downgrade Patito 0\.3\.0 → 0\.2\.1/);
});

test("installing asks first, installs the repository, refreshes the tabs and reloads the list", async () => {
  const { el, deps, calls, toasts, changed } = setup({ modules: [card()], notes: [] });
  await renderMarket(el, deps);
  buttons(el).find((b) => b.textContent === "Install")!.click();
  await settle();
  await settle();
  assert.deepEqual(calls.filter((c) => c.startsWith("market_install")), ['market_install {"repo":"invasor-patito"}']);
  assert.equal(changed(), 1);
  assert.equal(calls.filter((c) => c.startsWith("market_list")).length, 2);
  assert.ok(toasts.some((t) => /Patito 0\.2\.1 installed/.test(t)));
});

test("declining the question installs nothing", async () => {
  const { el, deps, calls } = setup({ modules: [card()], notes: [] }, { confirm: false });
  await renderMarket(el, deps);
  buttons(el).find((b) => b.textContent === "Install")!.click();
  await settle();
  assert.ok(!calls.some((c) => c.startsWith("market_install")));
});

test("a failed install says why and doesn't reload", async () => {
  const { el, deps, calls, toasts, changed } = setup({ modules: [card()], notes: [] }, { installError: "checksum doesn't match" });
  await renderMarket(el, deps);
  buttons(el).find((b) => b.textContent === "Install")!.click();
  await settle();
  await settle();
  assert.ok(toasts.some((t) => /Not installed: checksum doesn't match/.test(t)));
  assert.equal(changed(), 0);
  assert.equal(calls.filter((c) => c.startsWith("market_list")).length, 1);
});

test("a module that needs a newer Invasor can't be installed", async () => {
  const { el, deps } = setup({ modules: [card({ compatible: false, problem: "needs Invasor 9.0.0 or newer (this is 0.1.4)" })], notes: [] });
  await renderMarket(el, deps);
  assert.match(el.querySelector(".market-status")?.textContent ?? "", /needs Invasor 9\.0\.0/);
  assert.ok(buttons(el).find((b) => b.textContent === "Install")!.classList.contains("disabled"));
});

test("no network: the error and a way to retry", async () => {
  const { el, deps, calls } = setup(new Error("couldn't read the list of modules: offline"));
  await renderMarket(el, deps);
  assert.match(el.textContent ?? "", /Couldn't load the market: couldn't read the list of modules/);
  buttons(el).find((b) => b.textContent === "Try again")!.click();
  await settle();
  assert.equal(calls.filter((c) => c.startsWith("market_list")).length, 2);
  assert.ok(calls[1].includes('"refresh":true'));
});

test("repositories without a card are listed as notes, an empty market says so", async () => {
  const { el, deps } = setup({ modules: [], notes: ["invasor-x: has no release yet"] });
  await renderMarket(el, deps);
  assert.match(el.textContent ?? "", /No modules available/);
  assert.match(el.textContent ?? "", /Not shown: invasor-x: has no release yet/);
});

test("when the catalog file's date is known it is shown; asked directly, nothing", async () => {
  const a = setup({ modules: [card()], notes: [], source: "file", generated: "2026-10-09T12:07:00Z" });
  await renderMarket(a.el, a.deps);
  assert.match(a.el.textContent ?? "", /Catalog updated .*2026/);
  const b = setup({ modules: [card()], notes: [], source: "live", generated: null });
  await renderMarket(b.el, b.deps);
  assert.doesNotMatch(b.el.textContent ?? "", /Catalog updated/);
});
