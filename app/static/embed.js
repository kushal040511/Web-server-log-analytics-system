// Show an Apache Superset dashboard inside a LogLens page (Superset embedded mode).
// The app's server asks Superset for a guest token that can view only this dashboard;
// the Superset embedded SDK renders the dashboard in an iframe from Superset itself.
async function embedSuperset(slug, mountId, showFilters = false) {
  const mount = document.getElementById(mountId);
  const msg = (html) => { mount.innerHTML = `<div class="embed-msg">${html}</div>`; };
  let info;
  try {
    const r = await fetch(`/api/guest-token/${slug}`);
    info = await r.json();
    if (!r.ok) { msg(`<b>${info.error}</b><br>Start the cluster and run <code>make bi</code>, then reload.`); return; }
  } catch (e) { msg("<b>Superset is not reachable.</b> Run <code>make up</code> and <code>make bi</code>."); return; }

  const dash = await supersetEmbeddedSdk.embedDashboard({
    id: info.uuid,
    supersetDomain: info.supersetDomain,
    mountPoint: mount,
    fetchGuestToken: async () => (await (await fetch(`/api/guest-token/${slug}`)).json()).token,
    dashboardUiConfig: { hideTitle: true, hideChartControls: false, filters: { expanded: showFilters, visible: showFilters } },
  });
  // grow the iframe to the dashboard's height, so the page scrolls instead of the frame
  const fit = async () => {
    try {
      const size = await dash.getScrollSize();
      if (size && size.height) mount.querySelector("iframe").style.height = (size.height + 24) + "px";
    } catch (e) { /* dashboard still loading */ }
  };
  setInterval(fit, 1500);
}
