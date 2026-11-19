/* ESP8266 Bulb Control browser app. No build step or external runtime required. */
(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const main = $('#main-content');
  const state = { user: null, csrf: '', currentRoute: 'home', refreshTimer: null, search: '' };
  const cloudLink = { connected:false, connectionType:'Wi-Fi', latestReading:null };
  function useCloudDevices(devices) {
    const list = devices || [];
    const device = list.filter(item=>item.status==='online').sort((a,b)=>String(b.lastSeen||'').localeCompare(String(a.lastSeen||'')))[0] || list[0] || null;
    cloudLink.connected = list.some(item=>item.status==='online');
    cloudLink.connectionType = 'Wi-Fi';
    cloudLink.latestReading = device?.latest || null;
  }
  const publicRoutes = new Set(['home', 'login', 'register', 'forgot', 'reset', 'privacy', 'terms', 'help']);
  const routeNames = { home:'Welcome', dashboard:'Home', live:'Home comfort', control:'Appliances', analytics:'Energy & costs', devices:'Connections', notifications:'Activity', settings:'Settings', help:'Help & support', privacy:'Privacy policy', terms:'Terms & conditions', login:'Sign in', register:'Create account', forgot:'Reset password', reset:'Choose a new password' };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const fmt = (value, digits = 1) => value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(digits);
  const moneyFmt = value => new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value)||0);
  const dateFmt = value => value ? new Date(value).toLocaleString([], { dateStyle:'medium', timeStyle:'short' }) : '—';
  const timeFmt = value => value ? new Date(value).toLocaleTimeString([], { hour:'numeric', minute:'2-digit' }) : '—';
  const initials = name => (String(name || 'H').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase() || 'H');
  const statusPill = status => `<span class="status-pill ${esc(status)}"><span class="status-dot ${status === 'online' ? 'online' : status === 'offline' ? 'offline' : ''}"></span>${status === 'online' ? 'Connected' : status === 'offline' ? 'Offline' : 'Setting up'}</span>`;
  const wifiConnectButtons = () => '<button class="btn btn-primary" type="button" data-action="connect-node">⌁ Connect to Wi-Fi</button>';
  const emptyState = (title, copy, link = '', label = '') => `<div class="empty-state"><span class="empty-icon" aria-hidden="true">⌂</span><strong>${esc(title)}</strong><p>${esc(copy)}</p>${link ? `<a class="btn btn-primary btn-sm" href="${esc(link)}">${esc(label)}</a>` : ''}</div>`;
  const pageHead = (title, subtitle, actions = '') => `<div class="page-head"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`;
  const card = (content, extra = '') => `<section class="card panel ${extra}">${content}</section>`;
  const errorBlock = message => `<div class="notice error">${esc(message)}</div>`;

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const isAuth = path.startsWith('/api/auth/') && !['/api/auth/logout'].includes(path);
    if (options.body !== undefined && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (state.csrf && !isAuth && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', state.csrf);
    let body = options.body;
    if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') body = JSON.stringify(body);
    const response = await fetch(path, { ...options, headers, body, credentials:'same-origin' });
    const type = response.headers.get('content-type') || '';
    const data = type.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
    return data;
  }

  function toast(message, kind = '') {
    const region = $('#toast-region');
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = message;
    region.append(el);
    setTimeout(() => el.remove(), 3800);
  }

  function currentRoute() {
    const hash = location.hash.replace(/^#\/?/, '');
    const [rawRoute, rawQuery = ''] = hash.split('?');
    return { route: rawRoute || 'home', query: new URLSearchParams(rawQuery) };
  }

  function syncShell(route, unread = null, devices = null) {
    document.body.classList.toggle('public-mode', publicRoutes.has(route));
    const title = routeNames[route] || 'Page not found';
    $('#breadcrumbs').innerHTML = `<span>ESP8266 Bulb Control</span><span class="crumb-sep">/</span><strong>${esc(title)}</strong>`;
    $('#user-name').textContent = state.user?.name || 'Guest';
    $('#user-email').textContent = state.user?.email || 'Welcome';
    $('#user-avatar').textContent = initials(state.user?.name);
    $$('.nav-link').forEach(link => link.classList.toggle('active', link.dataset.route === route));
    const homeTarget = state.user ? '#/dashboard' : '#/home';
    $$('.brand,.side-home-link').forEach(link => link.setAttribute('href', homeTarget));
    const unreadEl = $('#nav-unread');
    if (unread !== null) {
      unreadEl.textContent = unread > 99 ? '99+' : unread;
      unreadEl.hidden = !unread;
      $('#top-ping').hidden = !unread;
    }
    if (devices) {
      const online = devices.filter(d => d.status === 'online').length;
      const dot = $('#side-status-dot');
      dot.classList.toggle('online', online > 0);
      dot.classList.toggle('offline', devices.length > 0 && online === 0);
      $('#side-status').textContent = devices.length ? (online ? `${online} appliance${online === 1 ? '' : 's'} connected` : 'Appliances offline') : 'No appliance connected';
      $('#side-status-detail').textContent = devices.length ? (online ? 'Home is up to date' : 'Check appliance connection') : 'No appliances connected yet';
    }
  }

  async function render(routeInfo = currentRoute(), opts = {}) {
    const route = routeInfo.route;
    state.currentRoute = route;
    if (!publicRoutes.has(route) && !state.user) {
      location.hash = '#/login';
      return;
    }
    if (state.user && ['login','register'].includes(route)) {
      location.hash = '#/dashboard';
      return;
    }
    syncShell(route);
    if (!opts.silent) main.innerHTML = '<div class="initial-loader"><span class="spinner"></span><p>Loading your workspace…</p></div>';
    try {
      let html = '';
      switch (route) {
        case 'home': html = homePage(); break;
        case 'login': html = authPage('login'); break;
        case 'register': html = authPage('register'); break;
        case 'forgot': html = authPage('forgot'); break;
        case 'reset': html = authPage('reset', routeInfo.query); break;
        case 'dashboard': html = await dashboardPage(); break;
        case 'live': html = await livePage(); break;
        case 'control': html = await controlPage(); break;
        case 'analytics': html = await analyticsPage(routeInfo.query); break;
        case 'devices': html = await devicesPage(); break;
        case 'notifications': html = await notificationsPage(); break;
        case 'settings': html = await settingsPage(); break;
        case 'help': html = helpPage(); break;
        case 'privacy': html = legalPage('privacy'); break;
        case 'terms': html = legalPage('terms'); break;
        default: html = notFoundPage();
      }
      main.innerHTML = html;
      document.title = `${routeNames[route] || 'Not found'} · ESP8266 Bulb Control and Power Consumption`;
      syncShell(route, window._hsUnread ?? null, window._hsDevices ?? null);
      if (route === 'devices' && routeInfo.query.get('addAppliance') === '1') {
        showConnectForm();
        history.replaceState(null, '', `${location.pathname}${location.search}#/devices`);
      }
      if (route === 'settings') setSettingsTab('profile');
    } catch (error) {
      if (error.message.includes('Sign in')) {
        state.user = null; state.csrf = ''; location.hash = '#/login'; return;
      }
      main.innerHTML = `${pageHead('Something went wrong', 'ESP8266 Bulb Control could not load this view.')}<div class="notice error">${esc(error.message)} <button class="text-button" data-action="retry">Try again</button></div>`;
    }
  }

  function homePage() {
    const homeActions = state.user ? `<a class="btn btn-secondary" href="#/settings">Settings</a><a class="btn btn-primary" href="#/dashboard">Open Home</a><button class="btn btn-secondary" type="button" data-action="logout">Sign out</button>` : `<a class="btn btn-secondary" href="#/login">Sign in</a><a class="btn btn-primary" href="#/register">Get started <span aria-hidden="true">→</span></a>`;
    return `<div class="landing">
      <nav class="landing-nav"><a class="brand" href="#/home"><span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M4 15.2 16 5l12 10.2v12.3a1.5 1.5 0 0 1-1.5 1.5h-7.2v-9h-6.6v9H5.5A1.5 1.5 0 0 1 4 27.5z"/></svg></span><span class="brand-copy"><strong>ESP8266 Bulb Control</strong><small>Power Consumption</small></span></a>
      <div class="landing-nav-links"><a href="#features">Features</a><a href="#how-it-works">How it works</a><a href="#/help">Support</a></div><div class="landing-nav-actions">${homeActions}</div></nav>
      <section class="hero"><div class="hero-copy"><span class="hero-badge"><span class="live-dot"></span> A clearer view of your home</span><h1>Use energy<br><span>with confidence.</span></h1><p>See what your home uses, follow estimated bulb costs, and control relay-connected bulbs. ESP8266 Bulb Control brings the important details together in one calm, colorful dashboard.</p><div class="hero-actions"><a class="btn btn-primary" href="#/register">Create your home space <span aria-hidden="true">→</span></a><a class="btn btn-secondary" href="#features">Explore features</a></div><div class="hero-proof"><span class="proof-avatars"><span>ϟ</span><span>₹</span><span>⌂</span></span><span>Bulb control from your website over Wi-Fi</span></div></div>
      <div class="hero-visual energy-landing-visual" aria-label="Home energy illustration"><div class="hero-orb"><span class="hero-house" aria-hidden="true">⌂</span></div><div class="float-card temperature"><span class="float-label">NODEMCU</span><strong>Wi-Fi ready</strong><small>Relay connection</small></div><div class="float-card lighting"><span class="float-label">TODAY’S ENERGY</span><strong>— kWh</strong><small>Household consumption</small></div><div class="float-card connectivity"><span class="float-label"><span class="live-dot"></span>HOME STATUS</span><strong style="font-size:13px">Ready when you are</strong><small>Appliances and relays</small></div></div></section>
      <section class="section-block" id="features"><div class="section-heading"><span class="eyebrow">ENERGY, COMFORT, CONTROL</span><h2>Your home at a glance</h2><p>Understand estimated bulb energy use and control one relay through a Wi-Fi connected NodeMCU.</p></div><div class="feature-grid">
        ${feature('ϟ','Energy that makes sense','See day, week, month, and year estimated bulb use.')}${feature('₹','Costs you can plan for','Set your electricity price and see estimated costs alongside energy use.')}${feature('◉','Controls in one place','Switch each relay-connected bulb on or off from the dashboard.')}${feature('⌁','Wi-Fi connection','Connect your NodeMCU to the website through Wi-Fi.')}${feature('♧','Helpful activity','See bulb changes and device connection updates.')}${feature('▣','Separate accounts','Each user has a private account and separate device readings and usage history.')}</div></section>
      <section class="section-block soft" id="how-it-works"><div class="section-heading"><span class="eyebrow">THREE SIMPLE STEPS</span><h2>Your home, at a glance</h2><p>Connect to Wi-Fi to view live sensors and control the bulb from the website.</p></div><div class="steps"><article class="step-card"><h3>Connect to Wi-Fi</h3><p>Enter the network details for your NodeMCU.</p></article><article class="step-card"><h3>Check its connection</h3><p>View its live temperature, humidity, and natural-light readings.</p></article><article class="step-card"><h3>Control your bulb</h3><p>Turn that device’s relay-connected bulb on or off from the dashboard.</p></article></div></section>
      <section class="landing-cta"><div><h2>Make your home a little easier to understand.</h2><p>Connect to Wi-Fi to view sensor readings and control the bulb from the website.</p></div><a class="btn" href="#/register">Create your account <span aria-hidden="true">→</span></a></section>
      <footer class="site-footer"><span>© 2026 ESP8266 Bulb Control and Power Consumption</span><div class="site-footer-links"><a href="#/help">Help</a><a href="#/privacy">Privacy</a><a href="#/terms">Terms</a></div></footer>
    </div>`;
  }

  function feature(icon, title, copy) { return `<article class="feature-card"><span class="feature-icon" aria-hidden="true">${icon}</span><h3>${title}</h3><p>${copy}</p></article>`; }

  function authPage(kind, query = new URLSearchParams()) {
    const titles = {login:'Welcome back',register:'Create your ESP8266 Bulb Control account',forgot:'Reset your password',reset:'Choose a new password'};
    const descriptions = {login:'Sign in to check on your home.',register:'Set up your personal home monitoring workspace.',forgot:'Enter the email for your account. For this local setup, the reset link appears in the server window.',reset:'Use a strong password with at least 10 characters.'};
    const submit = {login:'Sign in',register:'Create account',forgot:'Send reset link',reset:'Save new password'}[kind];
    let fields = '';
    if (kind === 'register') fields += `<div class="field"><label for="auth-name">Your name</label><input id="auth-name" name="name" autocomplete="name" maxlength="80" required placeholder="e.g. Alex Morgan"></div>`;
    if (kind === 'login' || kind === 'register' || kind === 'forgot') fields += `<div class="field"><label for="auth-email">Email address</label><input id="auth-email" name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com"></div>`;
    if (kind === 'login' || kind === 'register' || kind === 'reset') fields += `<div class="field"><label for="auth-password">${kind === 'reset' ? 'New password' : 'Password'}</label><input id="auth-password" name="password" type="password" autocomplete="${kind === 'login' ? 'current-password' : 'new-password'}" minlength="10" maxlength="200" required placeholder="At least 10 characters"></div>`;
    if (kind === 'reset') fields += `<input type="hidden" name="token" value="${esc(query.get('token') || '')}">`;
    const links = kind === 'login' ? `<div class="auth-links"><a class="auth-link" href="#/forgot">Forgot password?</a><span></span></div><p class="auth-switch">New to ESP8266 Bulb Control? <a href="#/register">Create an account</a></p>` : kind === 'register' ? `<p class="auth-switch">Already have an account? <a href="#/login">Sign in</a></p>` : (kind === 'forgot' || kind === 'reset') ? `<p class="auth-switch"><a href="#/login">Back to sign in</a></p>` : '';
    const helpList = kind === 'register' ? `<ul><li>See your energy and estimated costs</li><li>Connect the NodeMCU to your Wi-Fi</li><li>Keep bulb activity and usage together</li></ul>` : `<ul><li>See what your bulb is using</li><li>Control it from the website</li><li>Review estimated energy costs</li></ul>`;
    return `<div class="auth-wrap"><aside class="auth-aside"><div><a href="#/home" class="brand" style="padding:0;color:white"><span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M4 15.2 16 5l12 10.2v12.3a1.5 1.5 0 0 1-1.5 1.5h-7.2v-9h-6.6v9H5.5A1.5 1.5 0 0 1 4 27.5z"/></svg></span><span class="brand-copy"><strong style="color:white">ESP8266 Bulb Control</strong><small style="color:#dcecff">Power Consumption</small></span></a><h2>A calmer way to care for your home.</h2><p>Live sensor readings, useful history, and bulb control from your website over Wi-Fi.</p>${helpList}</div><div class="auth-aside-foot">Energy · Comfort · Control</div></aside><section class="auth-form-side"><form class="auth-form" data-form="${kind}"><span class="eyebrow">ESP8266 BULB CONTROL</span><h1>${titles[kind]}</h1><p>${descriptions[kind]}</p>${fields}<div class="form-error" aria-live="polite"></div><button class="btn btn-primary" type="submit">${submit} <span aria-hidden="true">→</span></button>${links}</form></section></div>`;
  }

  async function dashboardPage() {
    const data = await api('/api/dashboard');
    const devices = data.devices || [];
    useCloudDevices(devices);
    window._hsDevices = devices;
    window._hsUnread = data.unreadNotifications || 0;
    const latestDevice = [...devices].filter(d=>d.latest).sort((a,b)=>String(b.latest.createdAt||'').localeCompare(String(a.latest.createdAt||'')))[0];
    const latest = latestDevice?.latest;
    const dhtCount = devices.filter(d=>d.status==='online' && d.latest?.temperature!==null && d.latest?.temperature!==undefined && d.latest?.humidity!==null && d.latest?.humidity!==undefined).length;
    const lightCount = devices.filter(d=>d.status==='online' && d.latest?.lightPct!==null && d.latest?.lightPct!==undefined).length;
    const sensorsConnected = dhtCount + lightCount;
    const cards = `<div class="grid metric-grid energy-metric-grid home-sensor-metrics">
      ${metric('Sensors connected',sensorsConnected,`${dhtCount} temperature & humidity · ${lightCount} light`,'◉','blue')}
      ${metric('Temperature',latest?.temperature===null||latest?.temperature===undefined?'—':`${fmt(latest.temperature)} °C`,latestDevice?`${latestDevice.status==='online'?'Live':'Last reading'} · ${latestDevice.name}`:'Waiting for DHT11','♨','amber')}
      ${metric('Humidity',latest?.humidity===null||latest?.humidity===undefined?'—':`${fmt(latest.humidity)}%`,latestDevice?`${latestDevice.status==='online'?'Live':'Last reading'} · ${latestDevice.name}`:'Waiting for DHT11','◌','green')}
      ${metric('Natural light',latest?.lightPct===null||latest?.lightPct===undefined?'—':`${fmt(latest.lightPct,0)}%`,latestDevice?`${latestDevice.status==='online'?'Live':'Last reading'} · ${latest.lightLevel||'LDR'}`:'Waiting for LDR','☼','purple')}
    </div>`;
    const bulbControls = devices.length ? `<div class="home-bulb-list">${devices.map(bulbControl).join('')}</div>` : emptyState('No bulb connected','Your bulb controls will appear here when an appliance is connected.','#/devices','View connections');
    const events = data.events.length ? `<div class="activity-list">${data.events.slice(0,4).map(eventRow).join('')}</div>` : emptyState('Nothing to show yet','Appliance activity will appear here.');
    return `${pageHead('Home','Connected bulb control and energy use for your home.',`<span class="status-pill ${cloudLink.connected?'online':'waiting'}"><span class="status-dot ${cloudLink.connected?'online':''}"></span>${cloudLink.connected?'NodeMCU connected over Wi-Fi':'NodeMCU not connected'}</span>${wifiConnectButtons()}`)}${cards}<div class="grid dashboard-lower-grid"><section class="card panel"><div class="panel-head"><div><h2>Bulb control</h2><p>Turn your connected bulb on or off</p></div></div>${bulbControls}</section><section class="card panel"><div class="panel-head"><div><h2>Recent activity</h2><p>Changes around your home</p></div><a href="#/notifications" class="panel-link">View all →</a></div>${events}</section></div><div id="pair-area"></div>`;
  }
  function metric(label, value, sub, icon, tone, liveKey='', liveSubKey='') {
    const valueAttr = liveKey ? ` data-live-key="${esc(liveKey)}"` : '';
    const subAttr = liveSubKey ? ` data-live-key="${esc(liveSubKey)}"` : '';
    return `<article class="card metric-card"><div class="metric-top"><span>${esc(label)}</span><span class="metric-icon ${tone}">${icon}</span></div><div class="metric-value"${valueAttr}>${esc(value)}</div><div class="metric-sub"${subAttr}>${esc(sub)}</div></article>`;
  }
  function bulbControl(device) {
    const relayOn = device.relayOn;
    const enabled = device.status==='online' && device.relayOn !== null;
    return `<article class="home-bulb-card"><div class="home-bulb-info"><span class="home-bulb-icon ${relayOn?'is-on':''}" data-live-relay-icon aria-hidden="true">💡</span><div><strong>${esc(device.name)}</strong><small><span data-live-relay-label>${relayOn===null?'Waiting for status':relayOn?'Bulb is on':'Bulb is off'}</span> · ${statusPill(device.status)}</small></div></div><div class="home-bulb-actions"><button class="btn btn-control-on" type="button" data-action="relay-command" data-device="${esc(device.id)}" data-state="true" data-live-relay-control ${!enabled?'disabled':''}>Bulb ON</button><button class="btn btn-control-off" type="button" data-action="relay-command" data-device="${esc(device.id)}" data-state="false" data-live-relay-control ${!enabled?'disabled':''}>Bulb OFF</button></div></article>`;
  }
  function eventRow(event) {
    return `<div class="activity-row"><span class="activity-icon ${event.state ? 'on':''}">${event.state ? '◉':'○'}</span><span><strong>${esc(event.device_name)} turned ${event.state ? 'on':'off'}</strong><small>${esc(dateFmt(event.created_at))}</small></span></div>`;
  }

  function chartEmptyPanel(title, message) { return `<div class="chart-empty"><div><strong>${esc(title)}</strong>${esc(message)}</div></div>`; }
  async function chartPanel(deviceId, range, title, keys, labels, colors) {
    const data = await api(`/api/readings?range=${encodeURIComponent(range)}&deviceId=${encodeURIComponent(deviceId)}`);
    if (!data.readings.length) return `<div class="chart-empty"><div><strong>No readings to chart yet</strong>Connect the device to start building a history.</div></div>`;
    return chartSvg(data.readings, keys, labels, colors);
  }
  function chartSvg(rows, keys, labels, colors, height = 205) {
    const w = 800, h = height, left = 42, right = 12, top = 14, bottom = 28;
    const valid = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
    const all = keys.flatMap(k => rows.map(r => r[k]).filter(valid).map(Number));
    if (!all.length) return `<div class="chart-empty"><div><strong>No values available</strong>This period has no valid sensor readings.</div></div>`;
    let min = Math.min(...all), max = Math.max(...all);
    if (min === max) { min -= 1; max += 1; }
    const xAt = i => left + (rows.length <= 1 ? 0 : i * (w-left-right)/(rows.length-1));
    const yAt = value => top + (max-value)*(h-top-bottom)/(max-min);
    let parts = '';
    for (let i=0;i<5;i++) {
      const y = top + i*(h-top-bottom)/4;
      const val = max - i*(max-min)/4;
      parts += `<line x1="${left}" y1="${y}" x2="${w-right}" y2="${y}" stroke="#e8eef4" stroke-width="1"/><text x="${left-8}" y="${y+3}" text-anchor="end" font-size="10" fill="#92a0af">${val.toFixed(0)}</text>`;
    }
    keys.forEach((key, idx) => {
      const points = rows.map((r,i) => valid(r[key]) ? `${xAt(i)},${yAt(Number(r[key]))}` : null).filter(Boolean).join(' ');
      const lastValid = rows.findLastIndex ? rows.findLastIndex(r=>valid(r[key])) : rows.map(r=>valid(r[key])).lastIndexOf(true);
      if (points) parts += `<polyline points="${points}" fill="none" stroke="${colors[idx]}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${xAt(lastValid)}" cy="${yAt(Number(rows[lastValid][key]))}" r="4" fill="${colors[idx]}"/>`;
    });
    const ticks = [0, Math.floor((rows.length-1)/2), rows.length-1].filter((v,i,a)=>a.indexOf(v)===i);
    ticks.forEach(i => { const date = new Date(rows[i].createdAt); const label = date.toLocaleDateString([], {month:'short',day:'numeric'}); parts += `<text x="${xAt(i)}" y="${h-5}" text-anchor="middle" font-size="10" fill="#92a0af">${esc(label)}</text>`; });
    const legend = labels.map((label,i)=>`<span><i class="legend-dot ${i===1?'green':i===2?'orange':''}" style="background:${colors[i]}"></i>${esc(label)}</span>`).join('');
    return `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(labels.join(', '))} chart">${parts}</svg><div class="chart-legend">${legend}</div>`;
  }

  async function livePage() {
    const {devices=[]} = await api('/api/devices');
    useCloudDevices(devices);
    window._hsDevices = devices;
    const panels = devices.length ? (await Promise.all(devices.map(async d=>{
      const r=d.latest || {};
      const sampleTime=r.createdAt?timeFmt(r.createdAt):'Waiting';
      const history=await api(`/api/readings?range=day&deviceId=${encodeURIComponent(d.id)}`);
      const rows=history.readings || [];
      const temperatureChart=rows.some(row=>row.temperature!==null)?chartSvg(rows,['temperature'],['Temperature °C'],['#2563eb']):chartEmptyPanel('No temperature history yet','DHT11 samples will appear here.');
      const humidityChart=rows.some(row=>row.humidity!==null)?chartSvg(rows,['humidity'],['Humidity %'],['#e43d65']):chartEmptyPanel('No humidity history yet','DHT11 samples will appear here.');
      const lightChart=rows.some(row=>row.lightPct!==null)?chartSvg(rows,['lightPct'],['Natural light %'],['#e7a600']):chartEmptyPanel('No light history yet','LDR samples will appear here.');
      return `<section class="card panel"><div class="panel-head"><div><h2>${esc(d.name)}</h2><p>${statusPill(d.status)} · Last sensor update ${esc(sampleTime)}</p></div></div><div class="grid metric-grid home-sensor-metrics">${metric('Temperature',r.temperature==null?'—':`${fmt(r.temperature)} °C`,'DHT11 · current reading','♨','blue')}${metric('Humidity',r.humidity==null?'—':`${fmt(r.humidity)}%`,'DHT11 · relative humidity','◌','red')}${metric('Natural light',r.lightPct==null?'—':`${fmt(r.lightPct,0)}%`,r.lightLevel||'LDR reading','☼','amber')}</div><div class="grid panel-grid"><section class="card panel"><h2>Temperature · last 24 hours</h2>${temperatureChart}</section><section class="card panel"><h2>Humidity · last 24 hours</h2>${humidityChart}</section><section class="card panel"><h2>Natural light · last 24 hours</h2>${lightChart}</section></div></section>`;
    }))).join('') : `<section class="card panel"><div class="notice warning">Connect to Wi-Fi to see live sensor readings and control the bulb here.</div></section>`;
    return `${pageHead('Home comfort','Live sensor readings and recent history from every connected device.',wifiConnectButtons())}<div class="grid panel-grid">${panels}</div><div id="pair-area"></div>`;
  }

  async function controlPage() {
    const {devices=[]} = await api('/api/devices');
    useCloudDevices(devices);
    window._hsDevices = devices;
    if (!devices.length) return `${pageHead('Appliances','Control the things you have connected at home.',wifiConnectButtons())}<div class="card">${emptyState('No appliances connected yet','Your appliance controls will appear here when a device is connected.','#/devices','View connections')}</div>`;
    const cards = devices.map(d=>{
      const relayOn = d.relayOn;
      const controlsReady = d.status==='online' && d.relayOn !== null;
      return `<section class="card control-card appliance-control-card"><div class="appliance-control-head"><span class="appliance-tile">⌂</span><div class="appliance-control-name"><strong>${esc(d.name)}</strong><small data-live-relay-label>${relayOn===null?'Waiting for first update':relayOn?'Currently on':'Currently off'}</small></div>${statusPill(d.status)}</div><div class="bulb-visual"><span class="bulb ${relayOn?'on':''}" data-live-relay-icon aria-label="${esc(d.name)} ${relayOn?'on':'off'}">💡</span></div><div class="control-buttons"><button class="btn btn-control-on" data-action="relay-command" data-device="${esc(d.id)}" data-state="true" data-live-relay-control ${!controlsReady?'disabled':''}>● &nbsp;Bulb ON</button><button class="btn btn-control-off" data-action="relay-command" data-device="${esc(d.id)}" data-state="false" data-live-relay-control ${!controlsReady?'disabled':''}>○ &nbsp;Bulb OFF</button></div>${d.status!=='online'?'<p class="notice warning" style="margin:12px 0 0">This device is offline. Check its Wi-Fi connection.</p>':''}</section>`;
    }).join('');
    const events = await api('/api/dashboard');
    const history = events.events.length ? `<div class="activity-list">${events.events.map(eventRow).join('')}</div>` : emptyState('Nothing to show yet','Appliance changes will appear here.');
    return `${pageHead('Appliances','Switch connected appliances on or off.',wifiConnectButtons())}<div class="grid control-grid">${cards}</div><section class="card panel" style="margin-top:16px"><div class="panel-head"><div><h2>Recent activity</h2><p>Appliance changes in your home</p></div></div>${history}</section>`;
  }

  async function analyticsPage(query) {
    const range = ['day','week','month','year'].includes(query.get('range')) ? query.get('range') : 'day';
    const data = await api(`/api/energy?range=${range}`);
    const devices = await api('/api/devices');
    const deviceRows = devices.devices || [];
    useCloudDevices(deviceRows);
    window._hsDevices = deviceRows;
    const titles={day:'Today',week:'This week',month:'This month',year:'This year'};
    const tabs=`<nav class="range-tabs" aria-label="Energy period">${[['day','Day'],['week','Week'],['month','Month'],['year','Year']].map(([value,label])=>`<a class="range-tab ${range===value?'active':''}" href="#/analytics?range=${value}">${label}</a>`).join('')}</nav>`;
    const cards=`<div class="grid metric-grid energy-metric-grid energy-summary-grid">${metric(`Energy ${titles[range].toLowerCase()}`,`${fmt(data.energyKwh,2)} kWh`,data.calculation==='metered'?'Measured consumption':'Estimated consumption','▤','amber')}${metric('Estimated cost',moneyFmt(data.cost),`At ${moneyFmt(data.ratePerKwh)} per kWh`,'₹','green')}${metric('Appliances on',String(data.appliancesOn),'Currently connected and running','⌂','purple')}</div>`;
    const usage=data.appliances.length?`<div class="energy-appliance-list">${data.appliances.map(item=>`<article class="energy-appliance-card"><div class="energy-appliance-head"><span class="appliance-tile">⌂</span><div><strong>${esc(item.name)}</strong><small>${statusPill(item.status)} · ${item.calculation==='metered'?'Measured use':'Estimated use'}</small></div><span class="energy-appliance-total">${moneyFmt(item.cost)}</span></div><div class="energy-appliance-stats"><div><small>${titles[range]} energy</small><strong>${fmt(item.energyKwh,3)} kWh</strong></div></div><div class="energy-appliance-foot"><span>${item.relayOn?'Currently on':'Currently off'}</span></div></article>`).join('')}</div>`:emptyState('No energy data yet','Appliance energy and cost will appear here after readings are received.','#/devices','View connections');
    const rateCard=`<section class="card tariff-card"><span class="tariff-icon">₹</span><div class="tariff-copy"><strong>Your electricity rate</strong><small>Use the price from your electricity bill to calculate estimated costs.</small></div><label class="tariff-input"><span>₹ per kWh</span><input id="energy-rate" type="number" min="0" max="100000" step="0.01" value="${esc(fmt(data.ratePerKwh,2))}" aria-label="Electricity price per kilowatt-hour"></label><button class="btn btn-primary btn-sm" data-action="save-energy-rate">Save rate</button></section>`;
    return `${pageHead('Energy & costs','Understand what your home uses and what it may cost.',`<button class="btn btn-secondary" data-action="print">↗ &nbsp;Share report</button>`)}${tabs}${cards}<div class="energy-report-bottom"><section><div class="panel-head"><div><h2>Energy by appliance</h2><p>${titles[range]} · energy and estimated cost</p></div></div>${usage}</section><section>${rateCard}</section></div>`;
  }

  async function devicesPage() {
    const {devices=[]} = await api('/api/devices'); window._hsDevices = devices;
    useCloudDevices(devices);
    const list = devices.length ? `<div class="grid appliance-library">${devices.map(d=>`<article class="card appliance-library-card"><div class="appliance-library-head"><span class="appliance-tile">⌂</span><div><strong>${esc(d.name)}</strong><small>${statusPill(d.status)}</small></div></div><div class="appliance-library-foot"><span>Last update</span><strong>${esc(d.lastSeen?timeFmt(d.lastSeen):'Waiting')}</strong></div><div class="appliance-library-foot"><span>Temperature / humidity</span><strong>${d.latest?.temperature==null?'—':`${fmt(d.latest.temperature)} °C / ${fmt(d.latest.humidity)}%`}</strong></div><div class="appliance-library-foot"><span>Natural light</span><strong>${d.latest?.lightPct==null?'—':`${fmt(d.latest.lightPct,0)}%`}</strong></div><button class="text-button remove-appliance" data-action="delete-device" data-device="${esc(d.id)}" data-name="${esc(d.name)}">Remove appliance</button></article>`).join('')}</div>` : `<div class="card empty-connect-card">${emptyState('No appliance connected','Connect to Wi-Fi to see sensor readings and bulb controls.')}</div>`;
    const online = devices.filter(d=>d.status==='online').length;
     return `${pageHead('Connections','Manage your Wi-Fi connections.',wifiConnectButtons())}<section class="connection-welcome card"><div class="connection-welcome-icon">⌁</div><div><span class="eyebrow">WI-FI CONNECTION</span><h2>${cloudLink.connected?'NodeMCU connected over Wi-Fi':'Connect an appliance to Wi-Fi'}</h2><p>Each NodeMCU sends DHT11 temperature and humidity, LDR light readings, and relay status to this website. The website can command its relay.</p></div></section><div class="appliance-library-heading"><h2>Your appliances</h2><span>${devices.length} configured</span></div>${list}<div id="pair-area"></div>`;
  }

  async function notificationsPage() {
    const {notifications=[]} = await api('/api/notifications');
    const unread = notifications.filter(n=>!n.read).length; window._hsUnread = unread;
    const actions = notifications.length ? `<button class="btn btn-secondary btn-sm" data-action="read-all">Mark all read</button><button class="btn btn-secondary btn-sm" data-action="clear-notifications">Clear history</button>` : '';
    const rows = notifications.length ? notifications.map(n=>`<article class="notification-item ${n.read?'':'unread'}"><span class="notification-symbol ${esc(n.kind)}">${n.kind==='temperature'?'♨':n.kind==='device'?'▣':n.kind==='appliance'?'◉':'☼'}</span><div class="notification-content"><strong>${esc(n.title)}</strong><p>${esc(n.message)}</p><small>${esc(dateFmt(n.createdAt))}${n.deviceName?` · ${esc(n.deviceName)}`:''}${n.read?' · Read':' · New'}</small></div><div class="notification-actions">${!n.read?`<button class="btn btn-secondary btn-sm" data-action="read-notification" data-id="${n.id}">Mark read</button>`:''}</div></article>`).join('') : emptyState('You’re all caught up','Device connection changes and sensor alerts will appear here.');
    return `${pageHead('Notifications',`${unread} unread · alerts from your devices`,actions)}<section class="card">${rows}</section>`;
  }

  async function settingsPage() {
    const {profile} = await api('/api/profile');
    const prefs = {temperature:!!profile.notify_temperature,light:!!profile.notify_light,device:!!profile.notify_device,appliance:!!profile.notify_appliance};
    return `${pageHead('Profile & settings','Manage your account and ESP8266 Bulb Control preferences.')}<div class="settings-layout"><nav class="card settings-nav" aria-label="Settings sections"><a href="#profile" data-settings-tab="profile" class="active">Profile</a><a href="#notifications-settings" data-settings-tab="notifications">Notifications</a><a href="#appearance" data-settings-tab="appearance">Appearance</a><a href="#security" data-settings-tab="security">Security</a></nav><div class="card settings-panel" id="settings-panel">
      <section data-settings-section="profile"><h2>Personal details</h2><p>Update the name shown in your ESP8266 Bulb Control workspace.</p><form data-form="profile"><div class="field"><label for="profile-name">Full name</label><input id="profile-name" name="name" value="${esc(profile.name)}" maxlength="80" required></div><div class="field"><label for="profile-email">Email</label><input id="profile-email" value="${esc(profile.email)}" disabled><span class="field-help">Email address is used to sign in.</span></div><div class="form-error" aria-live="polite"></div><div class="form-actions"><button class="btn btn-primary" type="submit">Save profile</button></div></form></section>
      <section data-settings-section="notifications" hidden><h2>Notification preferences</h2><p>Choose which alerts are saved to your notification history.</p><form data-form="preferences">${preference('temperature','Temperature alerts','High temperature and sensor availability',prefs.temperature)}${preference('light','Light level alerts','Very low or very high ambient light',prefs.light)}${preference('device','Connection updates','Appliances connecting or disconnecting',prefs.device)}${preference('appliance','Appliance activity','Appliances being switched on or off',prefs.appliance)}<div class="form-error" aria-live="polite"></div><div class="form-actions"><button class="btn btn-primary" type="submit">Save preferences</button></div></form></section>
      <section data-settings-section="appearance" hidden><h2>Appearance</h2><p>Choose the theme for this browser and account.</p><div class="preference-row"><div><strong>Color theme</strong><small>Light or dark dashboard appearance</small></div><select id="theme-choice" aria-label="Color theme"><option value="light" ${profile.theme==='light'?'selected':''}>Light</option><option value="dark" ${profile.theme==='dark'?'selected':''}>Dark</option></select></div><div class="form-actions"><button class="btn btn-primary" data-action="save-theme">Save theme</button></div></section>
      <section data-settings-section="security" hidden><h2>Change password</h2><p>Use at least 10 characters. Changing your password signs out other sessions.</p><form data-form="password"><div class="field"><label for="current-password">Current password</label><input id="current-password" name="currentPassword" type="password" autocomplete="current-password" required></div><div class="field"><label for="new-password">New password</label><input id="new-password" name="newPassword" type="password" minlength="10" autocomplete="new-password" required></div><div class="form-error" aria-live="polite"></div><div class="form-actions"><button class="btn btn-primary" type="submit">Update password</button></div></form></section>
    </div></div>`;
  }
  function preference(key,title,description,checked) { return `<label class="preference-row"><span><strong>${title}</strong><small>${description}</small></span><input type="checkbox" name="${key}" ${checked?'checked':''} aria-label="${title}"></label>`; }
  function setSettingsTab(tab) {
    $$('[data-settings-tab]').forEach(a=>a.classList.toggle('active',a.dataset.settingsTab===tab));
    $$('[data-settings-section]').forEach(s=>s.hidden=s.dataset.settingsSection!==tab);
  }

  function helpPage() {
    return `${pageHead('Help & support','Simple answers about your readings and appliance controls.')}<div class="grid panel-grid"><section class="card panel"><div class="panel-head"><div><h2>Getting started</h2><p>What to expect from your Home page</p></div></div><div class="help-steps"><article><span>01</span><div><strong>Connect the NodeMCU</strong><p>Open Connections, enter your 2.4 GHz Wi-Fi name and password, then prepare and upload the generated sketch. The deployed Render website must be reachable before you generate it.</p></div></article><article><span>02</span><div><strong>Check device status</strong><p>The sketch sends DHT11 temperature and humidity, LDR light level, and relay status over Wi-Fi.</p></div></article><article><span>03</span><div><strong>Control your bulb</strong><p>Use the Home page or Appliances page to switch the relay-connected bulb on or off.</p></div></article></div><div class="notice warning" style="margin-top:14px"><strong>Energy estimates:</strong> ESP8266 Bulb Control uses the rated watts and recorded on time. A compatible energy meter is needed for measured consumption.</div></section><section class="card panel"><div class="panel-head"><div><h2>Common questions</h2><p>Quick ways to get back to normal</p></div></div>
      ${faq('The NodeMCU will not connect.','Use a 2.4 GHz Wi-Fi network and enter its details in Connections to generate a Wi-Fi sketch.')}${faq('Where are my sensor readings?','The generated Arduino sketch reads the DHT11 on D4 and LDR on A0, then sends the readings to Home comfort over Wi-Fi.')}${faq('The page says OFF but the bulb stays on.','The sketch assumes an active-low relay. Check the module trigger type; set RELAY_ACTIVE_LOW to false only if the module is active-high. For a bulb that should be off by default, connect the relay contacts through COM and NO. Mains wiring should be done by a qualified electrician.')}${faq('Energy use is an estimate.','ESP8266 Bulb Control estimates energy from the appliance rating and on time. A compatible energy meter is needed to measure real consumption.')}${faq('My electricity price is different.','Open Energy & costs, enter the price per kWh from your bill, and save it.')}
      </section></div>`;
  }
  function faq(question, answer) { return `<details class="faq-item"><summary>${esc(question)}</summary><p>${esc(answer)}</p></details>`; }

  function legalPage(kind) {
    const privacy = kind === 'privacy';
    return `${pageHead(privacy?'Privacy policy':'Terms & conditions',privacy?'How this local installation handles account and device data.':'Guidelines for using this ESP8266 Bulb Control installation.',`<a class="btn btn-secondary btn-sm" href="#/home">Project home</a>`)}<section class="card panel privacy-content">
      ${privacy?`<p>ESP8266 Bulb Control is hosted on Render and keeps each user's account and history separate.</p><h2>Information stored</h2><p>Your account details, one-way password hash, appliance information, comfort readings, energy history, and activity are stored for your use. Wi-Fi credentials are inserted into the generated sketch in your browser and are not stored in the account.</p><h2>Device connection</h2><p>The NodeMCU connects to the website over Wi-Fi. The firmware sends DHT11 temperature and humidity, LDR light level, Wi-Fi status, and relay state.</p><h2>Your choices</h2><p>You can remove appliances and their associated records, clear activity history, or stop ESP8266 Bulb Control. Back up your home history if you want to preserve it.</p><h2>Password recovery</h2><p>Password recovery requires a configured email service.</p>`:`<p>ESP8266 Bulb Control helps you understand home energy use, comfort readings, and connected appliance controls.</p><h2>Appliance safety</h2><p>Use appliances and electrical equipment according to their manufacturer’s instructions. Mains electrical work should only be completed by a qualified electrician.</p><h2>Availability and estimates</h2><p>ESP8266 Bulb Control is available over the internet while the service and NodeMCU are connected. Energy costs are estimates based on entered appliance ratings and your electricity price unless a compatible meter is installed.</p><h2>Your home data</h2><p>Keep your account and ESP8266 Bulb Control installation private.</p>`}
    </section>`;
  }
  function notFoundPage() { return `${pageHead('Page not found','The link may be out of date.')}<div class="card">${emptyState('We couldn’t find that page','Return to your dashboard or go back to the ESP8266 Bulb Control home page.',state.user?'#/dashboard':'#/home',state.user?'Open dashboard':'Go home')}</div>`; }

  async function commandRelay(deviceId, desired) {
    if (!cloudLink.connected) throw new Error('The NodeMCU is offline. Connect it to Wi-Fi first.');
    const queued = await api(`/api/devices/${encodeURIComponent(deviceId)}/commands`,{method:'POST',body:{state:desired}});
    toast(`Bulb ${desired?'ON':'OFF'} command queued.`,'success');
    const commandPath=`/api/devices/${encodeURIComponent(deviceId)}/commands/${encodeURIComponent(queued.commandId)}`;
    for(let attempt=0;attempt<12;attempt++){
      await new Promise(resolve=>setTimeout(resolve,1500));
      const result=await api(commandPath);
      if(result.status==='complete') { toast(`NodeMCU reported relay ${desired?'ON':'OFF'}.`,'success'); await render(currentRoute(),{silent:true}); return; }
      if(result.status==='failed') throw new Error(result.error || 'The NodeMCU did not complete the relay command.');
    }
    throw new Error('The NodeMCU has not acknowledged the command yet. Check its Wi-Fi connection and relay power.');
  }

  async function action(action, el) {
    switch (action) {
      case 'menu': $('#sidebar').classList.add('open'); $('#sidebar-scrim').classList.add('open'); break;
      case 'close-menu': $('#sidebar').classList.remove('open'); $('#sidebar-scrim').classList.remove('open'); break;
      case 'user-menu': $('#user-menu').hidden = !$('#user-menu').hidden; break;
      case 'logout':
        try { await api('/api/auth/logout',{method:'POST',body:{}}); } catch (e) { /* Clear local UI even if session expired. */ }
        state.user=null;state.csrf='';window._hsDevices=null;location.hash='#/home';toast('You have signed out.');break;
      case 'retry': await render(currentRoute());break;
      case 'relay-command': await commandRelay(el.dataset.device,el.dataset.state==='true');break;
      case 'connect-node': showConnectForm();break;
      case 'open-add-appliance':
        $('#sidebar').classList.remove('open');$('#sidebar-scrim').classList.remove('open');
        if(state.currentRoute==='devices') showConnectForm();
        else location.hash='#/devices?addAppliance=1';
        break;
      case 'prepare-wifi-sketch': await prepareWifiSketch();break;
      case 'save-energy-rate': {
        const rate=Number($('#energy-rate')?.value);
        if(!Number.isFinite(rate)||rate<0||rate>100000){toast('Enter a valid price per kWh.','error');break;}
        await api('/api/energy/settings',{method:'POST',body:{ratePerKwh:rate}});toast('Your electricity rate is saved.','success');await render(currentRoute());break;
      }
      case 'rename-device': {
        const name = window.prompt('Choose a new device name:',el.dataset.name);
        if (name === null) break;
        await api(`/api/devices/${encodeURIComponent(el.dataset.device)}`,{method:'PATCH',body:{name:name.trim()}});toast('Device name updated.','success');await render();break;
      }
      case 'delete-device':
        if (!window.confirm(`Remove ${el.dataset.name} and its stored readings? This cannot be undone.`)) break;
        await api(`/api/devices/${encodeURIComponent(el.dataset.device)}`,{method:'DELETE'});toast('Device and its records removed.','success');await render();break;
      case 'read-notification': await api(`/api/notifications/${el.dataset.id}`,{method:'PATCH',body:{read:true}});await render();break;
      case 'read-all': await api('/api/notifications/read-all',{method:'POST',body:{}});toast('All notifications marked as read.','success');await render();break;
      case 'clear-notifications': if(window.confirm('Clear all notification history?')){await api('/api/notifications',{method:'DELETE'});toast('Notification history cleared.');await render();}break;
      case 'download-csv': downloadCsv();break;
      case 'print': window.print();break;
      case 'apply-analytics': applyAnalytics();break;
      case 'save-theme': await saveTheme();break;
    }
  }

  function showConnectForm() {
    let area = $('#pair-area');
    if (!area) { area=document.createElement('div'); area.id='pair-area'; main.append(area); }
    area.innerHTML = `<section class="card connect-wizard" style="margin-top:16px"><div class="panel-head"><div><span class="eyebrow">WI-FI CONNECTION</span><h2>Connect to Wi-Fi</h2></div><button class="icon-button" type="button" aria-label="Close setup" data-action="cancel-pair">×</button></div><div class="form-grid"><div class="field"><label for="wifi-ssid">Your 2.4 GHz Wi-Fi name</label><input id="wifi-ssid" maxlength="32" autocomplete="off" placeholder="Your 2.4 GHz network" required></div><div class="field"><label for="wifi-password">Wi-Fi password</label><input id="wifi-password" type="password" maxlength="63" autocomplete="new-password" placeholder="Wi-Fi password"></div></div><div class="form-error" aria-live="polite"></div><div class="form-actions"><button class="btn btn-primary" type="button" data-action="prepare-wifi-sketch">Prepare connection</button></div></section>`;
    area.scrollIntoView({behavior:'smooth',block:'center'});
  }

  function cppString(value) {
    if (/[\r\n\x00-\x1f]/.test(value)) throw new Error('Wi-Fi details cannot contain line breaks or control characters.');
    return String(value).replace(/\\/g,'\\\\').replace(/"/g,'\\"');
  }

  async function prepareWifiSketch() {
    const ssid=$('#wifi-ssid')?.value.trim() || '';
    const password=$('#wifi-password')?.value || '';
    if (!ssid) throw new Error('Enter your Wi-Fi name.');
    if (ssid.length>32 || password.length>63) throw new Error('Wi-Fi name or password is too long.');
    const setup=await api('/api/setup');
    if (!setup.firmware) throw new Error('The sensor and relay sketch template could not be loaded.');
    const pairing=await api('/api/device/pair',{method:'POST',body:{}});
    const values={
      '__WIFI_SSID__':cppString(ssid),
      '__WIFI_PASSWORD__':cppString(password),
      '__SERVER_URL__':cppString(location.origin),
      '__DEVICE_TOKEN__':cppString(pairing.deviceToken)
    };
    let firmware=setup.firmware;
    for(const [key,value] of Object.entries(values)) firmware=firmware.replaceAll(key,value);
    if (/__(?:WIFI_SSID|WIFI_PASSWORD|SERVER_URL|DEVICE_TOKEN)__/.test(firmware)) throw new Error('Sketch setup is incomplete. Refresh the page and try again.');
    const fileUrl=URL.createObjectURL(new Blob([firmware],{type:'text/plain;charset=utf-8'}));
    const link=document.createElement('a');link.href=fileUrl;link.download='ESP8266-Bulb-Control-Sensors-Relay.ino';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(fileUrl),1000);
    toast('Wi-Fi sensor and relay sketch downloaded. Upload it to the NodeMCU.','success');
    const area=$('#pair-area');
    if(area) area.insertAdjacentHTML('beforeend','<div class="notice success" style="margin-top:12px">Connection file ready for upload to the NodeMCU.</div>');
  }

  function applyAnalytics() {
    const range = $('[data-change="analytics-range"]')?.value || 'week';
    const deviceId = $('[data-change="analytics-device"]')?.value || '';
    const params = new URLSearchParams({range}); if(deviceId)params.set('deviceId',deviceId);
    if(range==='custom'){params.set('from',$('[data-value="from"]')?.value||'');params.set('to',$('[data-value="to"]')?.value||'');}
    location.hash = `#/analytics?${params}`;
  }
  function downloadCsv() {
    const routeInfo = currentRoute(); const params = new URLSearchParams(routeInfo.query);
    if(!params.has('range'))params.set('range','week');
    const link = document.createElement('a');link.href=`/api/readings.csv?${params}`;link.download='homesense-report.csv';document.body.append(link);link.click();link.remove();
  }
  async function saveTheme() {
    const profile = await api('/api/profile');
    const prefs = {temperature:!!profile.profile.notify_temperature,light:!!profile.profile.notify_light,device:!!profile.profile.notify_device,appliance:!!profile.profile.notify_appliance};
    await api('/api/profile',{method:'PUT',body:{name:profile.profile.name,theme:$('#theme-choice').value,notifications:prefs}});
    setTheme($('#theme-choice').value);toast('Appearance saved.','success');
  }
  function setTheme(theme) { document.documentElement.classList.toggle('dark-mode',theme==='dark'); }

  async function formSubmit(form) {
    const kind = form.dataset.form; const button = $('button[type="submit"]',form); const errorEl=$('.form-error',form);errorEl.textContent='';button.disabled=true;
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      if(kind==='login'||kind==='register') {
        const result=await api(`/api/auth/${kind}`,{method:'POST',body:values});state.user=result.user;state.csrf=result.csrfToken;setTheme(result.user.theme);location.hash='#/dashboard';toast(kind==='register'?'Your account is ready.':'Welcome back.','success');return;
      }
      if(kind==='forgot') { const result=await api('/api/auth/forgot',{method:'POST',body:values});form.innerHTML=`<div class="notice success">${esc(result.message)}<br><br>For local setup, check the terminal window running ESP8266 Bulb Control. The reset link expires after 30 minutes.</div><p class="auth-switch"><a href="#/login">Return to sign in</a></p>`;return; }
      if(kind==='reset') { const result=await api('/api/auth/reset',{method:'POST',body:values});toast(result.message,'success');location.hash='#/login';return; }
      if(kind==='profile') { const userName=values.name.trim(); await api('/api/profile',{method:'PUT',body:{name:userName,theme:document.documentElement.classList.contains('dark-mode')?'dark':'light',notifications:await currentPrefs()}});state.user.name=userName;$('#user-name').textContent=userName;$('#user-avatar').textContent=initials(userName);toast('Profile saved.','success');return; }
      if(kind==='preferences') { const old=await api('/api/profile');const p={temperature:!!form.elements.temperature.checked,light:!!form.elements.light.checked,device:!!form.elements.device.checked,appliance:!!form.elements.appliance.checked};await api('/api/profile',{method:'PUT',body:{name:old.profile.name,theme:old.profile.theme,notifications:p}});toast('Notification preferences saved.','success');return; }
      if(kind==='password') { await api('/api/profile/password',{method:'POST',body:values});state.user=null;state.csrf='';toast('Password updated. Sign in with your new password.','success');location.hash='#/login';return; }
    } catch(e) { errorEl.textContent=e.message; } finally { if(button.isConnected)button.disabled=false; }
  }
  async function currentPrefs() { const p=await api('/api/profile');return {temperature:!!p.profile.notify_temperature,light:!!p.profile.notify_light,device:!!p.profile.notify_device,appliance:!!p.profile.notify_appliance}; }

  function onChange(event) {
    const el=event.target;
    if(el.matches('[data-change="analytics-range"]')) applyAnalytics();
    if(el.matches('[data-change="analytics-device"]')) applyAnalytics();
    if(el.matches('[data-change="live-device"]')) location.hash=`#/live?deviceId=${encodeURIComponent(el.value)}`;
  }

  document.addEventListener('click', async event => {
    const link = event.target.closest('a');
    if (link && link.hash && !link.hash.startsWith('#/')) {
      const section = document.getElementById(link.hash.slice(1));
      if (section) { event.preventDefault(); section.scrollIntoView({behavior:'smooth'}); return; }
    }
    if (link && link.hash.startsWith('#/')) {
      $('#sidebar').classList.remove('open');$('#sidebar-scrim').classList.remove('open');$('#user-menu').hidden=true;
    }
    const tab=event.target.closest('[data-settings-tab]');
    if(tab){event.preventDefault();setSettingsTab(tab.dataset.settingsTab);return;}
    const button=event.target.closest('[data-action]');
    if(!button)return;
    const actionName=button.dataset.action;
    if(actionName==='cancel-pair'){const area=$('#pair-area');if(area)area.innerHTML='';return;}
    if(actionName==='close-pair'){await render(currentRoute());return;}
    button.disabled = ['relay-command','save-energy-rate','prepare-wifi-sketch'].includes(actionName);
    try { await action(actionName,button); } catch(e) { toast(e.message,'error'); } finally { if(button.isConnected)button.disabled=false; }
  });

  document.addEventListener('submit', event => {
    const form=event.target.closest('form[data-form]');if(!form)return;event.preventDefault();formSubmit(form);
  });
  document.addEventListener('change', onChange);
  document.addEventListener('input', event => {
    if(event.target.id==='nav-search') {
      const query=event.target.value.trim().toLowerCase();
      $$('.nav-link').forEach(link=>{link.hidden=!!query&&!link.textContent.toLowerCase().includes(query);});
      if(query && !$$('.nav-link:not([hidden])').length) toast('No matching page in navigation.');
    }
  });
  document.addEventListener('keydown', event => {
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('#nav-search').focus();}
    if(event.key==='Escape'){ $('#user-menu').hidden=true;$('#sidebar').classList.remove('open');$('#sidebar-scrim').classList.remove('open'); }
  });
  window.addEventListener('hashchange',()=>render());

  async function boot() {
    try {
      const result=await api('/api/auth/me');
      if(result.authenticated){state.user=result.user;state.csrf=result.csrfToken;setTheme(result.user.theme);}
      else {const savedTheme=localStorage.getItem('homesense-theme');if(savedTheme)setTheme(savedTheme);}
    } catch(e) { /* Public pages remain available if the API is unavailable. */ }
    if(!location.hash) location.hash='#/home'; else render();
    if(state.refreshTimer)clearInterval(state.refreshTimer);
    state.refreshTimer=setInterval(()=>{
      const pairArea=$('#pair-area');
      if(state.user && ['dashboard','live','control','devices'].includes(state.currentRoute) && !(pairArea&&pairArea.innerHTML.trim()))render(currentRoute(),{silent:true});
    },8000);
  }
  boot();
})();

