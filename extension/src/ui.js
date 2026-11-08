// Tiny helpers shared by the popup and the options page.
const ext = globalThis.browser ?? globalThis.chrome;

export const t = (key, subs) => ext.i18n.getMessage(key, subs) || key;

export function localize(root = document) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  document.documentElement.lang = ext.i18n.getUILanguage().split("-")[0];
}

export const send = (msg) => ext.runtime.sendMessage(msg);

export function errorText(code) {
  return ext.i18n.getMessage(`err_${code}`) || t("err_unknown");
}
