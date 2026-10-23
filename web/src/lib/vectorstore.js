// The Embed tab's index, kept in the browser's IndexedDB so it survives reloads
// without any server-side state. The database opens on first use: tests and
// private-mode browsers have no IndexedDB, and the tab then just works in memory.

const DATABASE = "omni-embed";
const FILES = "files";
const CHUNKS = "chunks";

let opening = null;

function open() {
  opening ??= new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB is unavailable"));
      return;
    }
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(FILES, { keyPath: "name" });
      request.result.createObjectStore(CHUNKS, { keyPath: "id" }).createIndex("file", "file");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return opening;
}

function finished(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

function all(store) {
  return new Promise((resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function deleteChunksOf(transaction, name) {
  const cursorRequest = transaction.objectStore(CHUNKS).index("file").openKeyCursor(IDBKeyRange.only(name));
  cursorRequest.onsuccess = () => {
    const cursor = cursorRequest.result;
    if (!cursor) return;
    transaction.objectStore(CHUNKS).delete(cursor.primaryKey);
    cursor.continue();
  };
}

export async function loadAll() {
  const database = await open();
  const transaction = database.transaction([FILES, CHUNKS], "readonly");
  const [files, chunks] = await Promise.all([all(transaction.objectStore(FILES)), all(transaction.objectStore(CHUNKS))]);
  return { files, chunks };
}

/** Store one file's record and chunks, replacing whatever an earlier run left for it. */
export async function putFile(file, chunks) {
  const database = await open();
  const transaction = database.transaction([FILES, CHUNKS], "readwrite");
  deleteChunksOf(transaction, file.name);
  transaction.objectStore(FILES).put(file);
  for (const chunk of chunks) transaction.objectStore(CHUNKS).put(chunk);
  await finished(transaction);
}

export async function clear() {
  const database = await open();
  const transaction = database.transaction([FILES, CHUNKS], "readwrite");
  transaction.objectStore(FILES).clear();
  transaction.objectStore(CHUNKS).clear();
  await finished(transaction);
}
