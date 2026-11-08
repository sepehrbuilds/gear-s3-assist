'use strict';
var assert = require('assert'), WS = require('./websocket'), PCM16 = require('./audio');
var got = [], parser = new WS.Parser(true, function (op, b) { got.push([op,b.toString('utf8')]); }, 2048);
var encoded = WS.frame(1, 'x'.repeat(257), true);
for (var i=0;i<encoded.length;i++) parser.feed(encoded.slice(i,i+1));
assert.strictEqual(got[0][0],1); assert.strictEqual(got[0][1],'x'.repeat(257));
assert.throws(function () { new WS.Parser(false,function(){}).feed(WS.frame(1,'x',true)); });
assert.throws(function () { new WS.Parser(false,function(){},10).feed(WS.frame(1,'12345678901',false)); });
assert.throws(function () { new WS.Parser(false,function(){}).feed(new Buffer([0x81,127,0,0,0,0,0,0,0,1])); });
assert.strictEqual(WS.accept('dGhlIHNhbXBsZSBub25jZQ=='),'s3pPLMBiTxaQ9kYGzzhZRbK+xOo=');
function tone(hz) { var b=new Buffer(32000);for(var n=0;n<16000;n++)b.writeInt16LE(Math.round(5000*Math.sin(2*Math.PI*hz*n/16000)),n*2);return b; }
function convert(b,chunk) { var parts=[],d=new PCM16(0,function(p){parts.push(p);});for(var n=0;n<b.length;n+=chunk)d.feed(b.slice(n,n+chunk));d.finish();return Buffer.concat(parts); }
var input=tone(1000),whole=convert(input,input.length),chunked=convert(input,137);
assert(whole.equals(chunked),'chunking and odd-byte carries must preserve exact output');
assert(whole.slice(0,input.length).equals(input),'zero gain must preserve native 16k PCM samples exactly');
function rms(b) {var sum=0,count=0;for(var i=400;i<b.length-1000;i+=2){var x=b.readInt16LE(i);sum+=x*x;count++;}return Math.sqrt(sum/count);}
assert(Math.abs(rms(whole)-5000/Math.sqrt(2))<5,'passband gain');
var loud = new Buffer(960); for (var k=0;k<960;k+=2) loud.writeInt16LE(k%4 ? -32768 : 32767,k);
var clipped, boosted=new PCM16(18,function(p){clipped=p;}); boosted.feed(loud);
assert.strictEqual(boosted.clipped,480); assert.strictEqual(clipped.readInt16LE(0),32767); assert.strictEqual(clipped.readInt16LE(2),-32768);
assert(whole.length>=32000&&whole.length<32960,'16kHz output byte accounting with final frame padding');
console.log('PASS: WebSocket framing, bounds, masks, accept; audio chunking, odd bytes, gain and clipping');
