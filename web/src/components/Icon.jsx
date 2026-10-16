const PATHS = {
  chat: "M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v6a2.5 2.5 0 0 1-2.5 2.5H11l-4 3.5V15h0a2 2 0 0 1-2-2z",
  decide: "M5 7h2m-2 5h2m-2 5h2M10 7h9m-9 5h9m-9 5h6",
  embed: "M6 17l4-6 4 3 4-7M6 17h0M10 11h0M14 14h0M18 7h0",
  image: "M5 7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2zM5 15l4-4 4 4 2-2 4 4M14.5 9.5h0",
  video: "M4 8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM16 10.5l4-2.5v8l-4-2.5",
  stt: "M12 4a2.5 2.5 0 0 1 2.5 2.5v5a2.5 2.5 0 0 1-5 0v-5A2.5 2.5 0 0 1 12 4zM7 11.5a5 5 0 0 0 10 0M12 16.5V20",
  tts: "M5 10v4h3l4 3.5v-11L8 10zM15.5 9.5a3.5 3.5 0 0 1 0 5M17.5 7a7 7 0 0 1 0 10",
  models: "M12 4l7 4v8l-7 4-7-4V8zM5 8l7 4 7-4M12 12v8",
  health: "M4 12h4l2-5 4 10 2-5h4",
};

export function Icon({ name }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
