/** Incremental SSE decoder: consumes each event once, including split UTF-8/CRLF. */
export async function consumeSSE(response, onEvent, signal) {
  if (!response.body) throw new Error("浏览器未提供可读取的响应流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "", data = [];
  function line(value) {
    value = value.replace(/\r$/, "");
    if (!value) {
      if (data.length) {
        onEvent(JSON.parse(data.join("\n")));
        data = [];
      }
    } else if (value.startsWith("data:")) data.push(value.slice(5).replace(/^ /, ""));
  }
  function consume() {
    let i;
    while ((i = pending.indexOf("\n")) !== -1) {
      line(pending.slice(0, i));
      pending = pending.slice(i + 1);
    }
    if (pending.length > 2000000) throw new Error("响应内容超出单个事件的大小限制。");
  }
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("已停止", "AbortError");
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      consume();
    }
    pending += decoder.decode();
    consume();
    if (pending) line(pending);
    line("");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
