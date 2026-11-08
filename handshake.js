'use strict';
var fs = require('fs'), WS = require('./websocket');
var config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
config.ca = fs.readFileSync(config.caPath);
if (process.argv[3] === 'wrong-pin') config.pin = new Array(65).join('0');
if (process.argv[3] === 'wrong-token') config.token = 'deliberately-invalid-test-token';
var ws = new WS.Client(config), done = false;
var timer = setTimeout(function () { finish(2, 'timeout'); }, 12000);
function finish(code, text) { if (done) return; done = true; clearTimeout(timer); console.log(text); ws.close(); process.exit(code); }
ws.on('open', function (version) { console.log('tls=' + version); });
ws.on('message', function (m) { if (m.state === 'ready') finish(0, 'authenticated_backend_ready'); });
ws.on('failure', function (reason) { finish(1, 'rejected=' + reason); });
ws.on('closed', function () { if (!done) finish(2, 'closed_before_ready'); });
