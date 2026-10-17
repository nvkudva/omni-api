import { useState } from "preact/hooks";
import { record } from "../api/client.js";
import { embed } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import { Distribution, Panel, RequestBody, Turn, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";

const DEFAULT_QUERY = "how do I get my money back?";
const DEFAULT_DOCUMENTS = [
  "Refunds are issued to the original payment method within 5 business days.",
  "Our API returns 429 when you exceed the rate limit.",
  "To reset your password, open Settings and choose Security.",
  "Duplicate charges are reversed automatically after review.",
  "The office is closed on public holidays.",
  "def cosine(a, b): return sum(x * y for x, y in zip(a, b))",
].join("\n");

function bodies(query, documentsText) {
  const documents = documentsText.split("\n").map((line) => line.trim()).filter(Boolean);
  return [
    { input: query.trim(), input_type: "query" },
    { input: documents, input_type: "document" },
  ];
}

function dot(left, right) {
  let sum = 0;
  for (let index = 0; index < left.length; index++) sum += left[index] * right[index];
  return sum;
}

function isVector(value) {
  return Array.isArray(value) && value.length > 0 && value.every(Number.isFinite);
}

function Ranking({ rows, summary }) {
  return (
    <Distribution
      name="cosine similarity"
      type="dot product"
      instruction="Documents ranked by similarity to the query"
      answer={`${rows.length} docs`}
      rows={rows}
      format={(value) => value.toFixed(3)}
      foot={<span>{summary}</span>}
    />
  );
}

export default function Embed() {
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [documents, setDocuments] = useState(DEFAULT_DOCUMENTS);
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState("ui");
  const turnState = useTurnState();

  const [queryBody, documentBody] = bodies(query, documents);
  const preview = [queryBody, documentBody];
  const json = useJsonRequest(view, preview);
  const curl = `${curlJson("/v1/embeddings", queryBody)}\n\n${curlJson("/v1/embeddings", documentBody)}`;
  const role = runtime.value.roles.embed;

  const rank = async () => {
    const payloads = view === "json" ? json.body : preview;
    if (!Array.isArray(payloads) || payloads.length !== 2) {
      setError("the JSON must be a two-element array: the query body, then the documents body");
      return;
    }
    const [q, d] = payloads;
    if (!q?.input || !Array.isArray(d?.input) || !d.input.length) return;
    setBusy(true);
    setError("");
    const start = performance.now();
    const at = Date.now();
    try {
      const [queryResult, documentResult] = await Promise.all([embed(q), embed(d)]);
      const elapsed = since(start);
      record("embed", 200, elapsed);
      const queryData = queryResult.data;
      const vector = queryData.data?.[0]?.embedding;
      if (!isVector(vector)) throw new Error("query embedding is empty or contains non-finite values");
      const documentsData = documentResult.data.data ?? [];
      if (documentsData.length === 0) throw new Error("the server returned no document embeddings");
      for (const item of documentsData) {
        if (!isVector(item.embedding) || item.embedding.length !== vector.length) {
          throw new Error(`document embedding "${d.input[item.index] ?? item.index}" is empty or does not match the query dimension (${vector.length})`);
        }
      }
      const rows = documentsData
        .map((item) => ({ label: d.input[item.index] ?? `#${item.index}`, value: dot(vector, item.embedding) }))
        .sort((left, right) => right.value - left.value);
      const summary = `${queryData.model} · ${vector.length} dims · query ${queryData.usage?.prompt_tokens} + docs ${documentResult.data.usage?.prompt_tokens} tokens`;
      setTurns((current) => [...current, {
        id: crypto.randomUUID(), at, request: { query: q, documents: d }, response: { rows, summary }, meta: `${elapsed} ms`, rows, summary,
      }]);
    } catch (failure) {
      record("embed", failure.status ?? 0, since(start));
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  const openId = turnState.open ?? turns[turns.length - 1]?.id;

  const readout = [
    { label: "Model", value: models.value.defaults.embed ?? "—" },
    { label: "Runtime", value: "ollama" },
    { label: "API", value: "POST /v1/embeddings ×2" },
    { label: "Last run", value: role?.loaded ? (turns.at(-1)?.meta ?? "idle") : "not loaded" },
  ];

  return (
    <Workbench
      title="Embed"
      subtitle="Vectors for search and clustering, with EmbeddingGemma task prompts. Two calls: the query, then the documents in one batch."
      readout={readout}
    >
      <Panel
        title="Request"
        tools={<UiJson view={view} onChange={setView} />}
        foot={<ui.Button variant="primary" disabled={busy || (view === "ui" && (!queryBody.input || !documentBody.input.length))} onClick={rank}>{busy ? "Ranking…" : "Rank"}</ui.Button>}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
          <ui.Field label="Query" trailing={<ui.Meta>input_type=query</ui.Meta>}>
            <input value={query} onInput={(event) => setQuery(event.currentTarget.value)} />
          </ui.Field>
          <ui.Field
            label="Documents, one per line"
            trailing={<ui.Meta>input_type=document</ui.Meta>}
            hint="Vectors are normalised, so the dot product is the cosine similarity."
          >
            <textarea rows={9} value={documents} onInput={(event) => setDocuments(event.currentTarget.value)} />
          </ui.Field>
        </RequestBody>
      </Panel>

      <Panel title="Ranking" meta={turns.at(-1)?.meta} tools={<ui.Button small variant="plain" disabled={!turns.length} onClick={() => setTurns([])}>Clear</ui.Button>}>
        {error && <ui.ErrorText>{error}</ui.ErrorText>}
        {turns.length
          ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              {turns.map((turn, index) => (
                <Turn
                  key={turn.id}
                  label={`Ranking #${index + 1}`}
                  at={turn.at}
                  model={turn.response.summary?.split(" · ")[0]}
                  meta={turn.meta}
                  view={turnState.viewOf(turn.id)}
                  onView={(next) => turnState.setViewOf(turn.id, next)}
                  open={openId === turn.id}
                  onToggle={() => turnState.toggle(turn.id)}
                  summary={turn.request.query?.input}
                  wire={{ request: turn.request, response: { summary: turn.response.summary, rows: turn.response.rows.slice(0, 5) } }}
                >
                  <Ranking rows={turn.rows} summary={turn.summary} />
                </Turn>
              ))}
            </div>
          )
          : !error && <ui.Empty>Documents ranked by similarity to the query.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
