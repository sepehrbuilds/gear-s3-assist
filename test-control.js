'use strict';
// Regression: TCP half-close must release an upgraded control peer on Node4.
var assert=require('assert'),net=require('net'),fs=require('fs'),crypto=require('crypto'),os=require('os'),path=require('path'),spawn=require('child_process').spawn;
var dir=fs.mkdtempSync(path.join(os.tmpdir(),'gear-control-'));
var token='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
fs.writeFileSync(path.join(dir,'ca'),'test-only');
fs.writeFileSync(path.join(dir,'config'),JSON.stringify({pin:new Array(65).join('a'),controlToken:token,token:'test-only',caPath:path.join(dir,'ca')}));
var child=spawn(process.execPath,[process.env.GEAR_CLIENT_PATH || path.join(__dirname,'client.js'),path.join(dir,'config')]);
var timer=setTimeout(function(){done(new Error('control regression timed out'));},10000),finished=false;
function done(err){if(finished)return;finished=true;clearTimeout(timer);child.kill();fs.unlinkSync(path.join(dir,'ca'));fs.unlinkSync(path.join(dir,'config'));fs.rmdirSync(dir);if(err){console.error(err.message);process.exitCode=1;}else console.log('PASS: repeated half-closed control connections release peer slots');}
function probe(n){if(n===5)return done();var socket=net.connect(8765,'127.0.0.1'),buf='',accepted=false;
 socket.on('connect',function(){socket.write('GET /control HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+crypto.randomBytes(16).toString('base64')+'\r\nSec-WebSocket-Protocol: gearptt.'+token+'\r\n\r\n');});
 socket.on('data',function(data){if(accepted)return;buf+=data.toString();if(buf.indexOf('\r\n\r\n')<0)return;try{assert(/^HTTP\/1.1 101/.test(buf),'control peer slot leaked at connection '+n);}catch(e){socket.destroy();return done(e);}accepted=true;socket.end();setTimeout(function(){socket.destroy();probe(n+1);},150);});socket.on('error',done);
}
child.stdout.once('data',function(){probe(0);});child.on('error',done);
