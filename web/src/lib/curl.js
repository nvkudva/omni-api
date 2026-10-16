import { ORIGIN } from "../api/client.js";

export function shellQuote(text) {
  return `'${text.replaceAll("'", `'\\''`)}'`;
}

export function curlJson(path, body, extra = "") {
  return `curl -s ${ORIGIN}${path} \\\n  -H 'content-type: application/json' \\\n  -d ${shellQuote(JSON.stringify(body, null, 2))}${extra}`;
}
