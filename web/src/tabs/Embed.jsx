import { useEffect, useRef, useState } from "preact/hooks";
import { readDataUrl, record } from "../api/client.js";
import { embed, transcribe } from "../api/endpoints.js";
import { cancelExec, newExecId } from "../api/execs.js";
import { models } from "../api/models.js";
import * as dialog from "../components/Confirm.jsx";
import { since, size } from "../lib/format.js";
import { uid } from "../lib/uid.js";
import { cx } from "../lib/cx.js";
import * as corpus from "../lib/corpus.js";
import { thumbnail } from "../lib/thumbnail.js";
import * as storage from "../lib/storage.js";
import * as vectorstore from "../lib/vectorstore.js";
import * as ui from "../components/ui.jsx";
import { Panel, Turn, Workbench, useTurnState } from "../components/workbench.jsx";
import { LogsCard } from "../components/LogsCard.jsx";
import styles from "./Embed.module.css";

const BATCH = 64;
const TOP_K = 5;
const STATUS_BY_KIND = { text: "reading", image: "reading", audio: "transcribing" };

function embedSeesImages() {
  const { data, defaults } = models.value;
  const bare = (name) => name?.replace(/:latest$/, "");
  return Boolean(data.find((entry) => bare(entry.id) === bare(defaults.embed))?.capabilities?.includes("vision"));
}

async function extractText(file, kind, run) {
  if (kind === "text") return file.text();
  return (await transcribe(file, file.name, "text", run.controller.signal, run.next())).result;
}

async function embedAll(texts, onProgress, run) {
  const vectors = new Array(texts.length);
  let tokens = 0;
  let model = "";
  for (let from = 0; from < texts.length; from += BATCH) {
    const batch = texts.slice(from, from + BATCH);
    const { data } = await embed({ input: batch, input_type: "document" }, run.controller.signal, run.next());
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

function Stats({ done, storedBytes }) {
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
    ["Stored", `≈ ${size(storedBytes)}`],
    ["Dims", done[0]?.dims ?? "—"],
    ["Tokens", totals.tokens.toLocaleString()],
    ["Text", textSize(totals.chars)],
    ["Time", duration(totals.ms)],
  ];
  return (
    <dl class={styles.stats}>
      {cells.map(([label, value]) => (
        <div key={label} class={styles.stat}><dt>{label}</dt><dd title={String(value)}>{value}</dd></div>
      ))}
    </dl>
  );
}

function BatchRow({ paths, jobs, indexing, onDismiss }) {
  const batch = jobs.filter((job) => paths.includes(job.name));
  const finished = batch.filter((job) => job.status === "done" || job.status === "error");
  const failed = batch.filter((job) => job.status === "error");
  const embedded = batch.filter((job) => job.status === "done");
  const chunks = embedded.reduce((sum, job) => sum + job.chunks, 0);
  const current = batch.find((job) => !finished.includes(job));
  const partial = current?.total ? current.done / current.total : 0;
  const title = indexing
    ? `Embedding file ${Math.min(finished.length + 1, paths.length)} of ${paths.length}`
    : `Embedded ${embedded.length} of ${paths.length} ${paths.length === 1 ? "file" : "files"}`;
  const detail = [indexing ? current?.name ?? "starting" : null, `${chunks} ${chunks === 1 ? "chunk" : "chunks"}`].filter(Boolean).join(" · ");
  return (
    <div class={styles.batch}>
      {indexing && current?.thumb && <img class={styles.embedding} src={current.thumb} alt={`${current.name} being embedded`} />}
      <div class={styles.fileText}>
        <span class={styles.fileName}>{title}</span>
        <span class={styles.fileDetail} title={detail}>{detail}</span>
      </div>
      {!indexing && <ui.IconButton icon="x" label="Dismiss" onClick={onDismiss} />}
      {indexing && <div class={styles.fileBar}><ui.Meter fraction={(finished.length + partial) / paths.length} /></div>}
      {failed.length > 0 && (
        <div class={styles.failures}>
          <span class={styles.failuresTitle}>{failed.length} {failed.length === 1 ? "file" : "files"} failed</span>
          <ul>
            {failed.map((job) => (
              <li key={job.name}>
                <code title={job.name}>{job.name}</code>
                <span>{job.error}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function usageLine(usage) {
  if (!usage) return "This browser does not report how much storage this site uses.";
  const share = usage.quota ? ` of about ${size(usage.quota)} available` : "";
  return `Everything this site stores in this browser: ${size(usage.usage)}${share}.`;
}

function FinePrint({ usage }) {
  return (
    <div class={styles.fine}>
      <p>{usageLine(usage)}</p>
      <p>
        Embedded data is saved in this browser&rsquo;s IndexedDB, separately for each address (<code>127.0.0.1</code> and{" "}
        <code>localhost</code> do not share it). It survives page reloads and browser restarts, but is lost if site data is
        cleared, in private or incognito windows, and in another browser or profile; a browser short on disk space may also
        evict it. It is not tied to the model: unloading or reloading the embedding model keeps it. Switching to a different
        embedding model makes it unsearchable &mdash; passages whose vectors have another size are skipped, and the same size
        from another model gives meaningless scores &mdash; so clear it and embed again.
      </p>
    </div>
  );
}

function Passages({ hits, thumbs }) {
  return (
    <ol class={styles.passages}>
      {hits.map(({ chunk, score }) => (
        <li key={chunk.id} class={styles.passage}>
          <div class={styles.passageHead}>
            <span>{chunk.file} #{chunk.index}</span>
            <span class={styles.passageScore}>{score.toFixed(3)}</span>
          </div>
          {thumbs[chunk.file] && <img class={styles.hitImage} src={thumbs[chunk.file]} alt={chunk.file} />}
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
  const [batchPaths, setBatchPaths] = useState([]);
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState("");
  const [turns, setTurns] = useState([]);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [usage, setUsage] = useState(null);
  const turnState = useTurnState();
  const fileInput = useRef(null);
  const folderInput = useRef(null);
  const imageInput = useRef(null);
  const indexRun = useRef(null);

  useEffect(() => {
    vectorstore.loadAll().then((stored) => {
      const known = stored.files.map((file) => ({ ...file, status: "done", done: file.chunks, total: file.chunks }));
      setJobs((current) => [...known.filter((file) => !current.some((job) => job.name === file.name)), ...current]);
      setChunks((current) => [...stored.chunks.filter((chunk) => !current.some((own) => own.file === chunk.file)), ...current]);
    }).catch(() => setUnsaved(true));
  }, []);

  useEffect(() => {
    if (!indexing) storage.browserUsage().then(setUsage);
  }, [indexing, jobs.length]);

  const upsert = (name, fields) => setJobs((current) => (
    current.some((job) => job.name === name)
      ? current.map((job) => (job.name === name ? { ...job, ...fields } : job))
      : [...current, { name, ...fields }]
  ));

  useEffect(() => () => indexRun.current?.controller.abort(), []);

  const indexOne = async ({ file, path }, run) => {
    const kind = corpus.kindOf(file.name, file.type);
    const start = performance.now();
    upsert(path, { kind, status: STATUS_BY_KIND[kind], done: 0, total: 0, error: "" });
    const thumb = kind === "image" ? await thumbnail(file) : "";
    if (thumb) upsert(path, { thumb });
    try {
      if (kind === "text" && file.size > corpus.MAX_TEXT_BYTES) throw new Error("larger than 2 MB");
      if (kind === "image" && file.size > corpus.MAX_IMAGE_BYTES) throw new Error("larger than 10 MB");
      const isImage = kind === "image";
      const text = isImage ? file.name : await extractText(file, kind, run);
      const pieces = isImage ? [text] : corpus.chunkText(text);
      if (!pieces.length) throw new Error("no text found in this file");
      upsert(path, { status: "embedding", total: pieces.length });
      const inputs = isImage ? [{ image: await readDataUrl(file) }] : pieces;
      const { vectors, tokens, model } = await embedAll(inputs, (done) => upsert(path, { done }), run);
      const ms = since(start);
      record("embed", 200, ms);
      const entries = pieces.map((piece, index) => ({ id: `${path}#${index}`, file: path, index, text: piece, embedding: vectors[index] }));
      const stored = { name: path, kind, bytes: file.size, chars: text.length, chunks: pieces.length, tokens, dims: vectors[0].length, model, ms, at: Date.now(), thumb };
      setChunks((current) => [...current.filter((chunk) => chunk.file !== path), ...entries]);
      upsert(path, { ...stored, status: "done", done: pieces.length, total: pieces.length });
      await vectorstore.putFile(stored, entries).catch(() => setUnsaved(true));
    } catch (failure) {
      record("embed", failure.status ?? 0, since(start));
      upsert(path, { status: "error", error: failure.name === "AbortError" ? "stopped" : failure.message });
    }
  };

  const addItems = async (items) => {
    if (indexing) {
      setNotice("Already embedding: wait for it to finish or stop it, then drop again.");
      return;
    }
    setNotice("");
    const supported = items.filter(({ file, path }) => !corpus.isSkippedPath(path) && corpus.kindOf(file.name, file.type));
    const imagesBlocked = models.value.data.length > 0 && !embedSeesImages();
    const isBlockedImage = ({ file }) => imagesBlocked && corpus.kindOf(file.name, file.type) === "image";
    const usable = supported.filter((item) => !isBlockedImage(item));
    const blockedCount = supported.length - usable.length;
    if (blockedCount) setNotice(`${blockedCount} ${blockedCount === 1 ? "image" : "images"} skipped: the embed model does not support images.`);
    setSkipped(items.length - supported.length);
    const queued = [...new Set(usable.map(({ path }) => path))];
    setBatchPaths(queued);
    // Reset every path first: a re-dropped file still reads "done" from its last run and would count as finished.
    queued.forEach((path) => upsert(path, { status: "queued", error: "" }));
    setIndexing(true);
    const run = {
      controller: new AbortController(),
      execId: null,
      next() {
        this.execId = newExecId();
        return this.execId;
      },
    };
    indexRun.current = run;
    for (const item of usable) {
      if (run.controller.signal.aborted) break;
      await indexOne(item, run);
    }
    indexRun.current = null;
    setJobs((current) => current.map((job) => (job.status === "queued" ? { ...job, status: "error", error: "stopped" } : job)));
    setIndexing(false);
  };

  const stopIndexing = async () => {
    const confirmed = await dialog.ask({
      title: "Stop embedding?",
      message: "The file being embedded is dropped and the rest are skipped. Files already embedded are kept.",
      confirmLabel: "Stop embedding",
      cancelLabel: "Keep going",
      tone: "danger",
    });
    if (!confirmed) return;
    cancelExec(indexRun.current?.controller, indexRun.current?.execId);
  };

  const clearIndex = async () => {
    setJobs([]);
    setChunks([]);
    setBatchPaths([]);
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
      const { data } = await embed(request, undefined, newExecId());
      const ms = since(start);
      record("embed", 200, ms);
      const vector = data.data?.[0]?.embedding;
      if (!corpus.isVector(vector)) throw new Error("query embedding is empty or contains non-finite values");
      const hits = corpus.search(vector, chunks, TOP_K);
      if (!hits.length) throw new Error(`no indexed passage has ${vector.length} dimensions: the embedded data was made with a different embedding model`);
      const response = { model: data.model, usage: data.usage, results: hits.map((hit) => ({ id: hit.chunk.id, score: hit.score })) };
      setTurns((current) => [...current, { id: uid(), at, query: text, hits, request, response, meta: `${ms} ms` }]);
    } catch (failure) {
      record("embed", failure.status ?? 0, since(start));
      setError(failure.message);
    } finally {
      setAsking(false);
    }
  };

  const done = jobs.filter((job) => job.status === "done");
  const thumbs = Object.fromEntries(jobs.filter((job) => job.thumb).map((job) => [job.name, job.thumb]));
  const footprints = storage.footprints(done, chunks);
    const catalogLoaded = models.value.data.length > 0;
  const canAddImages = embedSeesImages() || !catalogLoaded;
  const openId = turnState.open ?? turns[turns.length - 1]?.id;

  return (
    <Workbench
      class={styles.embed}
      title="Embed"
      subtitle="Embed files and folders, then search them by meaning. Images are embedded directly when the embed model supports them; audio is embedded as its transcript."
      modelRole="embed"
    >
      <Panel
        title="Embedded data"
        endpoint="/v1/embeddings"
        meta={indexing ? "embedding…" : undefined}
        tools={(
          <>
            {indexing && <ui.Button variant="stop" onClick={stopIndexing}>Stop</ui.Button>}
            <ui.IconButton icon="trash" label="Clear embedded data" disabled={indexing || !jobs.length} onClick={clearIndex} />
          </>
        )}
        foot={(
          <form class={cx(ui.css.inputRow, styles.ask)} onSubmit={ask}>
            <input
              aria-label="Search embedded data"
              value={query}
              placeholder={chunks.length ? "Ask something about your embedded data" : "Embed some files first"}
              disabled={!chunks.length}
              onInput={(event) => setQuery(event.currentTarget.value)}
            />
            <ui.Button variant="primary" type="submit" disabled={asking || !query.trim() || !chunks.length}>{asking ? "Searching…" : "Search"}</ui.Button>
          </form>
        )}
      >
        <div
          class={cx(styles.drop, over && styles.over)}
          onDragOver={(event) => { event.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={async (event) => {
            event.preventDefault();
            setOver(false);
            try { await addItems(await droppedFiles(event.dataTransfer)); } catch (failure) { setError(failure.message); }
          }}
        >
          <span>{canAddImages ? "Drop files, images or a folder here" : "Drop files or a folder here"}</span>
          <div class={styles.pick}>
            <ui.Button disabled={indexing} onClick={() => fileInput.current.click()}>Add files</ui.Button>
            {canAddImages && <ui.Button disabled={indexing} onClick={() => imageInput.current.click()}>Add images</ui.Button>}
            <ui.Button disabled={indexing} onClick={() => folderInput.current.click()}>Add folder</ui.Button>
          </div>
          <span class={styles.pickHint}>
            {canAddImages ? "Text and code, images (up to 10 MB) and audio." : "Text and code and audio. Images need an embed model that supports them, such as embeddinggemma-2."} PDFs are not supported yet.
          </span>
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
            ref={imageInput}
            type="file"
            accept="image/*"
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
        {catalogLoaded && !canAddImages && (
          <ui.Notice tone="warn">The embed model reads text only, so images cannot be embedded. Switch to embeddinggemma-2 in Settings.</ui.Notice>
        )}
        {unsaved && <ui.Notice tone="warn">This browser cannot save embedded data; it will be lost on reload.</ui.Notice>}
        {notice && <ui.Notice tone="warn">{notice}</ui.Notice>}
        {skipped > 0 && <p class={styles.notes}>{skipped} unsupported or hidden {skipped === 1 ? "file" : "files"} skipped.</p>}
        {done.length > 0 && <Stats done={done} storedBytes={storage.total(footprints)} />}
        {batchPaths.length > 0 && <BatchRow paths={batchPaths} jobs={jobs} indexing={indexing} onDismiss={() => setBatchPaths([])} />}
        {jobs.length === 0 && <ui.Empty>Nothing embedded yet.</ui.Empty>}
        <FinePrint usage={usage} />
      </Panel>

      <Panel
        title="Results"
        meta={turns.at(-1)?.meta}
        tools={<ui.IconButton icon="trash" label="Clear history" disabled={!turns.length} onClick={() => setTurns([])} />}
      >
        {error && <ui.ErrorText>{error}</ui.ErrorText>}
        {turns.length
          ? (
            <div class={ui.css.stack}>
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
                  <Passages hits={turn.hits} thumbs={thumbs} />
                </Turn>
              ))}
            </div>
          )
          : !error && <ui.Empty>The closest passages from your embedded data appear here.</ui.Empty>}
      </Panel>
      <LogsCard role="embed" />
    </Workbench>
  );
}
