import { useEffect, useRef, useState } from "preact/hooks";
import { readDataUrl, record } from "../api/client.js";
import { describeImage, embed, transcribe } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { since } from "../lib/format.js";
import { cx } from "../lib/cx.js";
import * as corpus from "../lib/corpus.js";
import * as vectorstore from "../lib/vectorstore.js";
import * as ui from "../components/ui.jsx";
import { Panel, Turn, Workbench, useTurnState } from "../components/workbench.jsx";
import { LogsCard } from "../components/LogsCard.jsx";
import styles from "./Embed.module.css";

const BATCH = 64;
const TOP_K = 5;
const STATUS_BY_KIND = { text: "reading", image: "captioning", audio: "transcribing" };

function visionModel() {
  const { data, defaults } = models.value;
  const capable = data.filter((entry) => entry.capabilities?.includes("vision"));
  return capable.find((entry) => entry.id === defaults.chat)?.id ?? capable[0]?.id;
}

async function extractText(file, kind) {
  if (kind === "text") return file.text();
  if (kind === "audio") return (await transcribe(file, file.name, "text")).result;
  const model = visionModel();
  if (!model) throw new Error("no vision-capable chat model is available to caption images");
  return describeImage(await readDataUrl(file), model);
}

async function embedAll(texts, onProgress) {
  const vectors = new Array(texts.length);
  let tokens = 0;
  let model = "";
  for (let from = 0; from < texts.length; from += BATCH) {
    const batch = texts.slice(from, from + BATCH);
    const { data } = await embed({ input: batch, input_type: "document" });
    const items = data.data ?? [];
    if (items.length !== batch.length) throw new Error(`asked for ${batch.length} embeddings, got ${items.length}`);
    for (const item of items) {
      if (!corpus.isVector(item.embedding)) throw new Error("the server returned an empty or non-finite embedding");
      vectors[from + item.index] = Float32Array.from(item.embedding);
    }
    tokens += data.usage?.prompt_tokens ?? 0;
    model = data.model ?? model;
    onProgress(from + batch.length);
  }
  return { vectors, tokens, model };
}

async function entryFiles(entry, prefix) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return [{ file, path: `${prefix}${file.name}` }];
  }
  const reader = entry.createReader();
  const found = [];
  for (;;) {
    const children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!children.length) return found;
    for (const child of children) found.push(...await entryFiles(child, `${prefix}${entry.name}/`));
  }
}

async function droppedFiles(transfer) {
  const entries = Array.from(transfer.items ?? []).map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return Array.from(transfer.files).map((file) => ({ file, path: file.name }));
  const lists = await Promise.all(entries.map((entry) => entryFiles(entry, "")));
  return lists.flat();
}

function pickedFiles(input) {
  return Array.from(input.files).map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

function duration(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

function textSize(chars) {
  return chars >= 1e6 ? `${(chars / 1e6).toFixed(1)} M` : chars >= 1e3 ? `${(chars / 1e3).toFixed(1)} k` : String(chars);
}

function Stats({ done }) {
  const totals = {
    files: done.length,
    chunks: done.reduce((sum, file) => sum + file.chunks, 0),
    tokens: done.reduce((sum, file) => sum + file.tokens, 0),
    chars: done.reduce((sum, file) => sum + file.chars, 0),
    ms: done.reduce((sum, file) => sum + file.ms, 0),
  };
  const cells = [
    ["Files", totals.files],
    ["Chunks", totals.chunks],
    ["Dims", done[0]?.dims ?? "—"],
    ["Tokens", totals.tokens.toLocaleString()],
    ["Text", `${textSize(totals.chars)} chars`],
    ["Embed time", duration(totals.ms)],
  ];
  return (
    <dl class={styles.stats}>
      {cells.map(([label, value]) => (
        <div key={label} class={styles.stat}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function FileRow({ job, onRemove }) {
  const working = job.status !== "done" && job.status !== "error";
  const state = job.status === "done"
    ? `${job.chunks} chunks · ${duration(job.ms)}`
    : job.status === "embedding" ? `embedding ${job.done}/${job.total}` : job.status;
  return (
    <li class={styles.file}>
      <span class={styles.fileName} title={job.name}><span class={styles.fileKind}>{job.kind}</span>{job.name}</span>
      <span class={cx(styles.fileState, job.status === "error" && styles.fileBad)}>
        {state}
        {!working && <ui.Button small variant="plain" class={styles.remove} aria-label={`remove ${job.name}`} onClick={onRemove}>×</ui.Button>}
      </span>
      {working && <div class={styles.fileBar}><ui.Meter fraction={job.total ? job.done / job.total : 0} /></div>}
      {job.error && <span class={styles.fileError}>{job.error}</span>}
    </li>
  );
}

function Passages({ hits }) {
  return (
    <ol class={styles.passages}>
      {hits.map(({ chunk, score }) => (
        <li key={chunk.id} class={styles.passage}>
          <div class={styles.passageHead}>
            <span>{chunk.file} #{chunk.index}</span>
            <span class={styles.passageScore}>{score.toFixed(3)}</span>
          </div>
          <p class={styles.passageText}>{chunk.text}</p>
        </li>
      ))}
    </ol>
  );
}

export default function Embed() {
  const [jobs, setJobs] = useState([]);
  const [chunks, setChunks] = useState([]);
  const [skipped, setSkipped] = useState(0);
  const [unsaved, setUnsaved] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState("");
  const [turns, setTurns] = useState([]);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const turnState = useTurnState();
  const fileInput = useRef(null);
  const folderInput = useRef(null);

  useEffect(() => {
    vectorstore.loadAll().then((stored) => {
      const known = stored.files.map((file) => ({ ...file, status: "done", done: file.chunks, total: file.chunks }));
      setJobs((current) => [...known.filter((file) => !current.some((job) => job.name === file.name)), ...current]);
      setChunks((current) => [...stored.chunks.filter((chunk) => !current.some((own) => own.file === chunk.file)), ...current]);
    }).catch(() => setUnsaved(true));
  }, []);

  const upsert = (name, fields) => setJobs((current) => (
    current.some((job) => job.name === name)
      ? current.map((job) => (job.name === name ? { ...job, ...fields } : job))
      : [...current, { name, ...fields }]
  ));

  const indexOne = async ({ file, path }) => {
    const kind = corpus.kindOf(file.name, file.type);
    const start = performance.now();
    upsert(path, { kind, status: STATUS_BY_KIND[kind], done: 0, total: 0, error: "" });
    try {
      if (kind === "text" && file.size > corpus.MAX_TEXT_BYTES) throw new Error("larger than 2 MB");
      const text = await extractText(file, kind);
      const pieces = corpus.chunkText(text);
      if (!pieces.length) throw new Error("no text found in this file");
      upsert(path, { status: "embedding", total: pieces.length });
      const { vectors, tokens, model } = await embedAll(pieces, (done) => upsert(path, { done }));
      const ms = since(start);
      record("embed", 200, ms);
      const entries = pieces.map((piece, index) => ({ id: `${path}#${index}`, file: path, index, text: piece, embedding: vectors[index] }));
      const stored = { name: path, kind, bytes: file.size, chars: text.length, chunks: pieces.length, tokens, dims: vectors[0].length, model, ms, at: Date.now() };
      setChunks((current) => [...current.filter((chunk) => chunk.file !== path), ...entries]);
      upsert(path, { ...stored, status: "done", done: pieces.length, total: pieces.length });
      await vectorstore.putFile(stored, entries).catch(() => setUnsaved(true));
    } catch (failure) {
      record("embed", failure.status ?? 0, since(start));
      upsert(path, { status: "error", error: failure.message });
    }
  };

  const addItems = async (items) => {
    if (indexing) return;
    const usable = items.filter(({ file, path }) => !corpus.isSkippedPath(path) && corpus.kindOf(file.name, file.type));
    setSkipped(items.length - usable.length);
    setIndexing(true);
    for (const item of usable) await indexOne(item);
    setIndexing(false);
  };

  const removeFile = async (name) => {
    setJobs((current) => current.filter((job) => job.name !== name));
    setChunks((current) => current.filter((chunk) => chunk.file !== name));
    await vectorstore.remove(name).catch(() => setUnsaved(true));
  };

  const clearIndex = async () => {
    setJobs([]);
    setChunks([]);
    setTurns([]);
    await vectorstore.clear().catch(() => setUnsaved(true));
  };

  const ask = async (event) => {
    event.preventDefault();
    const text = query.trim();
    if (!text || !chunks.length || asking) return;
    setAsking(true);
    setError("");
    const request = { input: text, input_type: "query" };
    const start = performance.now();
    const at = Date.now();
    try {
      const { data } = await embed(request);
      const ms = since(start);
      record("embed", 200, ms);
      const vector = data.data?.[0]?.embedding;
      if (!corpus.isVector(vector)) throw new Error("query embedding is empty or contains non-finite values");
      const hits = corpus.search(vector, chunks, TOP_K);
      if (!hits.length) throw new Error(`no indexed passage has ${vector.length} dimensions: the index was built with a different embedding model`);
      const response = { model: data.model, usage: data.usage, results: hits.map((hit) => ({ id: hit.chunk.id, score: hit.score })) };
      setTurns((current) => [...current, { id: crypto.randomUUID(), at, query: text, hits, request, response, meta: `${ms} ms` }]);
    } catch (failure) {
      record("embed", failure.status ?? 0, since(start));
      setError(failure.message);
    } finally {
      setAsking(false);
    }
  };

  const done = jobs.filter((job) => job.status === "done");
  const openId = turnState.open ?? turns[turns.length - 1]?.id;

  const readout = [
    { label: "Model", value: models.value.defaults.embed ?? "—" },
    { label: "Runtime", value: "ollama" },
    { label: "API", value: "/v1/embeddings" },
    { label: "Index", value: done.length ? `${done.length} files · ${chunks.length} chunks` : "empty" },
  ];

  return (
    <Workbench
      title="Embed"
      subtitle="Index files and folders, then search them by meaning. The model reads text only: images are indexed by a vision-model caption and audio by its transcript."
      readout={readout}
    >
      <Panel
        title="Corpus"
        meta={indexing ? "indexing…" : undefined}
        tools={<ui.Button small variant="plain" disabled={indexing || !jobs.length} onClick={clearIndex}>Clear index</ui.Button>}
      >
        <div
          class={cx(styles.drop, over && styles.dropOver)}
          onDragOver={(event) => { event.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={async (event) => {
            event.preventDefault();
            setOver(false);
            addItems(await droppedFiles(event.dataTransfer));
          }}
        >
          <span>Drop files or a folder here</span>
          <div class={styles.pick}>
            <ui.Button disabled={indexing} onClick={() => fileInput.current.click()}>Add files</ui.Button>
            <ui.Button disabled={indexing} onClick={() => folderInput.current.click()}>Add folder</ui.Button>
          </div>
          <span class={styles.pickHint}>Text and code, images, audio. PDFs are not supported yet.</span>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              const items = pickedFiles(event.currentTarget);
              event.currentTarget.value = "";
              addItems(items);
            }}
          />
          <input
            ref={folderInput}
            type="file"
            webkitdirectory
            hidden
            onChange={(event) => {
              const items = pickedFiles(event.currentTarget);
              event.currentTarget.value = "";
              addItems(items);
            }}
          />
        </div>
        {unsaved && <ui.Notice tone="warn">This browser cannot save the index; it will be lost on reload.</ui.Notice>}
        {skipped > 0 && <p class={styles.notes}>{skipped} unsupported or hidden {skipped === 1 ? "file" : "files"} skipped.</p>}
        {done.length > 0 && <Stats done={done} />}
        {jobs.length > 0
          ? <ul class={styles.files}>{jobs.map((job) => <FileRow key={job.name} job={job} onRemove={() => removeFile(job.name)} />)}</ul>
          : <ui.Empty>Nothing indexed yet.</ui.Empty>}
      </Panel>

      <Panel
        title="Ask"
        meta={turns.at(-1)?.meta}
        tools={<ui.Button small variant="plain" disabled={!turns.length} onClick={() => setTurns([])}>Clear</ui.Button>}
        foot={(
          <form class={styles.ask} onSubmit={ask}>
            <input
              value={query}
              placeholder={chunks.length ? "Ask something about your files" : "Index some files first"}
              disabled={!chunks.length}
              onInput={(event) => setQuery(event.currentTarget.value)}
            />
            <ui.Button variant="primary" type="submit" disabled={asking || !query.trim() || !chunks.length}>{asking ? "Searching…" : "Search"}</ui.Button>
          </form>
        )}
      >
        {error && <ui.ErrorText>{error}</ui.ErrorText>}
        {turns.length
          ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              {turns.map((turn, index) => (
                <Turn
                  key={turn.id}
                  label={`Query #${index + 1}`}
                  at={turn.at}
                  model={turn.response.model}
                  meta={turn.meta}
                  view={turnState.viewOf(turn.id)}
                  onView={(next) => turnState.setViewOf(turn.id, next)}
                  open={openId === turn.id}
                  onToggle={() => turnState.toggle(turn.id)}
                  summary={turn.query}
                  wire={{ request: turn.request, response: turn.response }}
                >
                  <Passages hits={turn.hits} />
                </Turn>
              ))}
            </div>
          )
          : !error && <ui.Empty>The closest passages from your files appear here.</ui.Empty>}
      </Panel>
      <LogsCard role="embed" />
    </Workbench>
  );
}
