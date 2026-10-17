import { signal } from "@preact/signals";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { errorText, readDataUrl, record, sseEvents } from "../api/client.js";
import { chatStream } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { curlJson } from "../lib/curl.js";
import { shorten, since } from "../lib/format.js";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Panel, RequestBody, Turn, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import styles from "./Chat.module.css";

function chatModels() {
  const { data, defaults } = models.value;
  const names = data.filter((model) => model.role === "chat").map((model) => model.id);
  const saved = store.get("omni.chat.model", defaults.chat);
  if (saved && !names.includes(saved)) names.unshift(saved);
  return names;
}

function hasVision(name) {
  return models.value.data.find((entry) => entry.id === name)?.capabilities?.includes("vision") ?? false;
}

function toWire(message, shortenImages = false) {
  if (!message.images?.length) return { role: message.role, content: message.content };
  const images = message.images.map((url) => ({
    type: "image_url",
    image_url: { url: shortenImages ? shorten(url) : url },
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

/** Flat messages → one turn per user/assistant exchange. */
function toTurns(messages) {
  const turns = [];
  for (const message of messages) {
    if (message.role === "user" || !turns.length) {
      turns.push({ id: message.id, user: message.role === "user" ? message : null, assistant: null });
    } else {
      turns[turns.length - 1].assistant = message;
    }
  }
  return turns;
}

/** The text of a wire message, whether its content is a string or a parts array. */
function textOf(message) {
  if (typeof message?.content === "string") return message.content;
  if (Array.isArray(message?.content)) return message.content.find((part) => part.type === "text")?.text ?? "";
  return "";
}

function Attachments({ images }) {
  if (!images?.length) return null;
  return (
    <div class={styles.thumbs}>
      {images.map((url, index) => <img key={index} class={styles.thumb} src={url} alt="" />)}
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
  const [view, setView] = useState("ui");
  const abort = useRef(null);
  const fileInput = useRef(null);
  const stream = useMemo(() => signal(""), []);
  const turns = useMemo(() => toTurns(messages), [messages]);
  const turnState = useTurnState();

  const names = chatModels();
  const selected = model || names[0] || "";
  const vision = hasVision(selected);
  const chat = runtime.value.roles.chat;

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { if (!vision) setImages([]); }, [vision]);

  const draft = input ? [{ id: "draft", role: "user", content: input, images }] : [];
  const preview = requestBody(selected, system, [...messages, ...draft], true);
  const json = useJsonRequest(view, preview);
  const curl = curlJson("/v1/chat/completions", preview, " \\\n  --no-buffer");

  const choose = (name) => {
    setModel(name);
    store.set("omni.chat.model", name);
  };

  const clear = () => {
    setMessages([]);
    setMeta("");
    turnState.toggle(null);
  };

  const send = async () => {
    if (streaming) return;
    let outgoing;
    let userMessage;
    if (view === "json") {
      if (!json.body) return;
      outgoing = json.body;
      const lastUser = [...(outgoing.messages ?? [])].reverse().find((message) => message.role === "user");
      if (!lastUser) return;
      userMessage = { id: crypto.randomUUID(), role: "user", content: textOf(lastUser), images: [], at: Date.now() };
    } else {
      const text = input.trim();
      if (!text) return;
      userMessage = { id: crypto.randomUUID(), role: "user", content: text, images, at: Date.now() };
      outgoing = requestBody(selected, system, [...messages, userMessage], false);
    }

    const base = [...messages, userMessage];
    const wireRequest = requestBody(outgoing.model ?? selected, system, base, true);
    setMessages(base);
    setInput("");
    setImages([]);
    setMeta("");
    stream.value = "";
    setStreaming(true);
    abort.current = new AbortController();
    const start = performance.now();
    let reply = "";
    let firstToken = null;
    let usage = null;
    let status = 0;
    try {
      const response = await chatStream(outgoing, abort.current.signal);
      status = response.status;
      if (!response.ok) throw new Error(await errorText(response));
      for await (const event of sseEvents(response)) {
        if (event.usage) usage = event.usage;
        const delta = event.choices?.[0]?.delta?.content;
        if (!delta) continue;
        firstToken ??= since(start);
        reply += delta;
        stream.value = reply;
      }
      const elapsed = since(start);
      record("chat", status, elapsed);
      const text = timing(firstToken ?? elapsed, elapsed, usage);
      setMeta(text);
      setMessages([...base, {
        id: crypto.randomUUID(), role: "assistant", content: reply, usage,
        request: wireRequest, response: { content: reply, usage },
        meta: text, at: Date.now(), model: outgoing.model ?? selected,
      }]);
    } catch (error) {
      if (error.name === "AbortError") {
        if (reply) setMessages([...base, { id: crypto.randomUUID(), role: "assistant", content: reply }]);
      } else {
        record("chat", status || 0, since(start));
        setMessages([...base, { id: crypto.randomUUID(), role: "assistant", content: `⚠ ${error.message}` }]);
      }
    } finally {
      setStreaming(false);
      stream.value = "";
      abort.current = null;
    }
  };

  const onKeyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send();
  };

  const canSend = !streaming && (view === "json" ? Boolean(json.body) && !json.error : Boolean(input.trim()));
  const openId = turnState.open ?? turns[turns.length - 1]?.id;

  const readout = [
    {
      label: "Model",
      node: (
        <select aria-label="chat model" value={selected} onChange={(event) => choose(event.currentTarget.value)}>
          {names.length ? names.map((name) => <option key={name} value={name}>{name}</option>) : <option value="">no chat models</option>}
        </select>
      ),
    },
    { label: "Runtime", value: vision ? "ollama · vision" : "ollama" },
    { label: "API", value: "POST /v1/chat/completions" },
    { label: "Last run", value: chat?.loaded ? (meta || "idle") : "not loaded" },
  ];

  return (
    <Workbench title="Chat" subtitle="Streaming completions from the chat role, OpenAI wire format." readout={readout}>
      <Panel
        title="Request"
        tools={(
          <>
            {vision && <ui.Button small variant="secondary" onClick={() => fileInput.current.click()}>+ image</ui.Button>}
            <UiJson view={view} onChange={setView} />
          </>
        )}
        foot={(
          <>
            <span class={styles.footHint}>{view === "json" ? "sending the JSON above" : `${messages.length} messages in the thread`}</span>
            {streaming
              ? <ui.Button variant="stop" onClick={() => abort.current?.abort()}>Stop</ui.Button>
              : <ui.Button variant="primary" disabled={!canSend} onClick={send}>Send</ui.Button>}
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
          <ui.Field label="System prompt">
            <textarea rows={2} placeholder="optional" value={system} onInput={(event) => setSystem(event.currentTarget.value)} />
          </ui.Field>
          <ui.Field label="Message" hint="Enter to send · Shift+Enter for a newline">
            <textarea
              class={styles.input}
              rows={3}
              placeholder="Message"
              value={input}
              onInput={(event) => setInput(event.currentTarget.value)}
              onKeyDown={onKeyDown}
            />
          </ui.Field>
          {images.length > 0 && (
            <div class={styles.thumbs}>
              {images.map((url, index) => (
                <button key={index} type="button" class={styles.thumbButton} title="Remove" onClick={() => setImages(images.filter((_, other) => other !== index))}>
                  <img class={styles.thumb} src={url} alt="attachment" />
                </button>
              ))}
            </div>
          )}
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={async (event) => {
              const urls = await Promise.all([...event.currentTarget.files].map(readDataUrl));
              setImages((current) => [...current, ...urls]);
              event.currentTarget.value = "";
            }}
          />
        </RequestBody>
      </Panel>

      <Panel
        title="Conversation"
        meta={meta}
        tools={<ui.Button small variant="plain" disabled={!messages.length || streaming} onClick={clear}>Clear</ui.Button>}
      >
        {turns.length
          ? (
            <div class={styles.log}>
              {turns.map((turn, index) => {
                const isStreaming = streaming && index === turns.length - 1;
                const body = turn.assistant?.content ?? (isStreaming ? stream.value : "");
                return (
                  <Turn
                    key={turn.id}
                    label={`Request #${index + 1}`}
                    at={turn.user?.at ?? turn.assistant?.at}
                    model={turn.assistant?.model}
                    meta={turn.assistant?.meta}
                    view={turnState.viewOf(turn.id)}
                    onView={(next) => turnState.setViewOf(turn.id, next)}
                    open={openId === turn.id}
                    onToggle={() => turnState.toggle(turn.id)}
                    summary={<>{turn.user?.content}<Attachments images={turn.user?.images} /></>}
                    wire={turn.assistant?.request
                      ? { request: turn.assistant.request, response: turn.assistant.response }
                      : { request: preview }}
                  >
                    <div class={styles.text}>{body}</div>
                    {isStreaming && !body && <div class={styles.thinking}>…</div>}
                  </Turn>
                );
              })}
            </div>
          )
          : <ui.Empty>Build a request on the left and press Send. Replies stream token by token.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
