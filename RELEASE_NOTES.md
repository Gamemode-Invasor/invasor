New
- A task started with `ctx.task(label)` now shows a notification when it starts and when it ends (with the final text as its body), with the `toast` sound unless the module picks another one: `ctx.task(label, notify=True, sound="toast")`. A module can turn it off with `notify=False`
- When Steam starts, Invasor looks for a new Invasor version (as before, then daily) and for newer versions of your installed modules in the Market. A new Invasor version shows its own notification; module updates show a single generic one. A version older than the installed one is never announced

---
