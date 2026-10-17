import * as questions from "../lib/questions.js";
import * as ui from "./ui.jsx";
import styles from "./QuestionBuilder.module.css";

function Remove({ label, onClick }) {
  return <ui.IconButton icon="x" label={label} onClick={onClick} />;
}

function ChoiceCriteria({ row, number, update }) {
  const setPair = (index, slot, value) => update({
    criteria: row.criteria.map((pair, at) => (at === index ? pair.map((text, i) => (i === slot ? value : text)) : pair)),
  });
  return (
    <>
      <span class={styles.label}>criteria — label : description</span>
      {row.criteria.map((pair, index) => (
        <div class={styles.criterion} key={index}>
          <input class={styles.key} placeholder="label" aria-label={`Question ${number} option ${index + 1} label`} value={pair[0]} onInput={(event) => setPair(index, 0, event.currentTarget.value)} />
          <input placeholder="description (optional)" aria-label={`Question ${number} option ${index + 1} description`} value={pair[1]} onInput={(event) => setPair(index, 1, event.currentTarget.value)} />
          <Remove label="Remove this option" onClick={() => update({ criteria: row.criteria.filter((_, at) => at !== index) })} />
        </div>
      ))}
      <ui.IconButton icon="plus" label="Add option" class={styles.add} onClick={() => update({ criteria: [...row.criteria, ["", ""]] })} />
    </>
  );
}

function ScoreCriteria({ row, number, update }) {
  const setRung = (index, value) => update({ criteria: row.criteria.map((rung, at) => (at === index ? value : rung)) });
  return (
    <>
      <span class={styles.label}>criteria — lowest rung first</span>
      {row.criteria.map((rung, index) => (
        <div class={styles.criterion} key={index}>
          <input placeholder={`rung ${index}`} aria-label={`Question ${number} rung ${index}`} value={rung} onInput={(event) => setRung(index, event.currentTarget.value)} />
          <Remove label="Remove this rung" onClick={() => update({ criteria: row.criteria.filter((_, at) => at !== index) })} />
        </div>
      ))}
      <ui.IconButton icon="plus" label="Add rung" class={styles.add} onClick={() => update({ criteria: [...row.criteria, ""] })} />
    </>
  );
}

function NoulCriteria({ row, number, update }) {
  const setText = (index, value) => update({ criteria: ["true", "false"].map((_, at) => (at === index ? value : (row.criteria[at] ?? ""))) });
  return (
    <>
      <span class={styles.label}>criteria (optional)</span>
      {["true", "false"].map((answer, index) => (
        <div class={styles.criterion} key={answer}>
          <input class={styles.key} value={answer} aria-label={`Answer ${answer}`} disabled />
          <input placeholder={`what makes it ${answer}`} aria-label={`Question ${number} what makes it ${answer}`} value={row.criteria[index] ?? ""} onInput={(event) => setText(index, event.currentTarget.value)} />
        </div>
      ))}
    </>
  );
}

const CRITERIA = { choice: ChoiceCriteria, score: ScoreCriteria, noul: NoulCriteria };

function Question({ row, index, onChange, onRemove }) {
  const Criteria = CRITERIA[row.type];
  const number = index + 1;
  const update = (patch) => onChange({ ...row, ...patch });
  return (
    <div class={styles.question}>
      <div class={styles.head}>
        <input placeholder="question_name" aria-label={`Question ${number} name`} value={row.name} onInput={(event) => update({ name: event.currentTarget.value })} />
        <span class={styles.type}>{row.type}</span>
        <Remove label="Remove this question" onClick={onRemove} />
      </div>
      <textarea rows={2} placeholder="instructions" aria-label={`Question ${number} instructions`} value={row.instructions} onInput={(event) => update({ instructions: event.currentTarget.value })} />
      <Criteria row={row} number={number} update={update} />
    </div>
  );
}

/** The structured editor for a `questions` object: one card per question, plus the add strip. */
export function QuestionBuilder({ rows, onRows }) {
  const change = (index, row) => onRows(rows.map((current, at) => (at === index ? row : current)));
  const remove = (index) => onRows(rows.filter((_, at) => at !== index));
  const add = (type) => onRows([...rows, questions.blankRow(type)]);
  return (
    <div class={styles.builder}>
      {rows.map((row, index) => (
        <Question key={index} row={row} index={index} onChange={(next) => change(index, next)} onRemove={() => remove(index)} />
      ))}
      <div class={styles.adder}>
        <span class={styles.label}>Add a new question</span>
        <ui.Button small onClick={() => add("noul")}>+ noul</ui.Button>
        <ui.Button small onClick={() => add("choice")}>+ choice</ui.Button>
        <ui.Button small onClick={() => add("score")}>+ score</ui.Button>
      </div>
    </div>
  );
}
