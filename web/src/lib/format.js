export const GB = 1024 ** 3;

export const MB = 1024 ** 2;

export const gb = (bytes) => `${(bytes / GB).toFixed(1)} GB`;

export const since = (start) => Math.round(performance.now() - start);

export function shorten(url) {
  return `${url.slice(0, 40)}…`;
}

/** A model id without its org prefix: "org/name:tag" reads as "name:tag". */
export function shortModel(id) {
  if (!id) return id;
  const slash = id.lastIndexOf("/");
  return slash >= 0 ? id.slice(slash + 1) : id;
}

export function clock(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function wavSeconds(buffer) {
  const byteRate = new DataView(buffer).getUint32(28, true);
  return byteRate ? (buffer.byteLength - 44) / byteRate : 0;
}

export function size(bytes) {
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
