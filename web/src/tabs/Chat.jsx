import { useEffect, useRef, useState } from "preact/hooks";
import * as api from "../api.js";
import * as store from "../store.js";
import * as ui from "../ui.jsx";
import styles from "./tabs.module.css";

function chatModels() {
  const { data, defaults } = api.models.value;
  const names = data.filter((model) => model.role === "chat").map((model) => model.id);
  const saved = store.get("omni.chat.model", defaults.chat);
  if (saved && !names.includes(saved)) names.unshift(saved);
  return names;
}

function hasVision(name) {
  const model = api.models.value.data.find((entry) => entry.id === name);
  return model?.capabilities?.includes("vision") ?? false;
}

function toWire(message, shortenImages = false) {
  if (!message.images?.length) return { role: message.role, content: message.content };
  const images = message.images.map((url) => ({
    type: "image_url",
    image_url: { url: shortenImages ? api.shorten(url) : url },
  }));
  return { role: message.role, content: [{ type: "text", text: message.content }, ...images] };
}

function requestBody(model, system, messages, shortenImages = false) {
  const wire = messages.map((message) => toWire(message, shortenImages));
  if (system.trim()) wire.unshift({ role: "system", content: system.trim() });
  return { model: model || undefined, messages: wire, stream: true, stream_options: { include_usage: true } };
}

function timing(firstToken, elapsed, usage) {
  let text = `first token ${firstToken} ms · total ${elapsed} ms`;
  const generating = (elapsed - firstToken) / 1000;
  if (usage?.completion_tokens && generating > 0) text += ` · ${(usage.completion_tokens / generating).toFixed(0)} tok/s`;
  return text;
}

function Message({ message }) {
  const mine = message.role === "user";
  return (
    <div class={mine ? styles.mine : styles.theirs}>
      {message.images?.length > 0 && (
        <div class={styles.thumbs}>{message.images.map((url) => <img class={styles.thumb} src={url} alt="" />)}</div>
      )}
      <div class={mine ? styles.bubbleMine : styles.bubbleTheirs}>{message.content || <ui.Spinner />}</div>
    </div>
  );
}

export default function Chat() {
  const [model, setModel] = useState(() => store.get("omni.chat.model", ""));
  const [system, setSystem] = useState("");
  const [input, setInput] = useState("");
  const [images, setImages] = useState([]);
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [meta, setMeta] = useState("");
  const [last, setLast] = useState(null);
  const abort = useRef(null);
  const thread = useRef(null);
  const fileInput = useRef(null);

  const names = chatModels();
  const selected = model || names[0] || "";
  const vision = hasVision(selected);

  useEffect(() => {
    if (!vision) setImages([]);
  }, [vision]);

  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight });
  }, [messages]);

  const choose = (name) => {
    setModel(name);
    store.set("omni.chat.model", name);
  };

  const attach = async (files) => {
    const urls = await Promise.all([...files].map(api.readDataUrl));
    setImages((current) => [...current, ...urls]);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    const history = [...messages, { role: "user", content: text, images }];
    const body = requestBody(selected, system, history);
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setImages([]);
    setStreaming(true);
    setMeta("");
    abort.current = new AbortController();
    const start = performance.now();
    let reply = "";
    let firstToken = null;
    let usage = null;
    let status = 0;
    const show = (content) => setMessages([...history, { role: "assistant", content }]);
    try {
      const response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: abort.current.signal,
      });
      status = response.status;
      if (!response.ok) throw new Error(await api.errorText(response));
      for await (const event of api.sseEvents(response)) {
        if (event.usage) usage = event.usage;
        const delta = event.choices?.[0]?.delta?.content;
        if (!delta) continue;
        firstToken ??= api.since(start);
        reply += delta;
        show(reply);
      }
      const elapsed = api.since(start);
      api.record("chat", status, elapsed);
      setMeta(timing(firstToken ?? elapsed, elapsed, usage));
      setLast({ model: selected, message: { role: "assistant", content: reply }, usage });
    } catch (error) {
      if (error.name !== "AbortError") {
        api.record("chat", status || 0, api.since(start));
        show(`⚠ ${error.message}`);
      }
    } finally {
      setStreaming(false);
      abort.current = null;
    }
  };

  const onKeyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send();
  };

  const draft = { role: "user", content: input || "Hello!", images };
  const sample = messages.length ? messages : [draft];
  const curl = api.curlJson("/v1/chat/completions", requestBody(selected, system, sample, true), " \\\n  --no-buffer");

  return (
    <ui.Page title="Chat" subtitle="Streaming completions from the chat role, OpenAI wire format.">
      <ui.Columns>
        <ui.Card
          title="Conversation"
          meta={meta}
          actions={<ui.Button small variant="plain" disabled={!messages.length || streaming} onClick={() => { setMessages([]); setMeta(""); }}>Clear</ui.Button>}
          flush
        >
          <div class={styles.chat}>
            <div class={styles.toolbar}>
              <select aria-label="chat model" value={selected} onChange={(event) => choose(event.currentTarget.value)}>
                {names.length ? names.map((name) => <option value={name}>{name}</option>) : <option value="">no chat models</option>}
              </select>
              {vision && <span class={styles.badge}>vision</span>}
            </div>
            <div class={styles.systemPrompt}>
              <textarea rows={2} placeholder="System prompt (optional)" value={system} onInput={(event) => setSystem(event.currentTarget.value)} />
            </div>
            <div class={styles.thread} ref={thread}>
              {messages.length ? messages.map((message) => <Message message={message} />) : <ui.Empty>Say something. Replies stream token by token.</ui.Empty>}
            </div>
            <div class={styles.composer}>
              {images.length > 0 && (
                <div class={styles.thumbs}>
                  {images.map((url, index) => (
                    <button type="button" class={styles.thumbButton} title="Remove" onClick={() => setImages(images.filter((_, other) => other !== index))}>
                      <img class={styles.thumb} src={url} alt="attachment" />
                    </button>
                  ))}
                </div>
              )}
              <div class={styles.composerRow}>
                {vision && (
                  <>
                    <ui.Button variant="secondary" title="Attach images (this model has vision)" onClick={() => fileInput.current.click()}>+ image</ui.Button>
                    <input ref={fileInput} type="file" accept="image/*" multiple hidden onChange={(event) => { attach(event.currentTarget.files); event.currentTarget.value = ""; }} />
                  </>
                )}
                <textarea
                  class={styles.input}
                  rows={1}
                  placeholder="Message — Enter to send, Shift+Enter for a newline"
                  value={input}
                  onInput={(event) => setInput(event.currentTarget.value)}
                  onKeyDown={onKeyDown}
                />
                {streaming
                  ? <ui.Button variant="stop" onClick={() => abort.current?.abort()}>Stop</ui.Button>
                  : <ui.Button variant="primary" disabled={!input.trim()} onClick={send}>Send</ui.Button>}
              </div>
            </div>
          </div>
        </ui.Card>
        <ui.Card title="Last response">
          <ui.Code>{last ? JSON.stringify(last, null, 2) : "—"}</ui.Code>
          <ui.CurlBlock code={curl} />
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
