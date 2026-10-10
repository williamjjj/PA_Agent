import { test } from "node:test";
import assert from "node:assert/strict";
import { consumeSSE } from "../../public/stream.mjs";

function response(chunks) {
  const bytes = chunks.map((c) => typeof c === "string" ? new TextEncoder().encode(c) : c);
  return new Response(new ReadableStream({ start(controller) {
    bytes.forEach((b) => controller.enqueue(b)); controller.close();
  }}));
}
test("events are consumed once across fragmented UTF-8 and CRLF", async () => {
  const wire = new TextEncoder().encode(': heartbeat\r\n\r\ndata: {"event":"token","text":"中文"}\r\n\r\ndata: {"event":"result","data":{}}\n\n');
  const events = [];
  await consumeSSE(response(Array.from(wire, (b) => new Uint8Array([b]))), (e) => events.push(e));
  assert.equal(events.length, 2); assert.equal(events[0].text, "中文"); assert.equal(events[1].event, "result");
});
test("error events reach the consumer and stop reading", async () => {
  await assert.rejects(() => consumeSSE(response(['data: {"event":"error","message":"failed"}\n\n']), (e) => { if (e.event === "error") throw new Error(e.message); }), /failed/);
});
test("EOF does not invent a successful result", async () => {
  const events = [];
  await consumeSSE(response(['data: {"event":"stage","stage":"Stage1Started"}\n\n']), (e) => events.push(e));
  assert.equal(events.length, 1); assert.ok(!events.some((e) => e.event === "result"));
});
test("trailing JSON and multiline data are handled", async () => {
  const events = [];
  await consumeSSE(response(['data: {"event":\ndata: "result","data":{}}']), (e) => events.push(e));
  assert.equal(events[0].event, "result");
});
