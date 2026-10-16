import { useState } from "preact/hooks";
import { record } from "../api/client.js";
import { embed } from "../api/endpoints.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

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
  return [{ input: query.trim(), input_type: "query" }, { input: documents, input_type: "document" }];
}

function dot(left, right) {
  let sum = 0;
  for (let index = 0; index < left.length; index++) sum += left[index] * right[index];
  return sum;
}

function isVector(value) {
  return Array.isArray(value) && value.length > 0 && value.every(Number.isFinite);
}

export default function Embed() {
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [documents, setDocuments] = useState(DEFAULT_DOCUMENTS);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [meta, setMeta] = useState("");

  const [queryBody, documentBody] = bodies(query, documents);
  const curl = `${curlJson("/v1/embeddings", queryBody)}\n\n${curlJson("/v1/embeddings", documentBody)}`;

  const rank = async () => {
    if (!queryBody.input || !documentBody.input.length) return;
    setBusy(true);
    setError("");
    const start = performance.now();
    try {
      const [queryResult, documentResult] = await Promise.all([embed(queryBody), embed(documentBody)]);
      const elapsed = since(start);
      record("embed", 200, elapsed);
      const queryData = queryResult.data;
      const vector = queryData.data?.[0]?.embedding;
      // Guard before the cosine loop: both sides must be non-empty and equal length.
      if (!isVector(vector)) throw new Error("query embedding is empty or contains non-finite values");
      const documentsData = documentResult.data.data ?? [];
      if (documentsData.length === 0) throw new Error("the server returned no document embeddings");
      for (const item of documentsData) {
        if (!isVector(item.embedding) || item.embedding.length !== vector.length) {
          throw new Error(`document embedding "${documentBody.input[item.index] ?? item.index}" is empty or does not match the query dimension (${vector.length})`);
        }
      }
      const ranked = documentsData
        .map((item) => ({ label: documentBody.input[item.index] ?? `#${item.index}`, value: dot(vector, item.embedding) }))
        .sort((left, right) => right.value - left.value)
        .map((row, position) => ({ ...row, top: position === 0 }));
      setResult({
        query: queryBody.input,
        rows: ranked,
        summary: `${queryData.model} · ${vector.length} dims · query ${queryData.usage?.prompt_tokens} + docs ${documentResult.data.usage?.prompt_tokens} tokens`,
        preview: `query vector: [${vector.slice(0, 8).map((value) => value.toFixed(4)).join(", ")}, …]`,
      });
      setMeta(`${elapsed} ms`);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Embed" subtitle="Vectors for search and clustering, with EmbeddingGemma task prompts.">
      <ui.Columns>
        <ui.Card title="Semantic search">
          <ui.Stack>
            <ui.Field label="Query" trailing={<ui.Meta>input_type=query</ui.Meta>}>
              <input value={query} onInput={(event) => setQuery(event.currentTarget.value)} />
            </ui.Field>
            <ui.Field
              label="Documents, one per line"
              trailing={<ui.Meta>input_type=document</ui.Meta>}
              hint="Two calls: one for the query, one batched for the documents. Vectors are normalised, so the dot product is the cosine similarity."
            >
              <textarea rows={8} value={documents} onInput={(event) => setDocuments(event.currentTarget.value)} />
            </ui.Field>
            <ui.Row end>
              <ui.Button variant="primary" disabled={busy} onClick={rank}>{busy ? "Ranking…" : "Rank"}</ui.Button>
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Ranking" meta={meta}>
          <ui.Stack>
            {error && <ui.ErrorText>{error}</ui.ErrorText>}
            {result ? (
              <>
                <div class={styles.answerHead}>
                  <span class={styles.instruction}>{result.query}</span>
                  <ui.Meta>cosine</ui.Meta>
                </div>
                <ui.Bars rows={result.rows} format={(value) => value.toFixed(3)} />
                <ui.Meta>{result.summary}</ui.Meta>
                <ui.Code>{result.preview}</ui.Code>
              </>
            ) : !error && <ui.Empty>Documents ranked by similarity to the query.</ui.Empty>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
