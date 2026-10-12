import assert from "node:assert/strict";
import { test } from "node:test";
import { makeApi } from "../src/api";

const api = makeApi({ apiPort: 1, token: "t", role: "main" });
const answer = (status: number, body: string) => {
  (globalThis as { fetch: unknown }).fetch = async () => new Response(body, { status });
};

test("api: the result of a good answer", async () => {
  answer(200, '{"result": 5}');
  assert.equal(await api.call("core", "x"), 5);
});

test("api: the backend's error message, or the HTTP status when the body isn't JSON", async () => {
  answer(400, '{"error": "bad"}');
  await assert.rejects(api.call("core", "x"), /bad/);
  answer(502, "<html>Bad gateway</html>");
  await assert.rejects(api.call("core", "x"), /HTTP 502/);
});

test("api: an answer without result is an error, not undefined", async () => {
  answer(200, "{}");
  await assert.rejects(api.call("core", "x"), /no result/);
});
