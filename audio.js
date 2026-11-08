'use strict';
// ALSA plughw performs 48k -> 16k conversion natively. This path only applies
// microphone gain and frames 30 ms PCM blocks, keeping old ARM V8 responsive.
function PCM16(gainDb, send) {
  this.gain = Math.pow(10, gainDb / 20); this.send = send; this.odd = null;
  this.output = new Buffer(960); this.used = 0; this.frames = 0; this.clipped = 0;
}
PCM16.prototype.feed = function (data) {
  if (this.odd !== null) { data = Buffer.concat([new Buffer([this.odd]), data]); this.odd = null; }
  if (data.length % 2) { this.odd = data[data.length - 1]; data = data.slice(0, -1); }
  for (var i = 0; i < data.length; i += 2) {
    var value = Math.round(data.readInt16LE(i) * this.gain);
    if (value > 32767 || value < -32768) this.clipped++;
    this.output.writeInt16LE(Math.max(-32768, Math.min(32767, value)), this.used); this.used += 2;
    if (this.used === 960) { this.send(this.output); this.frames++; this.output = new Buffer(960); this.used = 0; }
  }
};
PCM16.prototype.finish = function () { if (this.used) { this.output.fill(0, this.used); this.send(this.output); this.frames++; this.used = 0; } };
module.exports = PCM16;
