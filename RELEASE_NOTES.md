rc1:

- A task started with `ctx.task(label)` now shows a notification when it starts and when it ends (with the final text as its body), with the `toast` sound unless the module picks another one: `ctx.task(label, notify=True, sound="toast")`. A module can turn it off with `notify=False`

---
