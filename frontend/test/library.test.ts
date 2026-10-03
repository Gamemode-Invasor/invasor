import assert from "node:assert/strict";
import { test } from "node:test";
import { appidFromArt } from "../src/library";

// The shapes of real URLs seen in Game Mode (2026-10-01), with made-up ids.
test("appid from Steam artwork", () => {
  assert.equal(appidFromArt("/assets/2100/library_600x900.jpg?c=738397192"), "2100");
  assert.equal(appidFromArt("https://steamloopback.host/assets/1245620/library_600x900.jpg"), "1245620");
});

test("appid from custom art (also non-Steam shortcuts)", () => {
  assert.equal(appidFromArt("/customimages/3000000001p.jpg?v=1700000001"), "3000000001");
  assert.equal(appidFromArt("/customimages/2500000002p.png?v=1700000002"), "2500000002");
  assert.equal(appidFromArt("https://steamloopback.host/customimages/2500000002_hero.png"), "2500000002");
  assert.equal(appidFromArt("/customimages/2600000003.png"), "2600000003");
});

test("no appid", () => {
  assert.equal(appidFromArt("https://avatars.steamstatic.com/0123456789ab_full.jpg"), undefined);
  assert.equal(appidFromArt("/assets/abc/library.jpg"), undefined);
  assert.equal(appidFromArt("/assets/12x/library.jpg"), undefined);
  assert.equal(appidFromArt(""), undefined);
  assert.equal(appidFromArt(null), undefined);
});
