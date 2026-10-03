// SPDX-License-Identifier: MIT
// Free to copy as the starting point of your own module (see LICENSES/MIT.txt); delete these two lines.
import { defineModule, ui } from "invasor";

// ui.ts is optional: without it, a module with "settings" in module.json gets a tab
// with just its settings form. Write one for anything more, as here.
//
// Each module is a tab in the panel (L1/R1). Use `render` for a single page, or
// `tabs` for sub-tabs (L2/R2) as here. Content is built the first time it's shown.
export default defineModule({
  tabsAlign: "justify", // sub-tab row: "start" (default) | "center" | "end" | "justify"
  tabs: [
    {
      label: "General",
      async render(el, ctx) {
        el.append(
          ui.info(await ctx.call<string>("hello", { name: "Invasor" })),
          // The form declared in module.json: validated and saved by the backend.
          await ui.settingsForm(ctx),
        );
      },
    },
    {
      label: "Advanced",
      render(el, ctx) {
        el.append(
          ui.button({
            label: "Delete data",
            onClick: async () => {
              if (await ui.confirm("Are you sure?")) ctx.toast("Deleted");
            },
          }),
        );
      },
    },
  ],
  // Optional hooks: pause timers when the tab isn't visible, react to game changes.
  onShow: () => {},
  onHide: () => {},
  onGameChange(game) {
    console.log("[example] game", game);
  },
});
// Expanded views: ui.windowButton(ctx, { open: () => ({ title, tabs: [...] }) })
// (locks itself where there's no room), or ctx.openWindow / ctx.canOpenWindow;
// image previews: ui.imageGrid({ items, aspect: "grid", onActivate }). See Demo › Windows.
// Custom controls: mark them data-nav, give a data-hint, and listen to
// "invasor:button" (preventDefault() what you handle). See module-api.ts.
