import { useState } from "preact/hooks";
import { record } from "../api/client.js";
import { decide } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Distribution, Panel, RequestBody, Turn, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import styles from "./Decide.module.css";

const DEFAULT_STATE = "Hi, I was charged twice for my subscription this month and nobody has answered my last two emails. Please fix this today.";
const DEFAULT_QUESTIONS = JSON.stringify({
  intent: {
    type: "choice",
    instructions: "What does the customer want?",
    criteria: {
      refund: "money returned or a duplicate charge reversed",
      technical_help: "a bug, outage or integration problem",
      other: "none of the other options fits",
    },
  },
  urgency: {
    type: "score",
    instructions: "How urgent is this message?",
    criteria: ["Can wait", "Needs attention this week", "Needs attention today"],
  },
  is_frustrated: { type: "noul", instructions: "Does the customer sound frustrated?" },
}, null, 2);

function parseQuestions(text) {
  try {
    return { questions: JSON.parse(text) };
  } catch (error) {
    return { error: error.message };
  }
}

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

export default function Decide() {
  const [model, setModel] = useState(() => store.get("omni.decide.model", ""));
  const [state, setState] = useState(DEFAULT_STATE);
  const [questionsText, setQuestionsText] = useState(() => store.get("omni.decide.questions", DEFAULT_QUESTIONS));
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState("ui");
  const turnState = useTurnState();

  const decideModels = models.value.data.filter((entry) => entry.role === "decide").map((entry) => entry.id);
  const parsed = parseQuestions(questionsText);
  const role = runtime.value.roles.decide;

  const body = {
    state,
    ...(parsed.questions ? { questions: parsed.questions } : {}),
    ...(model ? { model } : {}),
  };
  const json = useJsonRequest(view, body);
  const curl = parsed.error && view === "ui"
    ? "# questions are not valid JSON"
    : curlJson("/v1/systemone", view === "json" && !json.error ? json.body : body);

  const editQuestions = (text) => {
    setQuestionsText(text);
    store.set("omni.decide.questions", text);
  };

  const choose = (name) => {
    setModel(name);
    store.set("omni.decide.model", name);
  };

  const send = async () => {
    const payload = view === "json" ? json.body : body;
    if (!payload || (view === "ui" && parsed.error)) return;
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

  const disableSend = busy || (view === "ui" && Boolean(parsed.error)) || !state.trim();
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
        tools={<UiJson view={view} onChange={setView} />}
        foot={(
          <>
            {parsed.error && view === "ui" && <span class={styles.turnMeta}>questions JSON: {parsed.error}</span>}
            <ui.Button variant="primary" disabled={disableSend} onClick={send}>{busy ? "Deciding…" : "Decide"}</ui.Button>
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error}>
          <ui.Field label="State">
            <textarea rows={4} value={state} onInput={(event) => setState(event.currentTarget.value)} />
          </ui.Field>
          <ui.Field
            label="Questions"
            trailing={<ui.Button small variant="plain" onClick={() => editQuestions(DEFAULT_QUESTIONS)}>Reset</ui.Button>}
            hint={<><b>noul</b> yes/no · <b>choice</b> criteria is <code>{"{label: description}"}</code> · <b>score</b> criteria is an ordered list of levels.</>}
          >
            <textarea class={styles.questions} rows={18} spellcheck={false} value={questionsText} onInput={(event) => editQuestions(event.currentTarget.value)} />
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
        <ui.CurlBlock code={curl} />
      </Panel>
    </Workbench>
  );
}
