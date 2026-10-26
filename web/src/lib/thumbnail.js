// A small JPEG data URL of a picked image, kept with the embedded file so results can show it after a reload.

const SIDE = 160;

export async function thumbnail(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.8);
  } catch {
    return "";
  }
}
