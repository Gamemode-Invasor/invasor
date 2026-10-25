Pre-release of 0.3.0: more for modules, nothing removed. Modules that use the new features set "min_core": "0.3.0-rc1" to run on this pre-release (a release candidate counts as older than its release).

New for modules
- Steam's folders and files: the Steam folder, every library (also on SD cards and USB drives), the account in use, a game's compatdata and shader cache, and a reader for Steam's VDF files
- Unpacking zip, tar, 7z and tar.zst archives safely, the same rules for every format
- Downloads over https: checked against a checksum, or the user is asked first in the Tasks panel (Accept / Deny)
- Tasks can ask the user a question; the handle's dot shows when one is waiting
- Steam: add a non-Steam shortcut, remove the ones the module added, read and set launch options (read back afterwards to check Steam took them), open a web page in Steam, and Steam's language
- In a module's page: copy text to the clipboard, and a file or folder picker window (hidden files on request)
- Demo shows all of it

Fixes
- Updates and the Market: a redirect that leaves https is refused before it is followed, a failed download leaves no partial file, and the catalog tool no longer sends its GitHub token to another host
- Game names are found in libraries on SD cards and USB drives too

Internal
- One shared download client and one archive unpacker for the core and modules

---
