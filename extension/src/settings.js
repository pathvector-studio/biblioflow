// Settings live in the extension's local storage area only. Secrets (pat, cfClientSecret) are read by the background script
// and never sent to the popup or to page scripts.

const ext = globalThis.browser ?? globalThis.chrome;

export const DEFAULTS = { baseUrl: "", pat: "", cfClientId: "", cfClientSecret: "", maxPdfMb: 50, lastWorkspaceId: "" };

export async function loadSettings() {
  const s = await ext.storage.local.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...s };
}

export async function saveSettings(patch) {
  await ext.storage.local.set(patch);
}

export const ext_ = ext;
