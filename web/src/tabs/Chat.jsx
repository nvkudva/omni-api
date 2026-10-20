import { signal } from "@preact/signals";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { errorText, readDataUrl, record, sseEvents } from "../api/client.js";
import { chatStream } from "../api/endpoints.js";
import { cancelExec, newExecId } from "../api/execs.js";
import { models } from "../api/models.js";
import { curlJson } from "../lib/curl.js";
import { uid } from "../lib/uid.js";
import { cx } from "../lib/cx.js";
import { shorten, since } from "../lib/format.js";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Panel, RequestBody, Turn, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { LogsCard } from "../components/LogsCard.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import styles from "./Chat.module.css";

function chatModels() {
  const { data, defaults } = models.value;
  return data
    .filter((model) => model.role === "chat" && (model.capabilities?.includes("completion") || model.id === defaults.chat))
    .map((model) => model.id);
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
  let text = `TTFS ${firstToken} ms · total ${elapsed} ms`;
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

/** Turns flattened for the bubble view: a user bubble and/or an assistant bubble each. */
function toBubbles(turns, streaming) {
  const bubbles = [];
  turns.forEach((turn, index) => {
    if (turn.user) bubbles.push({ id: `${turn.id}-user`, role: "user", content: turn.user.content, images: turn.user.images });
    const isStreaming = streaming && index === turns.length - 1;
    const live = isStreaming && !turn.assistant;
    const body = turn.assistant?.content ?? "";
    if (body || live) bubbles.push({ id: `${turn.id}-assistant`, role: "assistant", content: body, live });
  });
  return bubbles;
}

function Attachments({ images }) {
  if (!images?.length) return null;
  return (
    <div class={styles.thumbs}>
      {images.map((url, index) => <img key={index} class={styles.thumb} src={url} alt="" />)}
    </div>
  );
}

/** The only reader of the stream signal, so a token re-renders this text and nothing else. */
function StreamText({ stream, class: className, follow }) {
  const text = stream.value;
  useEffect(() => { follow(); }, [text]);
  return text ? <div class={className}>{text}</div> : <div class={styles.thinking}>…</div>;
}

/** A wire request with its image data URLs shortened for display. */
function shortenWire(body) {
  return {
    ...body,
    messages: (body.messages ?? []).map((message) => (Array.isArray(message.content)
      ? {
        ...message,
        content: message.content.map((part) => (part?.type === "image_url" && part.image_url?.url
          ? { ...part, image_url: { ...part.image_url, url: shorten(part.image_url.url) } }
          : part)),
      }
      : message)),
  };
}

function imagesOf(message) {
  if (!Array.isArray(message?.content)) return [];
  return message.content.filter((part) => part?.type === "image_url" && part.image_url?.url).map((part) => part.image_url.url);
}

export default function Chat() {
  const [model, setModel] = useState(() => store.get("omni.chat.model", ""));
  const [system, setSystem] = useState("");
  const [systemOpen, setSystemOpen] = useState(false);
  const [input, setInput] = useState("");
  const [images, setImages] = useState([]);
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [meta, setMeta] = useState("");
  const [view, setView] = useState("ui");
  const [conversation, setConversation] = useState(() => store.get("omni.chat.conversation", "chat"));
  const abort = useRef(null);
  const execId = useRef(null);
  const fileInput = useRef(null);
  const stream = useMemo(() => signal(""), []);
  const turns = useMemo(() => toTurns(messages), [messages]);
  const listRef = useRef(null);
  const turnState = useTurnState();

  const names = chatModels();
  const selected = names.includes(model) ? model : (names.includes(models.value.defaults.chat) ? models.value.defaults.chat : names[0] || "");
  const vision = hasVision(selected);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { if (!vision) setImages([]); }, [vision]);
  useEffect(() => { if (system.trim()) setSystemOpen(true); }, [system]);

  const draft = input ? [{ id: "draft", role: "user", content: input, images }] : [];
  const preview = useMemo(
    () => requestBody(selected, system, [...messages, ...draft], true),
    [selected, system, messages, input, images],
  );
  const json = useJsonRequest(view, preview);
  const effectiveBody = view === "json" && json.body ? json.body : preview;
  const curl = useMemo(
    () => curlJson("/v1/chat/completions", effectiveBody, " \\\n  --no-buffer"),
    [effectiveBody],
  );

  const follow = () => {
    const scroller = listRef.current?.parentElement;
    if (!scroller) return;
    const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
    if (nearBottom) scroller.scrollTop = scroller.scrollHeight;
  };
  useEffect(follow, [messages, conversation]);

  const choose = (name) => {
    setModel(name);
    store.set("omni.chat.model", name);
  };

  const changeConversation = (next) => {
    setConversation(next);
    store.set("omni.chat.conversation", next);
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
      userMessage = { id: uid(), role: "user", content: textOf(lastUser), images: imagesOf(lastUser), at: Date.now() };
    } else {
      const text = input.trim();
      if (!text) return;
      userMessage = { id: uid(), role: "user", content: text, images, at: Date.now() };
      outgoing = requestBody(selected, system, [...messages, userMessage], false);
    }

    const base = [...messages, userMessage];
    const wireRequest = view === "json" ? shortenWire(outgoing) : requestBody(outgoing.model ?? selected, system, base, true);
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
      const response = await chatStream(outgoing, abort.current.signal, execId.current = newExecId());
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
        id: uid(), role: "assistant", content: reply, usage,
        request: wireRequest, response: { content: reply, usage },
        meta: text, at: Date.now(), model: outgoing.model ?? selected,
      }]);
    } catch (error) {
      if (error.name === "AbortError") {
        if (reply) setMessages([...base, { id: uid(), role: "assistant", content: reply }]);
      } else {
        record("chat", status || 0, since(start));
        setMessages([...base, { id: uid(), role: "assistant", content: `⚠ ${error.message}` }]);
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

  const jsonHasUser = Boolean(json.body?.messages?.some?.((message) => message.role === "user"));
  const canSend = !streaming && (view === "json" ? Boolean(json.body) && !json.error && jsonHasUser : Boolean(input.trim()));
  const openId = turnState.open ?? turns[turns.length - 1]?.id;
  const bubbles = useMemo(() => toBubbles(turns, streaming), [turns, streaming]);

  const modelPicker = (
    <select aria-label="chat model" value={selected} onChange={(event) => choose(event.currentTarget.value)}>
      {names.length ? names.map((name) => <option key={name} value={name}>{name}</option>) : <option value="">no chat models</option>}
    </select>
  );

  return (
    <Workbench title="Chat" subtitle="Streaming completions from the chat role, OpenAI wire format." modelRole="chat" headerTools={modelPicker}>
      <Panel
        title="Request"
        endpoint="/v1/chat/completions"
        tools={(
          <>
            {vision && <ui.IconButton icon="plus" label="Attach image" onClick={() => fileInput.current.click()} />}
            <UiJson view={view} onChange={setView} />
          </>
        )}
        foot={(
          <>
            {view === "ui"
              ? (
                <textarea
                  class={styles.input}
                  rows={3}
                  placeholder="Message"
                  aria-label="Message"
                  title="Enter to send · Shift+Enter for a newline"
                  value={input}
                  onInput={(event) => setInput(event.currentTarget.value)}
                  onKeyDown={onKeyDown}
                />
              )
              : <span class={styles.footHint}>sending the JSON above</span>}
            {streaming
              ? <ui.Button variant="stop" onClick={() => cancelExec(abort.current, execId.current)}>Stop</ui.Button>
              : <ui.Button variant="primary" disabled={!canSend} onClick={send}>Send</ui.Button>}
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
          <details class={styles.system} open={systemOpen} onToggle={(event) => setSystemOpen(event.currentTarget.open)}>
            <summary class={styles.systemSummary}>
              <span class={styles.systemLabel}>System prompt</span>
              {!systemOpen && system && <span class={styles.systemPreview}>{system}</span>}
            </summary>
            <textarea class={styles.systemText} placeholder="optional" aria-label="System prompt" value={system} onInput={(event) => setSystem(event.currentTarget.value)} />
          </details>
          {images.length > 0 && (
            <div class={styles.thumbs}>
              {images.map((url, index) => (
                <button key={index} type="button" class={styles.thumbButton} title="Remove" aria-label={`Remove attachment ${index + 1}`} onClick={() => setImages(images.filter((_, other) => other !== index))}>
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
              const inputEl = event.currentTarget;
              try {
                const urls = await Promise.all([...inputEl.files].map(readDataUrl));
                setImages((current) => [...current, ...urls]);
              } catch {
                setMeta("could not read that image");
              }
              inputEl.value = "";
            }}
          />
        </RequestBody>
      </Panel>

      <Panel
        title="Conversation"
        meta={meta}
        tools={(
          <>
            <ui.Segmented
              label="conversation view"
              options={[{ value: "chat", label: "Chat" }, { value: "requests", label: "Requests" }]}
              value={conversation}
              onChange={changeConversation}
            />
            <ui.IconButton icon="trash" label="Clear conversation" disabled={!messages.length || streaming} onClick={clear} />
          </>
        )}
      >
        {turns.length
          ? conversation === "chat"
            ? (
              <div class={styles.chat} ref={listRef}>
                {bubbles.map((bubble) => (
                  <div key={bubble.id} class={cx(styles.bubble, styles[bubble.role])}>
                    {bubble.live
                      ? <StreamText stream={stream} class={styles.bubbleText} follow={follow} />
                      : <div class={styles.bubbleText}>{bubble.content}</div>}
                    <Attachments images={bubble.images} />
                  </div>
                ))}
              </div>
            )
            : (
              <div class={styles.log} ref={listRef}>
                {turns.map((turn, index) => {
                  const isStreaming = streaming && index === turns.length - 1;
                  const live = isStreaming && !turn.assistant;
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
                      {live
                        ? <StreamText stream={stream} class={styles.text} follow={follow} />
                        : <div class={styles.text}>{turn.assistant?.content ?? ""}</div>}
                    </Turn>
                  );
                })}
              </div>
            )
          : <ui.Empty>Build a request on the left and press Send. Replies stream token by token.</ui.Empty>}
      </Panel>
      <LogsCard role="chat" />
    </Workbench>
  );
}
