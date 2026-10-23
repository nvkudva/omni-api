import { problems, clearProblems } from "../api/notify.js";
import * as ui from "../components/ui.jsx";
import { Workbench, Panel } from "../components/workbench.jsx";
import styles from "./Problems.module.css";

function Entry({ entry }) {
  const copy = () => navigator.clipboard?.writeText(entry.message);
  return (
    <li class={styles.entry}>
      <div class={styles.head}>
        <span>{new Date(entry.at).toLocaleTimeString()}</span>
        <code>{entry.source}</code>
        <ui.IconButton icon="copy" label="Copy message" size={14} onClick={copy} />
      </div>
      <p class={styles.message}>{entry.message}</p>
    </li>
  );
}

export default function Problems() {
  return (
    <Workbench single title="Problems" subtitle="Every error shown in this session, newest first. A refresh clears the list.">
      <Panel
        title="Errors"
        meta={problems.value.length ? `${problems.value.length}` : undefined}
        tools={<ui.IconButton icon="trash" label="Clear problems" disabled={!problems.value.length} onClick={clearProblems} />}
      >
        {problems.value.length
          ? <ul class={styles.list}>{problems.value.map((entry) => <Entry key={entry.id} entry={entry} />)}</ul>
          : <ui.Empty>Nothing has gone wrong in this session.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
