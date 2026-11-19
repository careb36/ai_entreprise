/** Hand a file to the browser to save: no network, nothing kept after the click. */
export function saveBlob(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

/** A text file (Markdown, JSON) to save under `name`. */
export function saveText(name: string, text: string, type = 'text/plain'): void {
  saveBlob(name, new Blob([text], { type: `${type};charset=utf-8` }))
}
