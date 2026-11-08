(function () {
  'use strict';
  var ws, reconnect, current='offline', gain=18, locked=false, last='', panel=false;
  var labels={idle:'PRESS TO TALK',connecting:'CONNECTING',recording:'LISTENING',processing:'WORKING',responding:'REPLYING',complete:'DONE',error:'TRY AGAIN',offline:'WATCH OFFLINE'};
  var errors={backend_busy:'The previous turn is finishing. Try again shortly.',rate_limited:'Too many requests. Please wait.',capture_timeout:'Recording timed out. Please try again.',response_timeout:'The answer took too long.',connect_timeout:'Mac backend is not reachable.',backend_version:'Backend version mismatch.',speaker_busy_or_unavailable:'HomePod is busy or unavailable.',microphone_failed:'Microphone stopped unexpectedly.',tls_pin:'Backend certificate changed.',upgrade_rejected:'Backend authentication failed.',connection_ECONNREFUSED:'Mac backend is not running.',turn_failed:'Request failed. Check connection status.'};
  function el(id){return document.getElementById(id);}
  function vibrate(ms){try{navigator.vibrate(ms);}catch(_){}}
  function power(on){try{if(on&&!locked){tizen.power.request('SCREEN','SCREEN_NORMAL');locked=true;}else if(!on&&locked){tizen.power.release('SCREEN');locked=false;}}catch(_){}}
  function send(message){if(ws&&ws.readyState===1)ws.send(JSON.stringify(message));}
  function draw(data){
    current=data.state;if(typeof data.gain==='number')gain=data.gain;
    var hasReply=!!data.text&&['recording','connecting','error'].indexOf(current)<0;
    el('face').className=current+(hasReply?' has-reply':'')+((data.results||[]).some(function(r){return r.status!=='confirmed';})?' partial':'');
    el('state').textContent=labels[current]||'GEAR ASSIST';
    if(hasReply&&data.kind==='home')el('state').textContent=data.text;
    el('gain').textContent='MIC +'+gain+' dB';
    el('answer').textContent=data.kind==='home'?'':(data.text||'');
    el('answer').dir='auto';el('actions').textContent='';
    (data.results||[]).forEach(function(row){
      var line=document.createElement('div'),name=document.createElement('span'),value=document.createElement('b');
      line.className='action-row'+(row.status==='confirmed'?'':' pending');name.textContent=row.label;
      value.textContent=row.status==='confirmed'?(row.value||(row.action==='on'?'ON':'OFF')):row.status==='unsupported'?(row.value||'Nicht unterstützt'):row.status==='jammed'?'BLOCKIERT':row.status==='unavailable'?'NICHT ERREICHBAR':(row.value?row.value+' ?':'UNCONFIRMED');
      line.appendChild(name);line.appendChild(value);el('actions').appendChild(line);
    });
    el('hint').textContent=current==='recording'?'Back to send · '+data.seconds+'s':current==='processing'?'Understanding your request':
      current==='responding'?'Reply on HomePod':current==='connecting'?'Secure connection to Mac mini':
      current==='error'?(errors[data.reason]||'Connection problem. Tap i for status.'):
      current==='offline'?'Open on the watch · Check service':data.reason==='no_speech'?'No speech heard. Try again.':'Tap the microphone or press Back';
    el('watch-status').textContent=current==='offline'?'Offline':'Online';
    var service=data.services||{};
    el('backend-status').textContent=service.backend==='online'?'Online':(current==='error'?'Check failed':'Not checked');
    el('ha-status').textContent=service.home_assistant==='online'?'Online':service.home_assistant==='unavailable'?'Unavailable':'Not checked';
    el('cloud-status').textContent=service.cloud==='configured'?'Configured':'Not checked';el('model-name').textContent=service.model||'';
    if(current!==last){if(current==='recording')vibrate(35);else if(current==='complete')vibrate([25,55,25]);else if(current==='error')vibrate(110);last=current;}
    power(['connecting','recording','processing','responding'].indexOf(current)>=0);
  }
  function talk(){if(current==='recording'){vibrate(20);send({action:'stop'});}else if(['idle','complete','error'].indexOf(current)>=0)send({action:'start'});else if(['connecting','processing','responding'].indexOf(current)>=0)send({action:'cancel'});}
  function connect(){
    clearTimeout(reconnect);
    if(document.hidden||(ws&&(ws.readyState===0||ws.readyState===1)))return;
    if(!window.GEAR_CONTROL_KEY){draw({state:'offline',gain:gain});return;}
    var socket;
    try{socket=new WebSocket('ws://127.0.0.1:8765/control','gearptt.'+window.GEAR_CONTROL_KEY);ws=socket;}
    catch(_){reconnect=setTimeout(connect,2000);return;}
    socket.onmessage=function(event){if(ws!==socket)return;try{draw(JSON.parse(event.data));}catch(_){}};
    socket.onclose=function(){if(ws!==socket)return;ws=null;power(false);draw({state:'offline',gain:gain});clearTimeout(reconnect);if(!document.hidden)reconnect=setTimeout(connect,2000);};socket.onerror=function(){};
  }
  function showPanel(on){panel=on;el('panel').className=on?'open':'';if(on&&['idle','complete','error'].indexOf(current)>=0)send({action:'check'});}
  el('talk').onclick=talk;el('again').onclick=talk;el('info').onclick=function(){showPanel(true);};
  el('close-panel').onclick=function(){showPanel(false);};el('refresh').onclick=function(){send({action:'check'});};
  el('exit').onclick=function(){send({action:'cancel'});power(false);try{tizen.application.getCurrentApplication().exit();}catch(_){}};
  document.addEventListener('tizenhwkey',function(event){if(event.keyName==='back'){event.preventDefault();if(panel)showPanel(false);else talk();}});
  document.addEventListener('rotarydetent',function(event){event.preventDefault();if(['idle','complete','error'].indexOf(current)>=0&&!panel)send({action:'gain',value:gain+(event.detail.direction==='CW'?3:-3)});});
  document.addEventListener('visibilitychange',function(){if(document.hidden){send({action:'cancel'});clearTimeout(reconnect);var old=ws;ws=null;if(old)old.close();power(false);}else connect();});
  window.addEventListener('unload',function(){send({action:'cancel'});power(false);});connect();
}());
