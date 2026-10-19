// A small preview of an image file, stored with its index record so the file list can
// show it after a reload, when the original file is no longer around.

const EDGE = 96;

/** A JPEG data URL no larger than EDGE px, or "" when the browser cannot decode the image. */
export async function of(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.7);
  } catch {
    return "";
  }
}
