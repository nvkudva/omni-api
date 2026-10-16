export const GB = 1024 ** 3;

export const gb = (bytes) => `${(bytes / GB).toFixed(1)} GB`;

export const since = (start) => Math.round(performance.now() - start);

export function shorten(url) {
  return `${url.slice(0, 40)}…`;
}

export function clock(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function wavSeconds(buffer) {
  const byteRate = new DataView(buffer).getUint32(28, true);
  return byteRate ? (buffer.byteLength - 44) / byteRate : 0;
}
