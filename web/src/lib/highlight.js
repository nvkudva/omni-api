// Tiny dependency-free syntax tinting for JSON and curl snippets.
//
// Returns [{ text, tone }] runs for <span>s. `tone` is one of:
// key, string, number, bool, null, punct (JSON) or flag, string,
// comment, punct (shell). Unknown text is tone "".
const JSON_RE = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],:])/g;

export function tintJson(source) {
  const runs = [];
  let last = 0;
  const push = (text, tone) => {
    if (text) runs.push({ text, tone });
  };
  JSON_RE.lastIndex = 0;
  let match;
  while ((match = JSON_RE.exec(source))) {
    if (match.index > last) push(source.slice(last, match.index), "");
    const [whole, str, colon, bool, nul, num, punct] = match;
    if (str) push(str, colon ? "key" : "string");
    else if (colon) push(colon, "punct");
    else if (bool) push(bool, "bool");
    else if (nul) push(nul, "null");
    else if (num) push(num, "number");
    else if (punct) push(punct, "punct");
    else push(whole, "");
    last = match.index + whole.length;
  }
  push(source.slice(last), "");
  return runs;
}

export function tintShell(source) {
  const runs = [];
  const push = (text, tone) => {
    if (text) runs.push({ text, tone });
  };
  for (const line of source.split("\n")) {
    const hash = line.indexOf("#");
    const code = hash < 0 ? line : line.slice(0, hash);
    const comment = hash < 0 ? "" : line.slice(hash);
    const parts = code.split(/('(?:[^']|'\\'')*'|--[\w-]+|-[A-Za-z])/g);
    for (const part of parts) {
      if (!part) continue;
      if (part.startsWith("'")) push(part, "string");
      else if (/^--?[\w-]+$/.test(part.trim()) && part.trim().length > 1) push(part, "flag");
      else push(part, "");
    }
    if (comment) push(comment, "comment");
    push("\n", "");
  }
  if (runs.length && runs[runs.length - 1].text === "\n") runs.pop();
  return runs;
}
