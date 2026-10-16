import { useState } from "preact/hooks";
import * as api from "../api.js";
import * as store from "../store.js";
import * as ui from "../ui.jsx";
import styles from "./tabs.module.css";

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

function noulView(answer) {
  const yes = answer.noul;
  return {
    headline: yes >= 0.5 ? "yes" : "no",
    rows: [
      { label: "yes", value: yes, top: yes >= 0.5 },
      { label: "no", value: 1 - yes, top: yes < 0.5 },
    ],
  };
}

function choiceView(answer) {
  const rows = Object.entries(answer.probabilities ?? {}).map(([label, value]) => ({ label, value, top: label === answer.choice }));
  return { headline: answer.choice, rows };
}

function scoreView(answer) {
  const entries = Object.entries(answer.probabilities ?? {});
  const best = entries.reduce((top, entry) => (entry[1] > top[1] ? entry : top), entries[0] ?? ["", 0]);
  const legend = answer.legend ?? {};
  const rows = entries.map(([level, value]) => ({
    label: legend[level] ? `${level} · ${legend[level]}` : level,
    value,
    top: level === best[0],
  }));
  const levels = Object.keys(legend).length || entries.length;
  return { headline: `${Number(answer.score).toFixed(2)} / ${levels - 1}`, rows };
}

const VIEWS = { noul: noulView, choice: choiceView, score: scoreView };

function Answer({ name, question, answer }) {
  const view = (VIEWS[question?.type] ?? choiceView)(answer);
  return (
    <div class={styles.answer}>
      <p class={styles.instruction}>{question?.instructions}</p>
      <div class={styles.answerHead}>
        <span class={styles.headline}>{view.headline}</span>
        <ui.Meta>{name} · {question?.type}</ui.Meta>
      </div>
      <ui.Bars rows={view.rows} />
      {answer.confidence != null && <ui.Meta>confidence {(answer.confidence * 100).toFixed(0)}%</ui.Meta>}
    </div>
  );
}

export default function Decide() {
  const [model, setModel] = useState(() => store.get("omni.decide.model", ""));
  const [state, setState] = useState(DEFAULT_STATE);
  const [questionsText, setQuestionsText] = useState(() => store.get("omni.decide.questions", DEFAULT_QUESTIONS));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [meta, setMeta] = useState("");

  const decideModels = api.models.value.data.filter((entry) => entry.role === "decide").map((entry) => entry.id);
  const parsed = parseQuestions(questionsText);

  const body = (questions) => {
    const request = { state, questions };
    if (model) request.model = model;
    return request;
  };

  const editQuestions = (text) => {
    setQuestionsText(text);
    store.set("omni.decide.questions", text);
  };

  const choose = (name) => {
    setModel(name);
    store.set("omni.decide.model", name);
  };

  const send = async () => {
    if (parsed.error) {
      setError(`questions JSON: ${parsed.error}`);
      return;
    }
    setBusy(true);
    setError("");
    const start = performance.now();
    try {
      const response = await fetch("/v1/systemone", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body(parsed.questions)),
      });
      const elapsed = api.since(start);
      api.record("decide", response.status, elapsed);
      if (!response.ok) throw new Error(await api.errorText(response));
      const data = await response.json();
      const tokens = data.usage?.prompt_tokens ?? data.usage?.input_tokens ?? "?";
      setResult({ data, questions: parsed.questions });
      setMeta(`${data.model ?? model ?? "default"} · ${elapsed} ms · ${tokens} tokens`);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  const curl = parsed.error ? "# questions are not valid JSON" : api.curlJson("/v1/systemone", body(parsed.questions));
  const answers = result ? Object.entries(result.data.answers ?? {}) : [];

  return (
    <ui.Page title="Decide" subtitle="Yes/no, choice and score questions answered as probability distributions.">
      <ui.Columns>
        <ui.Card title="Request">
          <ui.Stack>
            <ui.Field label="Model">
              <select value={model} onChange={(event) => choose(event.currentTarget.value)}>
                <option value="">default model</option>
                {decideModels.map((name) => <option value={name}>{name}</option>)}
              </select>
            </ui.Field>
            <ui.Field label="State">
              <textarea rows={4} value={state} onInput={(event) => setState(event.currentTarget.value)} />
            </ui.Field>
            <ui.Field
              label="Questions"
              trailing={<ui.Button small variant="plain" onClick={() => editQuestions(DEFAULT_QUESTIONS)}>Reset</ui.Button>}
              hint={<><b>noul</b> yes/no · <b>choice</b> criteria is <code>{"{label: description}"}</code> · <b>score</b> criteria is an ordered list of levels.</>}
            >
              <textarea class={styles.mono} rows={18} spellcheck={false} value={questionsText} onInput={(event) => editQuestions(event.currentTarget.value)} />
            </ui.Field>
            <ui.Row end>
              <ui.Button variant="primary" disabled={busy} onClick={send}>{busy ? "Deciding…" : "Decide"}</ui.Button>
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Answers" meta={meta}>
          <ui.Stack>
            {error && <ui.ErrorText>{error}</ui.ErrorText>}
            {answers.length
              ? answers.map(([name, answer]) => <Answer name={name} question={result.questions[name]} answer={answer} />)
              : !error && <ui.Empty>Answers show as probability distributions.</ui.Empty>}
            {result && <ui.Code>{JSON.stringify(result.data, null, 2)}</ui.Code>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
