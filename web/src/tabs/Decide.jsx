import { useState } from "preact/hooks";
import { record } from "../api/client.js";
import { decide } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as questionSet from "../lib/questions.js";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { QuestionBuilder } from "../components/QuestionBuilder.jsx";
import { Distribution, Panel, RequestBody, Turn, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import styles from "./Decide.module.css";

function viewFor(type, answer) {
  if (type === "noul") {
    const yes = answer.noul ?? 0;
    return {
      answer: yes >= 0.5 ? "true" : "false",
      rows: [{ label: "false", value: 1 - yes }, { label: "true", value: yes }],
      foot: `p(true) ${yes.toFixed(3)}`,
    };
  }
  const entries = Object.entries(answer.probabilities ?? {});
  if (type === "score") {
    const legend = answer.legend ?? {};
    const best = entries.reduce((top, entry) => (entry[1] > (top?.[1] ?? -1) ? entry : top), null);
    return {
      answer: Number(answer.score ?? 0).toFixed(2),
      rows: entries.map(([level, value]) => ({
        label: legend[level] ? `${level} · ${legend[level]}` : level,
        value,
        top: level === best?.[0],
      })),
      foot: `expectation, ${entries.length} levels`,
    };
  }
  return {
    answer: answer.choice ?? "",
    rows: entries.map(([label, value]) => ({ label, value })),
    foot: `${entries.length} options`,
  };
}

function Answers({ questions, data }) {
  const answers = Object.entries(data.answers ?? {});
  if (!answers.length) return <ui.Empty>The reply carried no answers.</ui.Empty>;
  return (
    <div class={styles.answers}>
      {answers.map(([name, answer]) => {
        const question = questions?.[name];
        const view = viewFor(question?.type, answer);
        return (
          <Distribution
            key={name}
            name={name}
            type={question?.type}
            instruction={question?.instructions}
            answer={view.answer}
            rows={view.rows}
            foot={(
              <>
                <span>{view.foot}</span>
                <span class={styles.turnMeta}>
                  {answer.confidence != null && <span>confidence {answer.confidence.toFixed(3)}</span>}
                </span>
              </>
            )}
          />
        );
      })}
    </div>
  );
}

function initialRows() {
  const saved = store.get("omni.decide.rows", null);
  if (Array.isArray(saved)) return saved;
  return questionSet.rowsFromQuestions(questionSet.EXAMPLES[questionSet.DEFAULT_EXAMPLE].questions);
}

function validationError(rows) {
  try {
    questionSet.questionsFromRows(rows);
    return "";
  } catch (failure) {
    return failure.message;
  }
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function adoptRequest(body) {
  if (!isPlainObject(body)) throw new Error("the body must be a JSON object");
  if (typeof body.state !== "string") throw new Error("state must be a string");
  if (!isPlainObject(body.questions)) throw new Error("questions must be an object");
  return {
    state: body.state,
    rows: questionSet.rowsFromQuestions(body.questions),
    model: typeof body.model === "string" ? body.model : "",
  };
}

export default function Decide() {
  const [model, setModel] = useState(() => store.get("omni.decide.model", ""));
  const [state, setState] = useState(() => store.get("omni.decide.state", questionSet.EXAMPLES[questionSet.DEFAULT_EXAMPLE].state));
  const [rows, setRows] = useState(initialRows);
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requestError, setRequestError] = useState("");
  const [view, setView] = useState("ui");
  const turnState = useTurnState();

  const decideModels = models.value.data.filter((entry) => entry.role === "decide").map((entry) => entry.id);
  const rowsError = validationError(rows);
  const role = runtime.value.roles.decide;

  const body = {
    state,
    questions: questionSet.questionsFromRows(rows, { strict: false }),
    ...(model ? { model } : {}),
  };
  const json = useJsonRequest(view, body);
  const curl = curlJson("/v1/systemone", view === "json" && !json.error ? json.body : body);

  const editState = (text) => {
    setState(text);
    store.set("omni.decide.state", text);
  };

  const editRows = (next) => {
    setRows(next);
    store.set("omni.decide.rows", next);
  };

  const choose = (name) => {
    setModel(name);
    store.set("omni.decide.model", name);
  };

  const loadExample = (key) => {
    const example = questionSet.EXAMPLES[key];
    if (!example) return;
    editState(example.state);
    editRows(questionSet.rowsFromQuestions(example.questions));
    setRequestError("");
  };

  const changeView = (next) => {
    if (next === view) return;
    if (view === "json") {
      try {
        if (json.error) throw new Error(json.error);
        const adopted = adoptRequest(json.body);
        editState(adopted.state);
        editRows(adopted.rows);
        choose(adopted.model);
      } catch (failure) {
        setRequestError(`Not a valid request: ${failure.message}`);
        return;
      }
    }
    setRequestError("");
    setView(next);
  };

  const send = async () => {
    const payload = view === "json" ? json.body : body;
    if (!payload || (view === "ui" && rowsError)) return;
    setBusy(true);
    setError("");
    const start = performance.now();
    const at = Date.now();
    record("decide", 0, 0);
    try {
      const { status, data } = await decide(payload);
      const elapsed = since(start);
      record("decide", status, elapsed);
      setTurns((current) => [...current, { id: crypto.randomUUID(), at, request: payload, response: data, meta: `${elapsed} ms` }]);
    } catch (failure) {
      record("decide", failure.status ?? 0, since(start));
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  const disableSend = busy || (view === "ui" && Boolean(rowsError)) || !state.trim();

  const sendOnShortcut = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !disableSend) {
      event.preventDefault();
      send();
    }
  };

  const openId = turnState.open ?? turns[turns.length - 1]?.id;

  const readout = [
    {
      label: "Model",
      node: (
        <select aria-label="decide model" value={model} onChange={(event) => choose(event.currentTarget.value)}>
          <option value="">default</option>
          {decideModels.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      ),
    },
    { label: "Runtime", value: "ollama" },
    { label: "API", value: "POST /v1/systemone" },
    { label: "Last run", value: role?.loaded ? (turns.at(-1)?.meta ?? "idle") : "not loaded" },
  ];

  return (
    <Workbench
      title="Decide"
      subtitle="Yes/no, choice and score questions answered as probability distributions in one forward pass."
      readout={readout}
    >
      <Panel
        title="Request"
        tools={(
          <>
            <select aria-label="load an example" value="" onChange={(event) => loadExample(event.currentTarget.value)}>
              <option value="">Examples…</option>
              {Object.entries(questionSet.EXAMPLES).map(([key, example]) => <option key={key} value={key}>{example.label}</option>)}
            </select>
            <UiJson view={view} onChange={changeView} />
          </>
        )}
        foot={(
          <>
            {view === "ui" && rowsError && <span class={styles.turnMeta}>{rowsError}</span>}
            {requestError && <span class={styles.turnMeta}>{requestError}</span>}
            <ui.Button variant="primary" disabled={disableSend} onClick={send}>{busy ? "Deciding…" : "Decide"}</ui.Button>
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
          <ui.Field label="State" hint="⌘/Ctrl + Enter to decide">
            <textarea rows={4} value={state} onInput={(event) => editState(event.currentTarget.value)} onKeyDown={sendOnShortcut} />
          </ui.Field>
          <ui.Field label="Questions">
            <QuestionBuilder rows={rows} onRows={editRows} />
          </ui.Field>
        </RequestBody>
      </Panel>

      <Panel title="Answers" meta={turns.at(-1)?.meta} tools={<ui.Button small variant="plain" disabled={!turns.length} onClick={() => setTurns([])}>Clear</ui.Button>}>
        {error && <ui.ErrorText>{error}</ui.ErrorText>}
        {turns.length
          ? (
            <div class={styles.pending}>
              {turns.map((turn, index) => (
                <Turn
                  key={turn.id}
                  label={`Request #${index + 1}`}
                  at={turn.at}
                  model={turn.response?.model ?? turn.request?.model}
                  meta={turn.meta}
                  view={turnState.viewOf(turn.id)}
                  onView={(next) => turnState.setViewOf(turn.id, next)}
                  open={openId === turn.id}
                  onToggle={() => turnState.toggle(turn.id)}
                  summary={turn.request?.state}
                  wire={{ request: turn.request, response: turn.response }}
                >
                  <Answers questions={turn.request?.questions} data={turn.response} />
                </Turn>
              ))}
            </div>
          )
          : !error && <ui.Empty>Answers show as probability distributions, one per question.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
