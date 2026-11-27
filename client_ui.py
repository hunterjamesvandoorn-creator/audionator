CLIENT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Audionator</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #10110f;
      color: #f2f2e9;
      --panel: #191b17;
      --raised: #22241f;
      --border: #30332d;
      --muted: #9a9d92;
      --accent: #d3f36b;
      --danger: #f08a82;
    }
    * { box-sizing: border-box; }
    body {
      min-width: 930px; min-height: 100vh; margin: 0;
      background: radial-gradient(ellipse at 75% -18%, #303a20 0, #10110f 48%);
    }
    button, input { font: inherit; }
    button { cursor: pointer; }
    .app-shell { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100vh; }
    .sidebar {
      position: sticky; top: 0; height: 100vh; padding: 25px 14px 112px;
      border-right: 1px solid #ffffff0a; background: #141512e8;
      display: flex; flex-direction: column;
    }
    .brand { display: flex; align-items: center; gap: 11px; padding: 0 10px 31px; }
    .brand-mark {
      width: 39px; height: 39px; display: grid; place-items: center;
      border-radius: 12px; background: var(--accent); color: #171912;
      font-size: 21px; font-weight: 850;
    }
    .brand-name { font-size: 18px; font-weight: 780; letter-spacing: -.5px; }
    .brand-caption { margin-top: 3px; color: var(--muted); font-size: 10px; }
    .nav-label { padding: 0 12px; margin: 0 0 8px; color: #74786d; font-size: 9px; font-weight: 800; letter-spacing: 1.1px; }
    .nav-list { display: grid; gap: 4px; }
    .nav-item {
      min-height: 43px; display: flex; align-items: center; gap: 12px;
      padding: 0 12px; border: 0; border-radius: 9px;
      background: transparent; color: #b6b9af; text-align: left;
      font-size: 12px; font-weight: 620;
    }
    .nav-item:hover { background: #ffffff09; color: #f2f2e9; }
    .nav-item.active { background: #292c24; color: var(--accent); }
    .nav-icon { width: 20px; color: currentColor; text-align: center; font-size: 16px; }
    .nav-count { margin-left: auto; color: #777b71; font-size: 10px; }
    .connection-dot { width: 9px; height: 9px; margin-left: auto; border-radius: 50%; background: var(--danger); box-shadow: 0 0 0 3px #f08a8218; }
    .connection-dot.connected { background: #83e38b; box-shadow: 0 0 0 3px #83e38b1c, 0 0 11px #83e38b77; }
    .connection-dot.connecting { background: #edc46f; box-shadow: 0 0 0 3px #edc46f1c; }
    .sidebar-bottom { margin-top: auto; padding: 14px 12px 0; border-top: 1px solid var(--border); }
    .network-note { color: #85897e; font-size: 10px; line-height: 1.5; }
    .main { min-width: 0; padding: 31px clamp(24px, 4vw, 58px) 140px; }
    .page { display: none; max-width: 1230px; margin: 0 auto; }
    .page.active { display: block; }
    .eyebrow { color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: 1.3px; }
    .page-title { margin: 7px 0 5px; font-size: 28px; letter-spacing: -.7px; }
    .page-subtitle { max-width: 720px; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
    .page-head { margin: 0 0 23px; }
    .card { border: 1px solid var(--border); border-radius: 15px; background: #191b17ed; }
    .card-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 17px 18px; }
    .card-head h2, .card-head h3 { margin: 0; font-size: 14px; }
    .counts { color: var(--muted); font-size: 10px; }
    .music-layout { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 15px; }
    .library-tools { display: flex; align-items: center; justify-content: space-between; padding: 0 18px 12px; gap: 12px; }
    .library-tools .hint { color: var(--muted); font-size: 10px; }
    .search {
      width: min(250px, 45%); height: 34px; padding: 7px 10px;
      border: 1px solid var(--border); border-radius: 8px; outline: none;
      background: #11120f; color: #f2f2e9; font-size: 11px;
    }
    .search:focus, .field input:focus { border-color: #839b44; box-shadow: 0 0 0 3px #d3f36b18; }
    .search:disabled { opacity: .5; }
    .media-list { max-height: 480px; overflow-y: auto; padding: 0 10px 10px; }
    .media-row { min-height: 58px; display: flex; align-items: center; gap: 12px; padding: 8px 8px; border-top: 1px solid #ffffff0b; }
    .media-row:hover { background: #ffffff06; }
    .media-icon {
      width: 38px; height: 38px; flex: 0 0 auto; display: grid; place-items: center;
      border-radius: 9px; background: linear-gradient(140deg, #313a25, #242620);
      color: var(--accent); font-size: 15px;
    }
    .media-meta { min-width: 0; flex: 1; }
    .media-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
    .media-detail { margin-top: 4px; color: var(--muted); font-size: 10px; }
    .button {
      min-height: 34px; border: 0; border-radius: 8px; padding: 0 13px;
      font-size: 10px; font-weight: 750; transition: background .15s, transform .15s;
    }
    .button:active { transform: translateY(1px); }
    .button.primary { background: var(--accent); color: #171912; }
    .button.primary:hover { background: #e0fa8a; }
    .button.secondary { background: #30332d; color: #f2f2e9; }
    .button.secondary:hover { background: #3a3e34; }
    .button:disabled { opacity: .48; cursor: default; }
    .music-aside { padding: 18px; overflow: hidden; }
    .music-aside h3 { margin: 0 0 8px; font-size: 13px; }
    .music-aside p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; }
    .mini-visualizer { display: flex; align-items: end; gap: 4px; height: 63px; margin: 18px 0; }
    .mini-visualizer i { width: 7px; height: var(--level); min-height: 8px; border-radius: 8px; background: linear-gradient(#defb88, #7d9e3b); opacity: .65; transform-origin: bottom; }
    .mini-visualizer.playing i { animation: mini-equalize .7s ease-in-out infinite alternate; animation-delay: var(--delay); }
    @keyframes mini-equalize { from { transform: scaleY(.35); } to { transform: scaleY(1); } }
    .empty-state { padding: 54px 18px; text-align: center; color: var(--muted); font-size: 11px; line-height: 1.6; }
    .empty-state strong { display: block; margin-bottom: 5px; color: #e3e4dc; font-size: 13px; }
    .connect-card { max-width: 760px; padding: 21px; }
    .connect-fields { display: flex; align-items: end; gap: 10px; margin: 18px 0 13px; }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field.host { flex: 1; }
    .field.port { width: 100px; }
    .field label { color: var(--muted); font-size: 9px; font-weight: 800; letter-spacing: .8px; }
    .field input { height: 38px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; background: #11120f; color: #f2f2e9; font-size: 11px; }
    .help-text { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
    .connection-status { display: flex; align-items: center; gap: 9px; margin-top: 18px; padding: 12px; border: 1px solid var(--border); border-radius: 9px; color: var(--muted); font-size: 11px; }
    .connection-status .connection-dot { margin: 0; }
    .health-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
    .health-metric { padding: 12px; border: 1px solid var(--border); border-radius: 9px; background: #11120f; }
    .health-label { color: var(--muted); font-size: 9px; font-weight: 800; letter-spacing: .8px; text-transform: uppercase; }
    .health-value { margin-top: 6px; font-size: 18px; font-weight: 650; letter-spacing: -.4px; }
    .video-layout { display: grid; grid-template-columns: minmax(0, 1fr) 285px; align-items: start; gap: 16px; }
    .video-main, .up-next { min-width: 0; }
    .video-stage { position: relative; overflow: hidden; border: 1px solid var(--border); border-radius: 13px; background: #050605; aspect-ratio: 16 / 9; }
    .video-stage video { display: block; width: 100%; height: 100%; object-fit: contain; background: #050605; }
    .video-placeholder { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: 9px; color: var(--muted); text-align: center; font-size: 11px; }
    .video-placeholder .play-mark { width: 48px; height: 48px; display: grid; place-items: center; border-radius: 50%; background: #d3f36b20; color: var(--accent); font-size: 19px; }
    .video-title { margin: 14px 0 6px; font-size: 17px; line-height: 1.4; overflow-wrap: anywhere; }
    .video-status { color: var(--muted); font-size: 10px; }
    .video-transport { position: absolute; left: 0; right: 0; bottom: 0; z-index: 2; display: flex; align-items: center; gap: 10px; padding: 24px 14px 10px; background: linear-gradient(transparent, #000000cc); color: #f2f2e9; font-size: 10px; transition: opacity .25s; }
    .video-stage.controls-hidden .video-transport { opacity: 0; pointer-events: none; }
    .video-stage.controls-hidden { cursor: none; }
    .video-transport button { flex: 0 0 auto; width: 30px; height: 30px; border: 0; border-radius: 50%; background: #ffffff24; color: #f2f2e9; font-size: 11px; cursor: pointer; }
    .video-transport button:disabled { opacity: .35; cursor: default; }
    .video-timeline { position: relative; flex: 1; height: 16px; display: flex; align-items: center; cursor: pointer; touch-action: none; }
    .video-track { position: relative; width: 100%; height: 4px; border-radius: 2px; background: #ffffff33; overflow: hidden; transition: height .1s ease; }
    .video-timeline:hover .video-track, .video-timeline.scrubbing .video-track { height: 6px; }
    .video-buffered { position: absolute; inset: 0; }
    .video-played { position: absolute; left: 0; top: 0; bottom: 0; }
    .video-buffered i { position: absolute; top: 0; bottom: 0; background: #ffffff80; }
    .video-played { width: 0; background: var(--accent); }
    .video-thumb { position: absolute; top: 50%; left: 0; width: 13px; height: 13px; margin: -6.5px 0 0 -6.5px; border-radius: 50%; background: var(--accent); opacity: 0; transform: scale(.5); transition: opacity .1s, transform .1s; pointer-events: none; }
    .video-timeline:hover .video-thumb, .video-timeline.scrubbing .video-thumb { opacity: 1; transform: scale(1); }
    .video-hover-time { position: absolute; bottom: 20px; left: 0; transform: translateX(-50%); padding: 3px 6px; border-radius: 4px; background: #000000dd; color: #f2f2e9; white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity .1s; }
    .video-timeline:hover .video-hover-time, .video-timeline.scrubbing .video-hover-time { opacity: 1; }
    .video-time { flex: 0 0 auto; min-width: 84px; text-align: right; font-variant-numeric: tabular-nums; }
    .video-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 9px; color: var(--muted); font-size: 10px; }
    .video-controls select { font: inherit; color: inherit; background: #ffffff10; border: 1px solid var(--border); border-radius: 7px; padding: 3px 6px; margin-left: 6px; }
    .up-next { overflow: hidden; }
    .up-next .card-head { padding-bottom: 11px; }
    .up-next .media-list { max-height: 550px; padding-top: 0; }
    .up-next .media-row { gap: 8px; padding: 8px 5px; }
    .up-next .media-icon { width: 31px; height: 31px; font-size: 12px; }
    .up-next .media-title { font-size: 10px; }
    .up-next .media-detail { font-size: 9px; }
    .up-next .button { min-height: 29px; padding: 0 8px; font-size: 9px; }
    .settings-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; max-width: 850px; }
    .settings-card { padding: 18px; }
    .settings-card h2 { margin: 0 0 9px; font-size: 13px; }
    .settings-card p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
    .settings-card .button { margin-top: 14px; }
    .account-card { max-width: 600px; display: flex; gap: 16px; padding: 22px; }
    .avatar { width: 55px; height: 55px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 50%; background: #303a20; color: var(--accent); font-size: 21px; font-weight: 800; }
    .account-card h2 { margin: 2px 0 7px; font-size: 15px; }
    .account-card p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; }
    .audio-dock {
      position: fixed; z-index: 5; left: 220px; right: 0; bottom: 0;
      min-height: 88px; border-top: 1px solid var(--border);
      background: #171813f5; backdrop-filter: blur(14px);
    }
    .audio-inner { max-width: 1500px; min-height: 88px; margin: 0 auto; padding: 10px 24px; display: grid; grid-template-columns: minmax(170px, 1fr) minmax(260px, 1.45fr) minmax(120px, 1fr); align-items: center; gap: 18px; }
    .audio-track { display: flex; min-width: 0; align-items: center; gap: 11px; }
    .cover-art { width: 48px; height: 48px; flex: 0 0 auto; display: grid; place-items: center; overflow: hidden; border-radius: 7px; background: linear-gradient(145deg, #48562c, #22271a 58%, #171912); color: #e4f9a4; font-size: 19px; }
    .audio-info { min-width: 0; }
    .audio-info strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
    .audio-info span { display: block; margin-top: 4px; color: var(--muted); font-size: 9px; }
    .visualizer { height: 34px; display: flex; align-items: center; justify-content: center; gap: 3px; opacity: .28; }
    .visualizer span { width: 3px; height: var(--level); min-height: 4px; border-radius: 5px; background: var(--accent); transform: scaleY(.35); transform-origin: center; }
    .visualizer.playing { opacity: 1; }
    .visualizer.playing span { animation: equalize .58s ease-in-out infinite alternate; animation-delay: var(--delay); }
    @keyframes equalize { from { transform: scaleY(.25); } to { transform: scaleY(1); } }
    .audio-controls { min-width: 0; display: grid; grid-template-columns: 35px minmax(0, 1fr); align-items: center; gap: 7px 10px; }
    .play-button { width: 33px; height: 33px; grid-row: 1 / span 2; border: 0; border-radius: 50%; background: var(--accent); color: #171912; font-size: 13px; font-weight: 850; }
    .timeline-row { display: flex; align-items: center; gap: 8px; }
    .timeline { width: 100%; margin: 0; accent-color: var(--accent); }
    .audio-time { min-width: 29px; color: var(--muted); text-align: center; font-size: 9px; }
    .dock-action { justify-self: end; }
    .status-line { min-height: 16px; margin-top: 10px; color: var(--muted); font-size: 10px; }
    .status-line.error { color: var(--danger); }
    @media (max-width: 1100px) {
      .app-shell { grid-template-columns: 190px minmax(0, 1fr); }
      .audio-dock { left: 190px; }
      .sidebar { padding-left: 10px; padding-right: 10px; }
      .music-layout { grid-template-columns: minmax(0, 1fr) 180px; }
      .video-layout { grid-template-columns: minmax(0, 1fr) 250px; }
    }
    @media (max-width: 980px) {
      .app-shell { grid-template-columns: 170px minmax(0, 1fr); }
      .audio-dock { left: 170px; }
      .music-layout { grid-template-columns: 1fr; }
      .music-aside { display: none; }
      .video-layout { grid-template-columns: 1fr; }
      .up-next .media-list { max-height: 220px; }
      .audio-inner { grid-template-columns: minmax(130px, .8fr) minmax(230px, 1.4fr) 30px; gap: 10px; }
      .dock-action { display: none; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A</div>
        <div><div class="brand-name">Audionator</div><div class="brand-caption">YOUR SHARED SPACE</div></div>
      </div>
      <div class="nav-label">LIBRARY</div>
      <nav class="nav-list" aria-label="Main navigation">
        <button class="nav-item" data-view="music"><span class="nav-icon">♫</span><span>Music</span><span id="music-count" class="nav-count">—</span></button>
        <button class="nav-item" data-view="videos"><span class="nav-icon">▣</span><span>Videos</span><span id="video-count" class="nav-count">—</span></button>
      </nav>
      <div class="nav-label" style="margin-top:27px">APP</div>
      <nav class="nav-list" aria-label="App navigation">
        <button class="nav-item active" data-view="connect"><span class="nav-icon">⌁</span><span>Connect</span><span id="connection-dot" class="connection-dot" role="img" aria-label="Disconnected"></span></button>
        <button class="nav-item" data-view="settings"><span class="nav-icon">⚙</span><span>Settings</span></button>
        <button class="nav-item" data-view="account"><span class="nav-icon">◉</span><span>Account</span></button>
      </nav>
      <div class="sidebar-bottom">
        <div class="network-note">Media streams directly from your server over your local network.</div>
      </div>
    </aside>

    <main class="main">
      <section id="page-music" class="page">
        <div class="page-head">
          <div class="eyebrow">YOUR MUSIC</div>
          <h1 class="page-title">Listen together</h1>
          <p class="page-subtitle">Choose a track from the shared library. Audionator receives it securely over your local network, then plays it here.</p>
        </div>
        <div class="music-layout">
          <section class="card">
            <div class="card-head"><h2>Shared music</h2><span id="audio-counts" class="counts">Connect to browse</span></div>
            <div class="library-tools">
              <span class="hint">MP3 audio</span>
              <input id="audio-search" class="search" type="search" placeholder="Find a track" aria-label="Search music" disabled>
            </div>
            <div id="audio-list" class="media-list" aria-live="polite">
              <div class="empty-state"><strong>Your music will show up here</strong>Connect to the server to browse its library.</div>
            </div>
          </section>
          <aside class="card music-aside">
            <h3>Now in your room</h3>
            <p id="music-server-note">Connect to a server to see its shared collection.</p>
            <div id="mini-visualizer" class="mini-visualizer" aria-hidden="true"></div>
            <p>Original MP3 files are received without re-encoding. Use the player below to pause or seek.</p>
          </aside>
        </div>
      </section>

      <section id="page-videos" class="page">
        <div class="page-head">
          <div class="eyebrow">WATCH TOGETHER</div>
          <h1 class="page-title">Videos</h1>
          <p class="page-subtitle">Pick a video to watch in the player. It starts after a short buffer and can pause briefly if the connection needs to catch up.</p>
        </div>
        <div class="video-layout">
          <section class="video-main">
            <div id="video-stage" class="video-stage">
              <video id="video-player" playsinline></video>
              <div id="video-placeholder" class="video-placeholder">
                <div class="play-mark">▶</div><strong>Choose a video to start watching</strong>
                <span>Your video plays right here in Audionator.</span>
              </div>
              <div id="video-transport" class="video-transport">
                <button id="video-toggle" type="button" disabled aria-label="Play or pause">▶</button>
                <div id="video-timeline" class="video-timeline">
                  <div class="video-track"><div id="video-buffered" class="video-buffered"></div><div id="video-played" class="video-played"></div></div>
                  <div id="video-thumb" class="video-thumb"></div>
                  <div id="video-hover-time" class="video-hover-time"></div>
                </div>
                <span id="video-time" class="video-time">0:00 / 0:00</span>
                <button id="video-fullscreen" type="button" disabled aria-label="Fullscreen">⛶</button>
              </div>
            </div>
            <div class="section-heading">
              <div><h2 id="video-title" class="video-title">Pick something from Up next</h2><div id="video-status" class="video-status">Videos stream over reliable UDP from your server.</div><div class="video-controls"><label>Quality<select id="video-quality" aria-label="Video quality" disabled><option value="auto">Auto</option></select></label><span id="video-traffic">Traffic: —</span></div></div>
              <button id="close-video" class="button secondary" style="display:none">Close player</button>
            </div>
          </section>
          <aside class="card up-next">
            <div class="card-head"><h3>Up next</h3><span id="video-counts" class="counts">—</span></div>
            <div class="library-tools"><input id="video-search" class="search" type="search" placeholder="Search videos" aria-label="Search videos" disabled></div>
            <div id="video-list" class="media-list" aria-live="polite">
              <div class="empty-state"><strong>No videos loaded</strong>Connect to browse MP4s on the server.</div>
            </div>
          </aside>
        </div>
      </section>

      <section id="page-connect" class="page active">
        <div class="page-head">
          <div class="eyebrow">YOUR LISTENING ROOM</div>
          <h1 class="page-title">Connect to a server</h1>
          <p class="page-subtitle">Connect to the computer that has the shared music and videos.</p>
        </div>
        <section class="card connect-card">
          <div class="connect-fields">
            <div class="field host"><label for="server-host">SERVER IP ADDRESS</label><input id="server-host" type="text" placeholder="Example: 192.168.1.25" autocomplete="off"></div>
            <div class="field port"><label for="server-port">PORT</label><input id="server-port" type="number" value="5005" min="1" max="65535"></div>
            <button id="connect-button" class="button primary">Connect</button>
          </div>
          <p class="help-text">On the server computer, run <b>ipconfig</b> to find its IPv4 address. Use 127.0.0.1 only when the app and server are on the same computer. Both devices need to be on the same local network.</p>
          <div class="connection-status"><span id="connection-status-dot" class="connection-dot" aria-hidden="true"></span><span id="connection-status-text">Not connected</span></div>
          <div id="status-line" class="status-line">Connect to browse the shared library.</div>
        </section>
        <section class="card connect-card">
          <div class="eyebrow">LISTENING SESSION</div>
          <p id="session-summary" class="help-text">Not in a session. Create one, or join with a code from the host.</p>
          <div class="connect-fields">
            <div class="field host"><label for="session-code">SESSION CODE</label><input id="session-code" type="text" maxlength="8" placeholder="Example: K7QM" autocomplete="off"></div>
            <button id="join-session-button" class="button secondary" disabled>Join</button>
            <button id="create-session-button" class="button primary" disabled>New session</button>
            <button id="leave-session-button" class="button secondary" style="display: none">Leave</button>
          </div>
        </section>
        <section class="card connect-card">
          <div class="eyebrow">CONNECTION HEALTH</div>
          <div class="health-grid">
            <div class="health-metric"><div class="health-label">Average latency</div><div id="health-average" class="health-value">—</div></div>
            <div class="health-metric"><div class="health-label">Maximum latency</div><div id="health-maximum" class="health-value">—</div></div>
            <div class="health-metric"><div class="health-label">Packet loss</div><div id="health-loss" class="health-value">—</div></div>
            <div class="health-metric"><div class="health-label">Latency samples</div><div id="health-samples" class="health-value">—</div></div>
          </div>
        </section>
      </section>

      <section id="page-settings" class="page">
        <div class="page-head"><div class="eyebrow">PREFERENCES</div><h1 class="page-title">Settings</h1><p class="page-subtitle">Connection and storage details for this device.</p></div>
        <div class="settings-grid">
          <section class="card settings-card"><h2>Server connection</h2><p id="settings-server">Not connected. Connect to the computer hosting your library.</p><button class="button secondary" data-open-connect>Connection settings</button></section>
          <section class="card settings-card"><h2>Received music</h2><p id="settings-storage">MP3 tracks you receive are stored on this device.</p><button id="settings-open-folder" class="button secondary">Open received folder</button></section>
          <section class="card settings-card"><h2>Video playback</h2><p>Videos play inside this window using your system's WebView2 media support. The local player bridge is only available on this device.</p></section>
          <section class="card settings-card"><h2>Network</h2><p>Audionator uses UDP port 5005 on your local network. For your safety, use it only on a trusted network.</p></section>
        </div>
      </section>

      <section id="page-account" class="page">
        <div class="page-head"><div class="eyebrow">PROFILE</div><h1 class="page-title">Account</h1><p class="page-subtitle">Your current Audionator profile.</p></div>
        <section class="card account-card">
          <div class="avatar">G</div>
          <div><h2>Guest listener</h2><p>This local-network version does not use online accounts or sign-in. Your connection is made directly to the server you choose.</p></div>
        </section>
      </section>
    </main>
  </div>

  <footer class="audio-dock">
    <div class="audio-inner">
      <div class="audio-track">
        <div class="cover-art">♫</div>
        <div class="audio-info"><strong id="audio-title">Nothing playing</strong><span id="audio-subtitle">Choose a track from Music</span></div>
        <div id="visualizer" class="visualizer" aria-hidden="true">
          <span style="--level:40%;--delay:-.2s"></span><span style="--level:70%;--delay:-.4s"></span><span style="--level:52%;--delay:-.1s"></span><span style="--level:88%;--delay:-.5s"></span><span style="--level:63%;--delay:-.3s"></span><span style="--level:36%;--delay:-.15s"></span><span style="--level:78%;--delay:-.45s"></span><span style="--level:48%;--delay:-.25s"></span><span style="--level:95%;--delay:-.35s"></span><span style="--level:56%;--delay:-.12s"></span><span style="--level:72%;--delay:-.38s"></span><span style="--level:42%;--delay:-.22s"></span>
        </div>
      </div>
      <div class="audio-controls">
        <button id="audio-toggle" class="play-button" title="Play or pause" disabled>▶</button>
        <div class="timeline-row">
          <span id="audio-elapsed" class="audio-time">0:00</span>
          <input id="audio-timeline" class="timeline" type="range" min="0" max="0" value="0" step="0.1" disabled aria-label="Audio position">
          <span id="audio-duration" class="audio-time">0:00</span>
        </div>
      </div>
      <div class="dock-action"><button id="open-folder" class="button secondary">Open received folder</button></div>
    </div>
  </footer>

  <script>
    const apiReady = new Promise(resolve => {
      if (window.pywebview && window.pywebview.api) resolve();
      else window.addEventListener("pywebviewready", resolve, { once: true });
    });
    const state = {
      files: { audio: [], video: [] }, view: "connect", connected: false,
      lastMessage: "", listKeys: { audio: "", video: "" }
    };
    const $ = id => document.getElementById(id);
    const formatTime = seconds => {
      const total = Math.max(0, Math.floor(Number(seconds) || 0));
      return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
    };
    const formatSize = bytes => {
      const size = Number(bytes) || 0;
      return size < 1048576 ? `${Math.round(size / 1024)} KB` : `${(size / 1048576).toFixed(1)} MB`;
    };
    function showStatus(message, isError = false) {
      $("status-line").textContent = message || "";
      $("status-line").classList.toggle("error", isError);
      $("connection-status-text").textContent = message || (state.connected ? "Connected" : "Not connected");
    }
    function navigate(view) {
      state.view = view;
      document.querySelectorAll(".page").forEach(page => page.classList.toggle("active", page.id === `page-${view}`));
      document.querySelectorAll(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
    }
    function renderList(type) {
      const list = $(type === "audio" ? "audio-list" : "video-list");
      const search = $(type === "audio" ? "audio-search" : "video-search");
      const files = state.files[type] || [];
      const query = search.value.trim().toLowerCase();
      const key = JSON.stringify([state.connected, query, files]);
      if (key === state.listKeys[type]) return;
      state.listKeys[type] = key;
      list.replaceChildren();
      if (!state.connected) {
        list.innerHTML = `<div class="empty-state"><strong>${type === "audio" ? "Your music" : "Your videos"} will show up here</strong>Connect to the server to browse its library.</div>`;
        return;
      }
      const filtered = files.filter(item => item.name.toLowerCase().includes(query));
      if (!filtered.length) {
        const title = files.length ? "No matches" : `No ${type === "audio" ? "MP3 audio" : "MP4 videos"} found`;
        const hint = files.length ? "Try another search." : `Add files to the server's ${type === "audio" ? "music" : "videos"} folder.`;
        list.innerHTML = `<div class="empty-state"><strong>${title}</strong>${hint}</div>`;
        return;
      }
      for (const item of filtered) {
        const row = document.createElement("div");
        row.className = "media-row";
        const icon = document.createElement("div");
        icon.className = "media-icon";
        icon.textContent = type === "audio" ? "♫" : "▶";
        const meta = document.createElement("div");
        meta.className = "media-meta";
        const title = document.createElement("div");
        title.className = "media-title";
        title.textContent = item.name;
        const detail = document.createElement("div");
        detail.className = "media-detail";
        detail.textContent = formatSize(item.size);
        meta.append(title, detail);
        const play = document.createElement("button");
        play.className = "button secondary";
        play.textContent = type === "audio" ? "Play" : "Watch";
        play.addEventListener("click", () => openMedia(item, type));
        row.append(icon, meta, play);
        list.append(row);
      }
    }
    function renderLists() {
      renderList("audio");
      renderList("video");
    }
    async function connect() {
      await apiReady;
      const host = $("server-host").value.trim();
      const port = Number($("server-port").value);
      if (!host) { showStatus("Enter the server computer's IP address.", true); $("server-host").focus(); return; }
      if (!Number.isInteger(port) || port < 1 || port > 65535) { showStatus("Enter a port between 1 and 65535.", true); $("server-port").focus(); return; }
      $("connect-button").disabled = true;
      $("connect-button").textContent = "Connecting…";
      showStatus("Contacting the server…");
      try {
        const result = await window.pywebview.api.connect(host, port);
        if (!result.ok) {
          $("connect-button").disabled = false;
          $("connect-button").textContent = "Try again";
          showStatus(result.message, true);
          return;
        }
      } catch (error) {
        $("connect-button").disabled = false;
        $("connect-button").textContent = "Try again";
        showStatus(`Could not contact the server: ${error.message || error}`, true);
      }
    }
    async function openMedia(item, type) {
      await apiReady;
      if (type === "video") {
        navigate("videos");
        $("video-title").textContent = item.name;
        $("video-status").textContent = "Opening the video…";
        $("video-placeholder").style.display = "none";
        $("close-video").style.display = "";
        try {
          const result = await window.pywebview.api.play_video(item.name);
          if (!result.ok) {
            $("video-status").textContent = result.message;
            $("video-placeholder").style.display = "grid";
            $("close-video").style.display = "none";
            showStatus(result.message, true);
            return;
          }
          await startVideoPlayer();
        } catch (error) {
          $("video-status").textContent = `Could not open video: ${error.message || error}`;
          $("video-placeholder").style.display = "grid";
          $("close-video").style.display = "none";
          showStatus($("video-status").textContent, true);
        }
      } else {
        navigate("music");
        try {
          const result = await window.pywebview.api.receive_audio(item.name);
          if (result.ok) showStatus(`Receiving ${item.name}…`);
          else showStatus(result.message, true);
        } catch (error) {
          showStatus(`Could not request audio: ${error.message || error}`, true);
        }
      }
    }
    async function refreshState() {
      await apiReady;
      try {
        const current = await window.pywebview.api.get_state();
        const wasConnected = state.connected;
        state.connected = current.connected;
        state.files = current.files;
        const dot = $("connection-dot");
        const statusDot = $("connection-status-dot");
        for (const indicator of [dot, statusDot]) {
          indicator.classList.toggle("connected", current.connected);
          indicator.classList.toggle("connecting", current.connecting);
          indicator.setAttribute("aria-label", current.connected ? "Connected" : current.connecting ? "Connecting" : "Disconnected");
        }
        const ping = current.latency.average_ms === null ? "" : ` · Ping ${current.latency.average_ms} ms avg, ${current.latency.max_ms} ms max`;
        const loss = current.loss.percent === null ? "" : ` · Loss ${current.loss.percent}%`;
        $("connection-status-text").textContent = current.connected
          ? `Connected to ${current.server.host}:${current.server.port}${ping}${loss}`
          : current.connecting ? "Contacting server…" : current.message;
        $("connect-button").disabled = current.connecting;
        $("connect-button").textContent = current.connecting ? "Connecting…" : current.connected ? "Refresh library" : "Connect";
        const session = current.session;
        $("session-summary").textContent = session.code
          ? `In session ${session.code} · ${session.members} listening`
          : "Not in a session. Create one, or join with a code from the host.";
        const measured = (value, unit) => current.connected && value !== null ? `${value}${unit}` : "—";
        $("health-average").textContent = measured(current.latency.average_ms, " ms");
        $("health-maximum").textContent = measured(current.latency.max_ms, " ms");
        $("health-loss").textContent = measured(current.loss.percent, "%");
        $("health-samples").textContent = current.connected ? String(current.latency.samples) : "—";
        $("leave-session-button").style.display = session.code ? "" : "none";
        $("create-session-button").disabled = !current.connected;
        $("join-session-button").disabled = !current.connected;
        $("audio-search").disabled = !current.connected;
        $("video-search").disabled = !current.connected;
        $("music-count").textContent = current.connected ? String(current.files.audio.length) : "—";
        $("video-count").textContent = current.connected ? String(current.files.video.length) : "—";
        $("audio-counts").textContent = current.connected ? `${current.files.audio.length} tracks` : "Connect to browse";
        $("video-counts").textContent = current.connected ? `${current.files.video.length} videos` : "—";
        $("music-server-note").textContent = current.connected
          ? `Connected to ${current.server.host}. ${current.files.audio.length} tracks are ready to explore.`
          : "Connect to a server to see its shared collection.";
        $("settings-server").textContent = current.connected
          ? `Connected to ${current.server.host}:${current.server.port}.`
          : "Not connected. Connect to the computer hosting your library.";
        $("settings-storage").textContent = current.download_directory;
        $("visualizer").classList.toggle("playing", current.audio.playing);
        $("mini-visualizer").classList.toggle("playing", current.audio.playing);
        $("close-video").style.display = current.video.active ? "" : "none";
        $("audio-title").textContent = current.audio.name || "Nothing playing";
        $("audio-subtitle").textContent = current.audio.subtitle;
        $("audio-toggle").disabled = !current.audio.ready;
        $("audio-toggle").textContent = current.audio.playing ? "Ⅱ" : "▶";
        $("audio-timeline").disabled = !current.audio.seekable;
        $("audio-timeline").max = String(current.audio.duration || 0);
        $("audio-timeline").value = String(current.audio.position || 0);
        $("audio-elapsed").textContent = formatTime(current.audio.position);
        $("audio-duration").textContent = formatTime(current.audio.duration);
        if (current.video.status) $("video-status").textContent = current.video.status;
        updateVideoSession(current.video);
        if (current.message !== state.lastMessage) {
          showStatus(current.message, current.error);
          state.lastMessage = current.message;
        }
        if (!wasConnected && current.connected && state.view === "connect") navigate("music");
        renderLists();
      } catch (error) {
        showStatus(`Could not refresh the app: ${error.message || error}`, true);
      }
    }
    document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => navigate(button.dataset.view)));
    document.querySelectorAll("[data-open-connect]").forEach(button => button.addEventListener("click", () => navigate("connect")));
    $("connect-button").addEventListener("click", connect);
    $("server-host").addEventListener("keydown", event => { if (event.key === "Enter") connect(); });
    async function sessionAction(request) {
      await apiReady;
      try {
        const result = await request();
        if (!result.ok) showStatus(result.message, true);
      } catch (error) {
        showStatus(`Session request failed: ${error.message || error}`, true);
      }
    }
    $("create-session-button").addEventListener("click", () => sessionAction(() => window.pywebview.api.create_session()));
    $("join-session-button").addEventListener("click", () => sessionAction(() => window.pywebview.api.join_session($("session-code").value)));
    $("session-code").addEventListener("keydown", event => { if (event.key === "Enter") sessionAction(() => window.pywebview.api.join_session($("session-code").value)); });
    $("leave-session-button").addEventListener("click", () => sessionAction(() => window.pywebview.api.leave_session()));
    $("server-port").addEventListener("keydown", event => { if (event.key === "Enter") connect(); });
    $("audio-search").addEventListener("input", () => renderList("audio"));
    $("video-search").addEventListener("input", () => renderList("video"));
    $("audio-toggle").addEventListener("click", async () => {
      await apiReady;
      try { await window.pywebview.api.toggle_audio(); }
      catch (error) { showStatus(`Playback control failed: ${error.message || error}`, true); }
    });
    $("audio-timeline").addEventListener("change", async event => {
      await apiReady;
      try { await window.pywebview.api.seek(Number(event.target.value)); }
      catch (error) { showStatus(`Could not seek: ${error.message || error}`, true); }
    });
    $("open-folder").addEventListener("click", async () => {
      await apiReady;
      try {
        const result = await window.pywebview.api.open_download_folder();
        if (!result.ok) showStatus(result.message, true);
      } catch (error) { showStatus(`Could not open folder: ${error.message || error}`, true); }
    });
    $("settings-open-folder").addEventListener("click", async () => {
      await apiReady;
      try {
        const result = await window.pywebview.api.open_download_folder();
        if (!result.ok) showStatus(result.message, true);
      } catch (error) { showStatus(`Could not open folder: ${error.message || error}`, true); }
    });
    $("close-video").addEventListener("click", async () => {
      stopVideoPlayer();
      const player = $("video-player");
      player.pause();
      player.removeAttribute("src");
      player.load();
      $("video-placeholder").style.display = "grid";
      $("video-title").textContent = "Pick something from Up next";
      $("video-status").textContent = "Videos stream over reliable UDP from your server.";
      setTransportEnabled(false);
      updateVideoTimeline();
      await apiReady;
      try { await window.pywebview.api.close_video(); }
      catch (error) { showStatus(`Could not close the video: ${error.message || error}`, true); }
    });
    const VIDEO_AHEAD_SECONDS = 20;
    const VIDEO_KEEP_BEHIND_SECONDS = 30;
    const videoRun = { id: 0, controller: null, mediaSource: null, sourceBuffer: null, objectUrl: null, info: null, index: 0, level: null, restarting: false, restartAt: -1 };

    function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

    function bufferOperation(sourceBuffer, operation) {
      return new Promise((resolve, reject) => {
        const cleanup = () => {
          sourceBuffer.removeEventListener("updateend", done);
          sourceBuffer.removeEventListener("error", failed);
        };
        const done = () => { cleanup(); resolve(); };
        const failed = () => { cleanup(); reject(new Error("The browser rejected part of the video.")); };
        sourceBuffer.addEventListener("updateend", done);
        sourceBuffer.addEventListener("error", failed);
        try { operation(); } catch (error) { cleanup(); reject(error); }
      });
    }

    function whenIdle(sourceBuffer) {
      if (!sourceBuffer.updating) return Promise.resolve();
      return new Promise(resolve => sourceBuffer.addEventListener("updateend", resolve, { once: true }));
    }

    function secondsBufferedAhead(sourceBuffer, time) {
      const ranges = sourceBuffer.buffered;
      if (!ranges.length) return 0;
      return Math.max(0, ranges.end(ranges.length - 1) - time);
    }

    function isTimeBuffered(time) {
      const player = $("video-player");
      for (let i = 0; i < player.buffered.length; i++) {
        if (time >= player.buffered.start(i) - 0.1 && time <= player.buffered.end(i) + 0.1) return true;
      }
      return false;
    }

    function showVideoError(message) {
      $("video-status").textContent = message;
      showStatus(message, true);
    }

    function setVideoQualityOptions(info) {
      const options = ['<option value="auto">Auto</option>'];
      for (const level of info.levels) options.push(`<option value="${level.id}">${level.id}</option>`);
      const select = $("video-quality");
      select.innerHTML = options.join("");
      select.value = info.mode || "auto";
      select.disabled = false;
    }

    function updateVideoSession(video) {
      const info = video.session;
      const select = $("video-quality");
      if (!info || info.status !== "ready") {
        $("video-traffic").textContent = "Traffic: —";
        return;
      }
      if (select.options.length && select.value !== info.mode) select.value = info.mode;
      const mode = info.mode === "auto" ? "Auto" : "Manual";
      const level = info.level ? ` · ${info.level}` : "";
      $("video-traffic").textContent = `Quality ${mode}${level} · Traffic ${(info.traffic_bps / 1000000).toFixed(2)} Mb/s`;
    }

    function stopVideoPlayer() {
      videoRun.id += 1;
      clearInterval(timelineTimer);
      timelineTimer = null;
      pendingSeek = null;
      if (videoRun.controller) videoRun.controller.abort();
      if (videoRun.objectUrl) URL.revokeObjectURL(videoRun.objectUrl);
      Object.assign(videoRun, { controller: null, mediaSource: null, sourceBuffer: null, objectUrl: null, info: null, index: 0, level: null, restarting: false, restartAt: -1 });
    }

    async function waitForVideoManifest(runId) {
      while (runId === videoRun.id) {
        const response = await fetch("/video/manifest", { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const info = await response.json();
        if (info.status === "ready") return info;
        if (info.status === "error") throw new Error(info.message);
        $("video-status").textContent = info.message || "Preparing the video on the server…";
        await sleep(500);
      }
      throw new Error("The video was closed.");
    }

    async function startVideoPlayer() {
      stopVideoPlayer();
      const runId = videoRun.id;
      const alive = () => runId === videoRun.id;
      const player = $("video-player");
      $("video-quality").disabled = true;
      try {
        const info = await waitForVideoManifest(runId);
        if (!alive()) return;
        const mime = `video/mp4; codecs="${info.codecs}"`;
        if (!window.MediaSource || !MediaSource.isTypeSupported(mime)) {
          throw new Error("This Windows WebView2 build cannot play the video's codecs.");
        }
        const mediaSource = new MediaSource();
        const opened = new Promise(resolve => mediaSource.addEventListener("sourceopen", resolve, { once: true }));
        const objectUrl = URL.createObjectURL(mediaSource);
        Object.assign(videoRun, { mediaSource, objectUrl, info });
        player.src = objectUrl;
        await opened;
        if (!alive()) return;
        mediaSource.duration = info.duration;
        setTransportEnabled(true);
        const sourceBuffer = mediaSource.addSourceBuffer(mime);
        sourceBuffer.mode = "sequence";
        Object.assign(videoRun, { sourceBuffer, controller: new AbortController(), index: 0, level: null });
        setVideoQualityOptions(info);
        $("video-status").textContent = "Buffering…";
        pumpVideoSegments(runId);
        clearInterval(timelineTimer);
        timelineTimer = setInterval(updateVideoTimeline, 250);
        player.play().catch(() => {
          $("video-status").textContent = "Ready. Use the player controls to start playback.";
        });
      } catch (error) {
        if (alive()) showVideoError(`Could not play this video: ${error.message || error}`);
      }
    }

    async function pumpVideoSegments(runId) {
      const alive = () => runId === videoRun.id;
      const player = $("video-player");
      const { info, sourceBuffer, controller, mediaSource } = videoRun;
      try {
        while (alive() && videoRun.index < info.segment_count) {
          if (secondsBufferedAhead(sourceBuffer, player.currentTime) >= VIDEO_AHEAD_SECONDS) {
            await sleep(400);
            continue;
          }
          const keepFrom = player.currentTime - VIDEO_KEEP_BEHIND_SECONDS;
          if (keepFrom > 0 && sourceBuffer.buffered.length && sourceBuffer.buffered.start(0) < keepFrom) {
            await bufferOperation(sourceBuffer, () => sourceBuffer.remove(0, keepFrom));
            if (!alive()) return;
          }
          const index = videoRun.index;
          const response = await fetch(`/video/segment/${index}`, { cache: "no-store", signal: controller.signal });
          if (!response.ok) throw new Error(await response.text());
          const level = response.headers.get("X-Video-Level");
          const bytes = await response.arrayBuffer();
          if (!alive()) return;
          if (level !== videoRun.level) {
            const initResponse = await fetch(`/video/init/${level}`, { cache: "no-store", signal: controller.signal });
            if (!initResponse.ok) throw new Error(await initResponse.text());
            const init = await initResponse.arrayBuffer();
            if (!alive()) return;
            await bufferOperation(sourceBuffer, () => sourceBuffer.appendBuffer(init));
            if (!alive()) return;
            videoRun.level = level;
          }
          sourceBuffer.timestampOffset = index * info.segment_seconds;
          await bufferOperation(sourceBuffer, () => sourceBuffer.appendBuffer(bytes));
          if (!alive()) return;
          videoRun.index = index + 1;
        }
        if (alive() && mediaSource.readyState === "open") {
          await whenIdle(sourceBuffer);
          mediaSource.endOfStream();
        }
      } catch (error) {
        if (alive() && error.name !== "AbortError") showVideoError(`Playback stopped: ${error.message || error}`);
      }
    }

    async function restartVideoAt(seconds) {
      const { info, sourceBuffer } = videoRun;
      if (!info || !sourceBuffer) return;
      if (videoRun.controller) videoRun.controller.abort();
      videoRun.id += 1;
      const runId = videoRun.id;
      videoRun.controller = new AbortController();
      videoRun.restarting = true;
      videoRun.restartAt = seconds;
      const player = $("video-player");
      try {
        await whenIdle(sourceBuffer);
        if (sourceBuffer.buffered.length) await bufferOperation(sourceBuffer, () => sourceBuffer.remove(0, Infinity));
        videoRun.index = Math.min(info.segment_count - 1, Math.max(0, Math.floor(seconds / info.segment_seconds)));
        videoRun.level = null;
        player.currentTime = seconds;
      } catch (error) {
        if (runId === videoRun.id) showVideoError(`Could not seek: ${error.message || error}`);
        return;
      } finally {
        videoRun.restarting = false;
      }
      pumpVideoSegments(runId);
    }

    function onVideoSeeking() {
      const player = $("video-player");
      if (!videoRun.sourceBuffer || videoRun.restarting) return;
      if (Math.abs(player.currentTime - videoRun.restartAt) < 0.25) return;
      if (isTimeBuffered(player.currentTime)) return;
      restartVideoAt(player.currentTime);
    }

    let scrubTime = null;
    let pendingSeek = null;
    let controlsTimer = null;
    let timelineTimer = null;

    function setTransportEnabled(enabled) {
      $("video-toggle").disabled = !enabled;
      $("video-fullscreen").disabled = !enabled;
    }

    function showHoverTime(event, time) {
      const rect = $("video-timeline").getBoundingClientRect();
      $("video-hover-time").style.left = `${event.clientX - rect.left}px`;
      $("video-hover-time").textContent = formatTime(time);
    }

    function scheduleControlsHide() {
      clearTimeout(controlsTimer);
      controlsTimer = setTimeout(() => {
        if (!$("video-player").paused && scrubTime === null && !$("video-stage").matches(":hover")) {
          $("video-stage").classList.add("controls-hidden");
        }
      }, 2500);
    }

    function wakeControls() {
      $("video-stage").classList.remove("controls-hidden");
      scheduleControlsHide();
    }

    function updateVideoTimeline() {
      const player = $("video-player");
      const duration = videoRun.info && videoRun.info.duration > 0 ? videoRun.info.duration : 0;
      const hasSource = Boolean(player.currentSrc) && duration > 0;
      const shown = hasSource ? Math.min(duration, scrubTime ?? pendingSeek ?? player.currentTime) : 0;
      const percent = value => (duration > 0 ? `${Math.min(100, Math.max(0, 100 * value / duration)).toFixed(2)}%` : "0%");
      let buffered = "";
      if (hasSource) {
        for (let i = 0; i < player.buffered.length; i++) {
          const start = player.buffered.start(i);
          buffered += `<i style="left:${percent(start)};width:${percent(player.buffered.end(i) - start)}"></i>`;
        }
      }
      $("video-buffered").innerHTML = buffered;
      $("video-played").style.width = percent(shown);
      $("video-thumb").style.left = percent(shown);
      $("video-time").textContent = hasSource ? `${formatTime(shown)} / ${formatTime(duration)}` : "0:00 / 0:00";
      $("video-toggle").textContent = player.paused ? "▶" : "❚❚";
    }

    function togglePlayback() {
      const player = $("video-player");
      if (!player.currentSrc) return;
      if (player.paused) player.play().catch(() => {});
      else player.pause();
    }

    function timeAtPointer(event) {
      const duration = videoRun.info ? videoRun.info.duration : 0;
      if (!$("video-player").currentSrc || !(duration > 0)) return null;
      const rect = $("video-timeline").getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      return fraction * duration;
    }

    $("video-toggle").addEventListener("click", togglePlayback);
    $("video-player").addEventListener("click", togglePlayback);
    $("video-fullscreen").addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else document.querySelector(".video-stage").requestFullscreen().catch(() => {});
    });
    $("video-timeline").addEventListener("pointerdown", event => {
      scrubTime = timeAtPointer(event);
      if (scrubTime === null) return;
      $("video-timeline").setPointerCapture(event.pointerId);
      $("video-timeline").classList.add("scrubbing");
      updateVideoTimeline();
    });
    $("video-timeline").addEventListener("pointermove", event => {
      const time = timeAtPointer(event);
      if (time === null) return;
      showHoverTime(event, time);
      if (scrubTime === null) return;
      scrubTime = time;
      updateVideoTimeline();
    });
    $("video-timeline").addEventListener("pointerup", () => {
      if (scrubTime === null) return;
      const target = scrubTime;
      scrubTime = null;
      $("video-timeline").classList.remove("scrubbing");
      pendingSeek = target;
      $("video-player").currentTime = target;
      updateVideoTimeline();
    });
    $("video-timeline").addEventListener("pointercancel", () => {
      scrubTime = null;
      $("video-timeline").classList.remove("scrubbing");
      updateVideoTimeline();
    });
    $("video-stage").addEventListener("pointermove", wakeControls);
    $("video-stage").addEventListener("pointerdown", wakeControls);
    $("video-stage").addEventListener("pointerleave", scheduleControlsHide);
    $("video-player").addEventListener("play", wakeControls);
    $("video-player").addEventListener("pause", wakeControls);
    $("video-player").addEventListener("seeked", () => { pendingSeek = null; });
    $("video-player").addEventListener("emptied", () => { pendingSeek = null; });
    for (const eventName of ["loadstart", "progress", "timeupdate", "seeking", "seeked", "emptied", "durationchange", "loadedmetadata", "play", "pause", "ended"]) {
      $("video-player").addEventListener(eventName, () => updateVideoTimeline());
    }
    $("video-player").addEventListener("seeking", onVideoSeeking);
    $("video-quality").addEventListener("change", async event => {
      await apiReady;
      try {
        const result = await window.pywebview.api.set_video_quality(event.target.value);
        if (!result.ok) showStatus(result.message, true);
      } catch (error) {
        showStatus(`Could not change quality: ${error.message || error}`, true);
      }
    });
    window.addEventListener("load", async () => {
      const bars = [18, 30, 42, 26, 50, 34, 22, 46, 28, 38, 17, 32, 24];
      bars.forEach((height, index) => {
        const bar = document.createElement("i");
        bar.style.setProperty("--level", `${height}px`);
        bar.style.setProperty("--delay", `${-index * .09}s`);
        $("mini-visualizer").append(bar);
      });
      await apiReady;
      try {
        const saved = await window.pywebview.api.get_saved_connection();
        $("server-host").value = saved.host;
        $("server-port").value = String(saved.port);
        if (saved.host) connect();
      } catch (error) {
        showStatus(`Could not load saved connection: ${error.message || error}`, true);
      }
    });
    setInterval(refreshState, 500);
  </script>
</body>
</html>
"""
