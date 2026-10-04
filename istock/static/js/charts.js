/* Shared Chart.js defaults for i-STOCK: quiet, data-first. Load after chart.umd.min.js. */
(function () {
  if (!window.Chart) return;
  const d = Chart.defaults;
  d.font.family = '"Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif';
  d.font.size = 12.5;
  d.color = '#64748b';
  d.borderColor = '#eef1f6';
  d.maintainAspectRatio = false;
  d.animation = false;
  d.plugins.legend.align = 'start';
  d.plugins.legend.labels.boxWidth = 10;
  d.plugins.legend.labels.boxHeight = 10;
  d.plugins.legend.labels.usePointStyle = true;
  d.elements.bar.borderRadius = 6;
  d.elements.line.tension = 0.3;
  d.elements.line.borderWidth = 2;
  d.plugins.legend.labels.padding = 14;
  Object.assign(d.plugins.tooltip, {
    backgroundColor: '#0f172a', titleColor: '#fff', bodyColor: '#e2e8f0', padding: 10,
    cornerRadius: 8, boxPadding: 3, titleFont: { weight: '600' },
  });
  d.scale.grid.drawTicks = false;
  d.scale.border = { display: false };
  d.scale.ticks.padding = 6;
  window.ISTOCK_COLORS = {
    accent: '#4f46e5', accentSoft: 'rgba(79,70,229,.10)', accentBar: 'rgba(79,70,229,.75)',
    gray: '#94a3b8', dark: '#334155', amber: '#d97706', green: '#059669', greenSoft: 'rgba(5,150,105,.10)', red: '#dc2626',
  };
})();
