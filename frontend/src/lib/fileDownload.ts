function triggerBlobDownload(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(href)
}

function triggerUrlDownload(url: string, filename: string) {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export async function downloadSignedFile(url: string, filename: string) {
  const resolved = new URL(url, window.location.origin)
  if (resolved.origin !== window.location.origin) {
    triggerUrlDownload(resolved.toString(), filename)
    return
  }

  const res = await fetch(resolved.toString())
  if (!res.ok) throw new Error(await res.text())
  triggerBlobDownload(await res.blob(), filename)
}
