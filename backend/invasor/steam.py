"""Guarded calls to Steam's JS API (SteamClient) for the overlay and for modules.

STEAM TOUCHPOINT (see README "What Invasor relies on in Steam"): SteamClient lives in
Steam's SharedJSContext page, not in the windows Invasor is injected into, so calls
are evaluated there over CDP. Everything about it is optional: a missing function or
page is a clean `Unavailable` error, never a crash, and callers must have a plan B.
"""
import asyncio
import concurrent.futures
import json
import re
import struct

from .schema import InvalidArgument, Unavailable

# "Apps.SetCustomArtworkForApp": dotted names only, nothing private or magic.
PATH = re.compile(r"^[A-Z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9]*){1,4}$")
FORBIDDEN = {"constructor", "prototype"}
DEFAULT_TIMEOUT = 15
MAX_ARGS_BYTES = 176 << 20  # a 128 MB image as base64 is the biggest thing expected

CALL_JS = """(async () => {
  const path = %(path)s;
  let self = window.SteamClient, fn = window.SteamClient;
  for (const key of path.split('.')) { self = fn; fn = fn == null ? undefined : fn[key]; }
  if (typeof fn !== 'function') return {missing: true};
  const value = await fn.apply(self, %(args)s);
  return {value: value === undefined ? null : value};
})()"""


# ---------- notifications ----------
# STEAM TOUCHPOINT (see docs/API-Steam.md): a notification shown the way Steam shows an
# achievement. NotificationStore (SharedJSContext) takes a notification type and its
# protobuf message; both are Steam's public protobuf definitions
# (steammessages_clientnotificationtypes.proto): type 5 = k_EClientNotificationType_Achievement,
# message CAchievementNotification. No patching of Steam's UI. If any of it is gone, the
# call is Unavailable and the caller's plan B applies.
NOTIFY_TYPE_ACHIEVEMENT = 5
NOTIFY_TITLE_MAX = 64
NOTIFY_BODY_MAX = 256
NOTIFY_ICON_MAX = 512 << 10
ICON_URL = re.compile(r"^https://[^\s\"'<>]{1,2040}$")
ICON_DATA = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$")
# The sounds a notification can use, by name. Steam's toast config carries the sound as a number
# of its own sound enum (the one Decky Loader's `sound` option takes too); NOTIFY_JS swaps it
# for the one notification being shown. "" leaves it to Steam (the achievement sound), "none" is silent.
SOUNDS = {
    "trophy": 5,   # ToastAchievement
    "message": 4,  # ToastMessage
    "toast": 6,    # ToastMisc
    "chat": 3,     # ChatMessage
    "mention": 2,  # ChatMention
    "friend": 1,   # FriendMessage
    "online": 8,   # FriendOnline
    "ingame": 9,   # FriendInGame
    "none": 0,
}
CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

NOTIFY_JS = """(() => {
  const ns = window.NotificationStore;
  if (!ns || typeof ns.OnNotification !== 'function') return {missing: true};
  const id = typeof ns.m_nNextTestNotificationID === 'number'
    ? ns.m_nNextTestNotificationID++
    : 900000000 + (window.__invasorNotifyId = (window.__invasorNotifyId || 0) + 1);
  const sound = %(sound)s;
  // The sound of a visible toast is chosen by Steam when the toast shows: NotificationStore
  // .PlayNotificationSound(notification) looks the type's sound up through ChooseSound. Steam's
  // own methods ProcessNotification/OnNotification are MobX actions (read-only), so instead
  // that one method is taught, once, to use the sound remembered for this notification's id.
  // Steam still draws its own toast; only the sound number changes (0: none).
  if (sound !== null) {
    const sounds = window.__invasorSounds = window.__invasorSounds || {};
    const now = Date.now();
    for (const k of Object.keys(sounds)) if (now - sounds[k].at > 60000) delete sounds[k];
    if (ns.__invasorSound !== 2) {
      if (ns.__invasorSound) delete ns.PlayNotificationSound;  // an older version of this patch
      const play = ns.PlayNotificationSound, choose = ns.ChooseSound;
      if (typeof play === 'function' && typeof choose === 'function') {
        ns.PlayNotificationSound = function (n) {
          const mine = n && sounds[n.notificationID];
          if (!mine) return play.apply(this, arguments);
          delete sounds[n.notificationID];
          this.ChooseSound = (info, m) => choose.call(this, Object.assign({}, info, mine.sound ? {sound: mine.sound, playSound: true} : {playSound: false}), m);
          try { return play.apply(this, arguments); } finally { delete this.ChooseSound; }
        };
        if (ns.PlayNotificationSound !== play) ns.__invasorSound = 2;
      }
    }
    // If Steam changed and the sound can't be set, the notification still shows with Steam's own.
    if (ns.__invasorSound === 2) sounds[id] = {sound, at: now};
  }
  ns.OnNotification(id, %(type)d, new Uint8Array(%(bytes)s));
  return {value: true};
})()"""


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if not n:
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


def _string(num, text):
    data = text.encode("utf-8")
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def achievement_message(title, body, icon):
    """CAchievementNotification: 1 achievement_id, 2 appid, 3 name, 4 description,
    5 image_url, 6 achieved, 11 global_achieved_pct."""
    return (_string(1, "invasor") + _varint(2 << 3) + _varint(0) + _string(3, title) + _string(4, body)
            + _string(5, icon) + _varint(6 << 3) + _varint(1) + _varint(11 << 3 | 5) + struct.pack("<f", 100.0))


def check_notification(title, body="", icon="", sound=""):
    """(title, body, icon, sound) cleaned, or InvalidArgument: plain text, a title, short
    texts, an icon that's an https URL or an embedded image (data:image/…;base64), and
    optionally the name of a sound (see SOUNDS); `sound` comes back as Steam's number for it,
    or None to leave it to Steam."""
    if not all(isinstance(v, str) for v in (title, body, icon, sound)):
        raise InvalidArgument("title, body, icon and sound must be text")
    title, body, icon, sound = title.strip(), body.strip(), icon.strip(), sound.strip()
    if not title:
        raise InvalidArgument("a notification needs a title")
    if len(title) > NOTIFY_TITLE_MAX or len(body) > NOTIFY_BODY_MAX:
        raise InvalidArgument(f"title: {NOTIFY_TITLE_MAX} characters at most; body: {NOTIFY_BODY_MAX}")
    if CONTROL.search(title) or CONTROL.search(body):
        raise InvalidArgument("title and body must be plain text")
    if icon and not (ICON_URL.match(icon) or (len(icon) <= NOTIFY_ICON_MAX and ICON_DATA.match(icon))):
        raise InvalidArgument("icon: an https:// URL, or data:image/png|jpeg|gif|webp;base64,… up to 512 KB")
    if sound and sound not in SOUNDS:
        raise InvalidArgument(f"sound: one of {', '.join(SOUNDS)}")
    return title, body, icon, SOUNDS.get(sound)


class SteamBridge:
    def __init__(self, evaluate_shared):
        self._evaluate = evaluate_shared  # async (expression, timeout) -> value
        self.loop = None  # the service's event loop, for calls from worker threads

    async def call(self, path, args=(), timeout=DEFAULT_TIMEOUT):
        """SteamClient.<path>(*args); its JSON-able result. Raises InvalidArgument for a
        bad path/args, Unavailable if Steam (or that function) isn't there."""
        if not isinstance(path, str) or not PATH.match(path) or FORBIDDEN & set(path.split(".")):
            raise InvalidArgument(f"invalid SteamClient path {path!r}")
        try:
            encoded = json.dumps(list(args))
        except (TypeError, ValueError) as e:
            raise InvalidArgument(f"SteamClient.{path}: arguments must be JSON ({e})") from None
        if len(encoded) > MAX_ARGS_BYTES:
            raise InvalidArgument(f"SteamClient.{path}: arguments too large")
        try:
            res = await self._evaluate(CALL_JS % {"path": json.dumps(path), "args": encoded}, timeout)
        except Unavailable:
            raise
        except Exception as e:
            raise Unavailable(f"SteamClient.{path} failed: {e}") from None
        if not isinstance(res, dict) or res.get("missing"):
            raise Unavailable(f"SteamClient.{path} isn't available")
        return res.get("value")

    async def notify(self, title, body="", icon="", timeout=DEFAULT_TIMEOUT, sound=""):
        """Show a notification as Steam shows an achievement, with the chosen
        Steam sound. InvalidArgument for bad input, Unavailable if Steam
        can't show it."""
        title, body, icon, sound = check_notification(title, body, icon, sound)
        data = list(achievement_message(title, body, icon))
        try:
            res = await self._evaluate(NOTIFY_JS % {"type": NOTIFY_TYPE_ACHIEVEMENT, "bytes": json.dumps(data),
                                      "sound": json.dumps(sound)}, timeout)
        except Unavailable:
            raise
        except Exception as e:
            raise Unavailable(f"Steam couldn't show the notification: {e}") from None
        if not isinstance(res, dict) or res.get("missing"):
            raise Unavailable("Steam's notifications aren't available")
        return True

    def notify_sync(self, title, body="", icon="", timeout=DEFAULT_TIMEOUT, sound=""):
        """notify() for plain (threaded) module methods."""
        return self._sync(self.notify(title, body, icon, timeout, sound), timeout)

    def _sync(self, coro, timeout):
        if self.loop is None:
            coro.close()
            raise Unavailable("SteamClient bridge not ready")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            coro.close()
            raise RuntimeError("this blocks: in an async method use the async variant")
        return self._wait(asyncio.run_coroutine_threadsafe(coro, self.loop), timeout)

    @staticmethod
    def _wait(future, timeout):
        """The result, or Unavailable when Steam doesn't answer in time: callers' plan B
        catches Unavailable, never a bare TimeoutError."""
        try:
            return future.result(timeout + 5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise Unavailable(f"Steam didn't answer in {timeout}s") from None

    def call_sync(self, path, *args, timeout=DEFAULT_TIMEOUT):
        """call() for plain (threaded) module methods. Not from the event loop itself:
        async methods use `await ctx.steam_call_async(...)`."""
        if self.loop is None:
            raise Unavailable("SteamClient bridge not ready")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            raise RuntimeError("ctx.steam_call() blocks: in an async method use `await ctx.steam_call_async(...)`")
        return self._wait(asyncio.run_coroutine_threadsafe(self.call(path, args, timeout), self.loop), timeout)
