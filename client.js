'use strict';
var fs = require('fs'), http = require('http'), crypto = require('crypto');
var spawn = require('child_process').spawn;
var WS = require('./websocket'), PCM16 = require('./audio');
var config = JSON.parse(fs.readFileSync(process.argv[2] || '/opt/usr/gear-ptt/config.json', 'utf8'));
config.ca = fs.readFileSync(config.caPath);
if (!/^[a-f0-9]{64}$/.test(config.pin) || !/^[A-Za-z0-9_-]{43}$/.test(config.controlToken) ||
    typeof config.token !== 'string' || /[\r\n]/.test(config.token) || config.token.length > 512) throw new Error('invalid private configuration');
var state = 'idle', reason = '', gain = 18, backend = null, recorder = null, watchdog = null;
var peers = [], seq = 0, bytes = 0, stopping = false, decimator = null, connectedAt = 0, captureLimit = null, stopLimit = null;
var reply = '', resultRows = [], resultKind = '', services = {}, checking = false;
var maxSeconds = Math.max(3, Math.min(30, config.maxSeconds || 20));
function status(privateData) {
  var value = {state: state, reason: reason, gain: gain, seconds: Math.round(bytes / 32000), maxSeconds: maxSeconds};
  if (privateData) { value.text = reply; value.results = resultRows; value.kind = resultKind; value.services = services; }
  return value;
}
function broadcast() { var data = WS.frame(1, JSON.stringify(status(true)), false); peers.forEach(function (p) { p.write(data); }); }
function setState(next, why) { state = next; reason = why || ''; broadcast(); console.log('state=' + next + (why ? ' reason=' + why : '')); }
function cleanup(next, why) {
  if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  clearTimeout(captureLimit); clearTimeout(stopLimit);
  var rec = recorder; recorder = null; if (rec) { rec.kill('SIGTERM'); setTimeout(function () { try { rec.kill('SIGKILL'); } catch (_) {} }, 1000); }
  var ws = backend; backend = null; if (ws) ws.close();
  setState(next || 'idle', why);
}
function fail(why) { cleanup('error', why); }
function finishCapture() {
  if (!recorder || state !== 'recording') return;
  if (stopping) return;
  stopping = true; var rec = recorder; rec.kill('SIGINT');
  stopLimit = setTimeout(function () { if (recorder === rec) rec.kill('SIGKILL'); }, 500);
}
function startCapture() {
  if (!backend) return;
  if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  stopping = false; bytes = 0; seq = 0;
  decimator = new PCM16(gain, function (pcm) {
    if (!backend || state !== 'recording' || bytes >= maxSeconds * 32000) return;
    var message = new Buffer(964); message.writeUInt32LE(seq++, 0); pcm.copy(message, 4);
    backend.send(message); bytes += pcm.length; if (seq % 16 === 0) broadcast();
  });
  var rec = spawn('/opt/usr/gear-ptt/arecord', ['-D', 'plughw:0,0', '-r', '16000', '-c', '1', '-f', 'S16_LE', '-t', 'raw', '-d', String(maxSeconds), '--fatal-errors', '-q'], {stdio: ['ignore', 'pipe', 'pipe']});
  recorder = rec; setState('recording');
  var errors = 0, rawBytes = 0, captureAt = Date.now();
  console.log('recorder_pid=' + rec.pid);
  rec.stdout.on('data', function (data) {
    // Yield between 30 ms input slices so pipe backlog cannot starve controls,
    // incoming TLS errors, or the bounded capture/stop timers on Node4.
    rec.stdout.pause(); var offset = 0;
    function pump() {
      if (recorder !== rec) return;
      var end = Math.min(offset + 960, data.length), feedAt = Date.now();
      rawBytes += end - offset; decimator.feed(data.slice(offset, end)); offset = end;
      var feedMs = Date.now() - feedAt;
      if (feedMs > 100) console.log('slow_feed_ms=' + feedMs);
      if (offset < data.length) setImmediate(pump); else rec.stdout.resume();
    }
    pump();
  });
  rec.stderr.on('data', function (data) { errors += data.length; }); // No recording/log content persisted.
  rec.on('error', function () { if (recorder === rec) fail('microphone_start'); });
  rec.on('close', function (code, signal) {
    if (recorder !== rec) return;
    console.log('recorder_closed code=' + code + ' signal=' + signal + ' raw_bytes=' + rawBytes + ' wall_ms=' + (Date.now() - captureAt));
    recorder = null; clearTimeout(watchdog); watchdog = null;
    clearTimeout(captureLimit); clearTimeout(stopLimit);
    if (!stopping && (code !== 0 || signal)) return fail('microphone_failed');
    decimator.finish();
    if (!bytes || !backend) return fail('empty_capture');
    console.log('capture bytes=' + bytes + ' frames=' + seq + ' clipped=' + decimator.clipped + ' stderr_bytes=' + errors);
    backend.send({type: 'end'}); setState('processing');
    watchdog = setTimeout(function () { fail('response_timeout'); }, 160000);
  });
  // Send END before the backend's wall-time deadline even if ALSA stalls.
  captureLimit = setTimeout(finishCapture, (maxSeconds - 1) * 1000);
  watchdog = setTimeout(function () { fail('capture_timeout'); }, (maxSeconds + 1) * 1000);
}
function start(check) {
  if (state !== 'idle' && state !== 'complete' && state !== 'error') return;
  checking = check === true;
  if (!checking) { reply = ''; resultRows = []; resultKind = ''; }
  bytes = 0; seq = 0; cleanup('connecting');
  var ws = new WS.Client(config); backend = ws; connectedAt = Date.now();
  ws.on('open', function (version) { console.log('backend_tls=' + version); });
  ws.on('failure', function (why) { if (backend === ws) fail(why); });
  ws.on('closed', function () { if (backend === ws) fail('backend_closed'); });
  ws.on('message', function (message) {
    if (backend !== ws || typeof message.state !== 'string') return;
    if (message.state === 'ready' && state === 'connecting') {
      if (message.version !== 2) return fail('backend_version');
      if (checking) ws.send({type: 'status'});
      else ws.send({type: 'start', id: 'gear-' + crypto.randomBytes(12).toString('hex'), mode: 'ptt', protocolVersion: 2});
    }
    else if (message.state === 'status' && checking) {
      services = message.services || {}; cleanup('idle');
    }
    else if (message.state === 'result') {
      reply = typeof message.text === 'string' ? message.text.slice(0, 1200) : '';
      resultKind = message.kind === 'home' ? 'home' : 'answer';
      resultRows = Array.isArray(message.results) ? message.results.slice(0, 12) : [];
      if (message.voice === 'unavailable') services.voice = 'unavailable';
      broadcast();
    }
    else if (message.state === 'recording' && state === 'connecting') startCapture();
    else if (message.state === 'transcribing') setState('processing');
    else if (message.state === 'responding') setState('responding');
    else if (message.state === 'complete') { console.log('turn_complete_ms=' + (Date.now() - connectedAt)); cleanup('complete'); }
    else if (message.state === 'no_speech') cleanup('idle', 'no_speech');
    else if (message.state === 'error') fail(/^[a-z_]{1,64}$/.test(message.reason || '') ? message.reason : 'backend_error');
  });
  watchdog = setTimeout(function () { fail('connect_timeout'); }, 12000);
}
function authorized(value) {
  var expected = 'gearptt.' + config.controlToken;
  if (typeof value !== 'string' || value.length !== expected.length) return false;
  var difference = 0; for (var i = 0; i < value.length; i++) difference |= value.charCodeAt(i) ^ expected.charCodeAt(i);
  return difference === 0;
}
var server = http.createServer(function (req, res) {
  if (req.method !== 'GET' || req.url !== '/status') { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store'); res.end(JSON.stringify(status()));
});
server.maxHeadersCount = 20; server.timeout = 8000;
server.on('upgrade', function (req, socket, head) {
  var origin = req.headers.origin;
  if (req.url !== '/control' || req.headers.host !== '127.0.0.1:8765' ||
      (origin && origin !== 'file://' && origin !== 'null' && origin.indexOf('file://') !== 0) ||
      !authorized(req.headers['sec-websocket-protocol']) || peers.length >= 2 ||
      !/^[A-Za-z0-9+/]{22}==$/.test(req.headers['sec-websocket-key'] || '')) {
    socket.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n'); return;
  }
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + WS.accept(req.headers['sec-websocket-key']) +
    '\r\nSec-WebSocket-Protocol: ' + req.headers['sec-websocket-protocol'] + '\r\n\r\n');
  peers.push(socket); socket.setTimeout(0); socket.setNoDelay(true);
  var parser = new WS.Parser(true, function (op, payload) {
    if (op === 8) return socket.end(WS.frame(8, '', false));
    if (op === 9) return socket.write(WS.frame(10, payload, false));
    if (op !== 1) return;
    var message = JSON.parse(payload.toString('utf8'));
    if (['start','stop','cancel'].indexOf(message.action) >= 0) console.log('control=' + message.action + ' state=' + state);
    if (message.action === 'start') start(false);
    else if (message.action === 'check') start(true);
    else if (message.action === 'stop') finishCapture();
    else if (message.action === 'cancel') { if (backend) backend.send({type: 'cancel'}); cleanup('idle'); }
    else if (message.action === 'gain' && ['idle', 'complete', 'error'].indexOf(state) >= 0 &&
             typeof message.value === 'number' && isFinite(message.value)) { gain = Math.max(0, Math.min(30, Math.round(message.value))); broadcast(); }
  }, 1024);
  socket.on('data', function (data) { try { parser.feed(data); } catch (_) { socket.destroy(); } });
  // Upgraded HTTP sockets may remain half-open on this Node4 runtime.
  socket.on('end', function () { socket.destroy(); });
  socket.on('error', function () { socket.destroy(); });
  socket.on('close', function () { peers = peers.filter(function (p) { return p !== socket; }); if (!peers.length && (state === 'recording' || state === 'connecting')) cleanup('idle', 'ui_closed'); });
  broadcast(); if (head.length) { try { parser.feed(head); } catch (_) { socket.destroy(); } }
});
server.listen(8765, '127.0.0.1', function () { console.log('gearptt_ready loopback_only'); });
process.on('SIGTERM', function () { cleanup('idle'); server.close(); process.exit(0); });
