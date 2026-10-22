Fixes
- Security: choosing a module zip in Settings > Install module no longer runs any of its code before you press Install, and only the zip that was shown is installed
- The notification of a task that Steam can't show no longer fails silently: it is written to the log
- Games are also found when Steam lives in ~/.steam/steam (before, only ~/.local/share/Steam was looked at)
- Lighter background work: each running process is read once instead of on every check

Internal
- Code cleanup in the core with no change for modules: shared helpers, the core API split into sections, unused code removed
- Docs: all modules listed, Popy and Cometa replace Noty, the Market catalog refreshes every 12 hours

---
