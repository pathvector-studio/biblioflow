// One source tree, two manifests. Only `background` and the Firefox-specific id differ.

export const PERMISSIONS = ["activeTab", "scripting", "storage"];
export const OPTIONAL_HOSTS = ["https://*/*", "http://*/*"];

export function buildManifest(browser, version) {
  const base = {
    manifest_version: 3,
    name: "__MSG_ext_name__",
    description: "__MSG_ext_description__",
    default_locale: "en",
    version,
    permissions: [...PERMISSIONS],
    optional_host_permissions: [...OPTIONAL_HOSTS],
    action: { default_title: "__MSG_action_title__", default_popup: "popup.html" },
    options_ui: { page: "options.html", open_in_tab: true },
  };
  if (browser === "chrome") {
    return { ...base, minimum_chrome_version: "110", background: { service_worker: "src/background.js", type: "module" } };
  }
  if (browser === "firefox") {
    return {
      ...base,
      background: { scripts: ["src/background.js"], type: "module" },
      browser_specific_settings: { gecko: { id: "{6e3f1c52-8b7a-4d4e-9a55-3b1c0a7d2f10}", strict_min_version: "128.0" } },
    };
  }
  throw new Error(`unknown browser: ${browser}`);
}
