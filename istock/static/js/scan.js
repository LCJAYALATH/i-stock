/* i-STOCK camera scanning workflow (html5-qrcode). */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  let scanner = null, running = false, current = null, lastCode = null, lastAt = 0, busy = false;
  const counts = { IN: 0, OUT: 0, ERR: 0 };

  const mode = () => document.querySelector('input[name="mode"]:checked').value;

  function beep(ok) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = ok ? 880 : 220; o.connect(g); g.connect(ctx.destination);
      g.gain.value = 0.1; o.start(); o.stop(ctx.currentTime + (ok ? 0.12 : 0.3));
    } catch (e) { /* audio not available */ }
    if (navigator.vibrate) navigator.vibrate(ok ? 60 : [80, 60, 80]);
  }

  function showMsg(text, kind) {
    const div = document.createElement('div');
    div.className = 'alert alert-' + kind;
    div.setAttribute('role', 'alert');
    const body = document.createElement('div');
    body.className = 'flex-1'; body.textContent = text;
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'alert-close'; btn.setAttribute('data-dismiss', '');
    btn.setAttribute('aria-label', 'Dismiss'); btn.innerHTML = '<i class="bi bi-x"></i>';
    div.append(body, btn);
    $('msg').replaceChildren(div);
  }

  function applyMode() {
    const m = mode();
    document.querySelectorAll('.in-only').forEach((el) => el.classList.toggle('hidden', m !== 'IN'));
    document.querySelectorAll('.out-only').forEach((el) => el.classList.toggle('hidden', m !== 'OUT'));
    $('modeStripe').className = 'h-1 ' + (m === 'IN' ? 'bg-emerald-600' : 'bg-accent-600');
    const tag = $('modeTag');
    tag.className = 'badge font-mono ' + (m === 'IN' ? 'badge-success' : 'badge-primary');
    tag.textContent = m;
    const b = $('submitBtn');
    b.className = 'btn h-10 px-5 ' + (m === 'IN' ? 'btn-success' : 'btn-primary');
    b.textContent = m === 'IN' ? 'Confirm receipt' : 'Confirm pick';
    updateQtyHint();
    if (!current) $('manualCode').focus();
  }

  function updateQtyHint() {
    const hint = $('qtyHint');
    if (!current) { hint.textContent = ''; return; }
    const q = parseInt($('qty').value, 10) || 0;
    if (mode() === 'OUT') {
      const left = current.on_hand - q;
      hint.className = 'help' + (left < 0 ? ' !text-red-700' : '');
      hint.textContent = left < 0 ? 'Only ' + current.on_hand + ' ' + current.unit + ' on hand.'
        : 'Balance after pick: ' + left + ' ' + current.unit + (left <= current.reorder_point ? ' (at or below reorder point)' : '');
    } else {
      hint.className = 'help';
      hint.textContent = 'Balance after receipt: ' + (current.on_hand + q) + ' ' + current.unit;
    }
  }

  function renderItem(item) {
    current = item;
    $('itemPanel').classList.remove('hidden');
    $('emptyPanel').classList.add('hidden');
    $('iName').textContent = item.name;
    $('iSku').textContent = item.sku + (item.barcode ? '  ·  ' + item.barcode : '');
    $('iOnHand').textContent = item.on_hand + ' ' + item.unit;
    $('iRop').textContent = item.reorder_point;
    $('iSs').textContent = item.safety_stock;
    $('qtyUnit').textContent = '(' + item.unit + ')';
    $('iBin').textContent = item.location || '—';
    const st = $('iStatus');
    st.className = 'badge badge-' + item.status_color; st.textContent = item.status_label;
    $('iLink').href = item.url;
    const sel = $('batchSel');
    sel.replaceChildren(new Option('Auto (first-expiry-first-out)', ''));
    item.batches.forEach((b) => sel.add(new Option(b.batch_no + ' · ' + b.qty + (b.expiry ? ' · exp ' + b.expiry : ''), b.id)));
    $('qty').value = 1;
    updateQtyHint();
    $('qty').focus(); $('qty').select();
  }

  async function lookup(code) {
    code = (code || '').trim();
    if (!code) return;
    const res = await fetch('/api/items/lookup?code=' + encodeURIComponent(code), { headers: { 'Accept': 'application/json' } });
    const data = await res.json();
    if (!data.ok) { beep(false); showMsg(data.error, 'danger'); return; }
    beep(true); $('msg').replaceChildren(); renderItem(data.item);
  }

  function onScan(text) {
    const now = Date.now();
    if (busy || (text === lastCode && now - lastAt < 2500)) return; // debounce repeated frames
    lastCode = text; lastAt = now;
    lookup(text);
  }

  async function startCamera(deviceId) {
    $('camPlaceholder').classList.add('hidden');
    if (!window.Html5Qrcode) { $('camPlaceholder').classList.remove('hidden'); showMsg('Scanner library failed to load. Check your internet connection.', 'danger'); return; }
    if (!scanner) {
      const F = Html5QrcodeSupportedFormats;
      scanner = new Html5Qrcode('reader', { formatsToSupport: [F.QR_CODE, F.CODE_128, F.EAN_13, F.EAN_8, F.UPC_A, F.CODE_39], verbose: false });
    }
    try {
      await scanner.start(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: 'environment' },
        { fps: 10, qrbox: (w, h) => ({ width: Math.min(300, w * 0.85), height: Math.min(180, h * 0.6) }) },
        onScan, () => {});
      running = true;
      $('camPlaceholder').classList.add('hidden');
      $('camBtn').innerHTML = '<i class="bi bi-stop-circle"></i> Stop camera';
      const cams = await Html5Qrcode.getCameras();
      const sel = $('camSelect');
      if (cams.length > 1 && sel.options.length === 0) {
        cams.forEach((c) => sel.add(new Option(c.label || 'Camera', c.id)));
        sel.classList.remove('hidden');
      }
    } catch (err) {
      $('camPlaceholder').classList.remove('hidden');
      showMsg('Could not start the camera: ' + err + '. You can still type or use a USB scanner.', 'warning');
    }
  }

  async function stopCamera() {
    if (scanner && running) { await scanner.stop(); }
    running = false;
    $('camPlaceholder').classList.remove('hidden');
    $('camBtn').innerHTML = '<i class="bi bi-camera-video"></i> Start camera';
  }

  function addHistory(entry) {
    const tb = $('history');
    if (tb.querySelector('[data-empty]')) tb.replaceChildren();
    const tr = document.createElement('tr');
    const cell = (cls, text) => { const td = document.createElement('td'); td.className = cls; td.textContent = text; return td; };
    const time = cell('whitespace-nowrap tabular-nums text-gray-500', new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    const type = document.createElement('td');
    const b = document.createElement('span');
    b.className = 'badge font-mono ' + (entry.ok ? (entry.mode === 'IN' ? 'badge-success' : 'badge-primary') : 'badge-danger');
    b.textContent = entry.ok ? entry.mode : 'ERR';
    type.append(b);
    const item = document.createElement('td');
    const nm = document.createElement('div'); nm.className = 'truncate text-gray-900'; nm.textContent = entry.name;
    const sub = document.createElement('div'); sub.className = 'truncate text-xs2 ' + (entry.ok ? 'mono text-gray-500' : 'text-red-700');
    sub.textContent = entry.ok ? entry.sku : entry.error;
    item.append(nm, sub);
    const qty = cell('num font-medium', entry.ok ? (entry.mode === 'IN' ? '+' : '−') + entry.qty : '—');
    const bal = cell('num', entry.ok ? String(entry.balance) : '—');
    tr.append(time, type, item, qty, bal);
    tb.prepend(tr);
    counts[entry.ok ? entry.mode : 'ERR'] += 1;
    $('cntIn').textContent = counts.IN; $('cntOut').textContent = counts.OUT; $('cntErr').textContent = counts.ERR;
  }

  $('camBtn').addEventListener('click', () => (running ? stopCamera() : startCamera()));
  $('camSelect').addEventListener('change', async (e) => { await stopCamera(); startCamera(e.target.value); });
  $('manualForm').addEventListener('submit', (e) => { e.preventDefault(); lookup($('manualCode').value); $('manualCode').value = ''; });
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener('change', applyMode));
  document.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
    const q = $('qty'); q.value = Math.max(1, (parseInt(q.value, 10) || 0) + parseInt(b.dataset.step, 10)); updateQtyHint();
  }));
  $('qty').addEventListener('input', updateQtyHint);
  const resetPanel = () => { $('itemPanel').classList.add('hidden'); $('emptyPanel').classList.remove('hidden'); current = null; $('manualCode').focus(); };
  $('cancelBtn').addEventListener('click', resetPanel);

  $('txnForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!current || busy) return;
    busy = true; $('submitBtn').disabled = true;
    const payload = {
      mode: mode(), item_id: current.id, qty: parseInt($('qty').value, 10),
      reference: $('reference').value,
      batch_no: $('batchNo').value, expiry_date: $('expiry').value,
      batch_id: $('batchSel').value, client_id: $('client').value,
    };
    try {
      const res = await fetch('/api/scan', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        beep(true); showMsg(data.message, 'success');
        addHistory({ ok: true, mode: payload.mode, name: data.item.name, sku: data.item.sku, qty: payload.qty, balance: data.item.on_hand });
        resetPanel();
        $('batchNo').value = ''; $('expiry').value = ''; $('reference').value = '';
      } else {
        beep(false); showMsg(data.error, 'danger');
        addHistory({ ok: false, mode: payload.mode, name: current.name, error: data.error });
      }
    } catch (err) {
      showMsg('Network error: ' + err, 'danger');
    } finally {
      busy = false; $('submitBtn').disabled = false; if (!current) $('manualCode').focus();
    }
  });

  applyMode();
})();
