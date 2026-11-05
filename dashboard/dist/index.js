/*
 * Sluice dashboard entry — API-only.
 *
 * The dashboard plugin loader injects `entry` for every manifest and reports "NO_REGISTER"
 * when the bundle does not call `window.__HERMES_PLUGINS__.register`. Sluice's UI is the
 * desktop pane (`desktop/plugin.js`), and a browser dashboard is an explicit v1 non-goal
 * (REQUIREMENTS.md §5), so this registers a component that renders nothing. The tab is
 * marked hidden in manifest.json; the routes in `plugin_api.py` are the real surface.
 */
(function registerSluiceApiOnly() {
  var registry = window.__HERMES_PLUGINS__
  if (!registry || typeof registry.register !== 'function') return
  registry.register('sluice', function SluiceApiOnly() {
    return null
  })
})()
