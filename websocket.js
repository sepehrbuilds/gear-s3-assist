'use strict';
// Small bounded RFC6455 subset for this client: complete messages and control frames.
var crypto = require('crypto');
var EventEmitter = require('events').EventEmitter;
var tls = require('tls');
var util = require('util');
function frame(op, payload, masked) {
  if (!Buffer.isBuffer(payload)) payload = new Buffer(payload || '', 'utf8');
  if (payload.length > 65535) throw new Error('frame too large');
  var wide = payload.length >= 126, header = wide ? 4 : 2;
  var out = new Buffer(header + (masked ? 4 : 0) + payload.length);
  out[0] = 0x80 | op; out[1] = (masked ? 0x80 : 0) | (wide ? 126 : payload.length);
  if (wide) out.writeUInt16BE(payload.length, 2);
  if (masked) {
    var key = crypto.randomBytes(4); key.copy(out, header);
    for (var i = 0; i < payload.length; i++) out[header + 4 + i] = payload[i] ^ key[i % 4];
  } else payload.copy(out, header);
  return out;
}
function Parser(expectMask, callback, limit) {
  this.buffer = new Buffer(0); this.expectMask = expectMask;
  this.callback = callback; this.limit = limit || 8192;
}
Parser.prototype.feed = function (data) {
  this.buffer = Buffer.concat([this.buffer, data]);
  if (this.buffer.length > this.limit + 65550) throw new Error('receive buffer limit');
  while (this.buffer.length >= 2) {
    var b = this.buffer, opcode = b[0] & 15, masked = !!(b[1] & 128);
    if (!(b[0] & 128) || (b[0] & 112) || masked !== this.expectMask ||
        [1, 2, 8, 9, 10].indexOf(opcode) < 0) throw new Error('unsupported frame');
    var length = b[1] & 127, off = 2;
    if (length === 127) throw new Error('oversize frame encoding');
    if (length === 126) { if (b.length < 4) return; length = b.readUInt16BE(2); off = 4; }
    if (length > this.limit || (opcode >= 8 && length > 125)) throw new Error('frame limit');
    var keyOff = off; if (masked) off += 4;
    if (b.length < off + length) return;
    var payload = new Buffer(length);
    for (var i = 0; i < length; i++) payload[i] = masked ? b[off + i] ^ b[keyOff + i % 4] : b[off + i];
    this.buffer = b.slice(off + length);
    this.callback(opcode, payload);
  }
};
function accept(key) { return crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64'); }
function Client(config) {
  EventEmitter.call(this); var self = this;
  this.open = false; this.closed = false; this.pending = new Buffer(0);
  var key = crypto.randomBytes(16).toString('base64');
  this.socket = tls.connect({host: config.host, port: config.port, ca: config.ca,
    rejectUnauthorized: true, checkServerIdentity: function (_, cert) { return tls.checkServerIdentity(config.host, cert); }}, function () {
    if (!self.socket.authorized) return self.fail('tls_rejected');
    var cert = self.socket.getPeerCertificate();
    if (!cert.raw || crypto.createHash('sha256').update(cert.raw).digest('hex') !== config.pin) return self.fail('tls_pin');
    self.socket.write('GET /audio HTTP/1.1\r\nHost: ' + config.host + ':' + config.port +
      '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key +
      '\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer ' + config.token + '\r\n\r\n');
  });
  this.socket.setNoDelay(true);
  this.socket.setTimeout(12000, function () { self.fail('socket_timeout'); });
  var parser = new Parser(false, function (op, payload) {
    if (op === 9) self.socket.write(frame(10, payload, true));
    else if (op === 8) self.close();
    else if (op === 1) {
      try { self.emit('message', JSON.parse(payload.toString('utf8'))); }
      catch (_) { self.fail('invalid_server_message'); }
    } else if (op !== 10) self.fail('unexpected_binary');
  }, 8192);
  this.socket.on('data', function (data) {
    try {
      if (self.open) return parser.feed(data);
      self.pending = Buffer.concat([self.pending, data]);
      var end = self.pending.indexOf('\r\n\r\n');
      if (end < 0) { if (self.pending.length > 16384) self.fail('handshake_limit'); return; }
      var text = self.pending.slice(0, end).toString('ascii'), lines = text.split('\r\n'), headers = {};
      if (!/^HTTP\/1\.[01] 101\b/.test(lines[0])) {
        var code = /^HTTP\/1\.[01] ([0-9]{3})\b/.exec(lines[0]);
        return self.fail(code && code[1] === '409' ? 'backend_busy' : code && code[1] === '429' ? 'rate_limited' : 'upgrade_rejected');
      }
      lines.slice(1).forEach(function (line) { var i = line.indexOf(':'); if (i > 0) headers[line.slice(0, i).toLowerCase()] = line.slice(i + 1).trim(); });
      if (headers['sec-websocket-accept'] !== accept(key) || (headers.upgrade || '').toLowerCase() !== 'websocket') return self.fail('upgrade_invalid');
      self.socket.setTimeout(0); // Phase watchdogs own capture/response deadlines after upgrade.
      self.open = true; self.emit('open', self.socket.getProtocol ? self.socket.getProtocol() : 'verified TLS (Node 4)');
      var rest = self.pending.slice(end + 4); self.pending = new Buffer(0); if (rest.length) parser.feed(rest);
    } catch (_) { self.fail('protocol_error'); }
  });
  this.socket.on('error', function (error) { self.fail('connection_' + (error && /^[A-Z0-9_]{1,80}$/.test(error.code || '') ? error.code : 'error')); });
  this.socket.on('close', function () { if (!self.closed) { self.closed = true; self.emit('closed'); } });
}
util.inherits(Client, EventEmitter);
Client.prototype.send = function (value) { if (this.open && !this.closed) { var t=Date.now(); var packet=frame(Buffer.isBuffer(value) ? 2 : 1, Buffer.isBuffer(value) ? value : JSON.stringify(value), true); var framed=Date.now(); this.socket.write(packet); if (Date.now()-t>20) console.log('send_frame_ms='+(framed-t)+' write_ms='+(Date.now()-framed)); } };
Client.prototype.close = function () { if (!this.closed) { this.closed = true; if (this.open) this.socket.write(frame(8, '', true)); this.socket.end(); this.socket.destroy(); this.emit('closed'); } };
Client.prototype.fail = function (reason) { if (this.closed) return; this.emit('failure', reason); this.close(); };
module.exports = {frame: frame, Parser: Parser, accept: accept, Client: Client};
