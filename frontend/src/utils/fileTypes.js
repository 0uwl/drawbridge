// Mirrors ALLOWED_EXTENSIONS in drawbridge/api/files.py exactly — keep in
// sync with the backend allow-list, not with any looser assumption about
// what "an image" or "a config" is.
export const ALLOWED_EXTENSIONS = {
  image: ['bin', 'spa', 'pkg', 'tar'],
  config: ['cfg', 'conf', 'txt'],
  script: ['py', 'tcl', 'sh'],
}

export const TYPE_PLURAL = { image: 'images', config: 'configs', script: 'scripts' }
export const TYPE_LABELS = { image: 'Image', config: 'Config', script: 'Script' }

export function inferFileType(filename) {
  const ext = filename.split('.').pop()?.toLowerCase()
  for (const [type, exts] of Object.entries(ALLOWED_EXTENSIONS)) {
    if (exts.includes(ext)) return type
  }
  return null
}

export function acceptAttr() {
  return Object.values(ALLOWED_EXTENSIONS)
    .flat()
    .map((ext) => `.${ext}`)
    .join(',')
}
