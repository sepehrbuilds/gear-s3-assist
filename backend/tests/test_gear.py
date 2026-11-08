import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
import pytest

from desk_stream.common import Trace
from desk_stream.gear import Lights, GearConversation
from desk_stream.model import Model
from desk_stream.server import Backend, app_for
from test_pipeline import config, Vad, ASR, TOKEN, until


def watch_config():
    cfg = config()
    cfg.update(watch_mode=True, playback='watch', provider='openai-compatible', ha_enabled=True,
               max_tool_calls=1, watch_targets=json.loads((Path(__file__).parents[1]/'watch-targets.json').read_text()))
    return cfg


def batch(**actions):
    return [{'name':'apply_lights','arguments':{'actions':[{'target':t,'action':a} for t,a in actions.items()]}}]


def test_exact_map_and_specific_overrides_room():
    control=Lights(None,watch_config())
    rows=control.validate(batch(wohnzimmer='off',kuche='off',flur='on',tv_backlight='on'))
    result={entity:action for _,entity,action in rows}
    assert result == {'light.example_ceiling':'off','light.example_floor_lamp':'off','light.example_tv_backlight':'on',
        'light.example_kitchen_1':'off','light.example_kitchen_2':'off','light.example_kitchen_3':'off','light.example_hallway':'on'}
    reverse=control.validate(batch(tv_backlight='on',wohnzimmer='off'))
    assert dict((e,a) for _,e,a in reverse)['light.example_tv_backlight']=='on'
    assert len(control.validate(batch(kuche='on')))==3


def test_full_batch_rejected_before_side_effects():
    control=Lights(None,watch_config())
    for calls in [batch(flur='toggle'),batch(unknown='on'),batch(flur='on')*2,
            [{'name':'apply_lights','arguments':{'actions':[{'target':'flur','action':'on'},{'target':'flur','action':'off'}]}}]]:
        with pytest.raises(ValueError):control.validate(calls)


def test_direct_only_complete_unambiguous_grammar():
    control=Lights(None,watch_config())
    assert control.direct('Schalte die Stehlampe aus.') == batch(stehlampe='off')
    assert control.direct('Turn on the kitchen lights.') == batch(kuche='on')
    for text in ['Nicht die Stehlampe aus','Schalte die Stehlampe nicht aus','Can you turn off the kitchen lights?',
                 'Turn kitchen lights off except light two','Flur an und Küche aus','What does "Flur an" mean?']:
        assert control.direct(text) is None


async def test_execute_groups_and_checks_states_no_automatic_retry(monkeypatch):
    requests=[];states={};cfg=watch_config()
    async def service(request):
        body=await request.json();requests.append((request.match_info['service'],body['entity_id']))
        for entity in body['entity_id']:states[entity]='on' if request.match_info['service']=='turn_on' else 'off'
        return web.json_response([])
    async def state(request):return web.json_response({'state':states.get(request.match_info['entity'],'unknown')})
    app=web.Application();app.router.add_post('/api/services/light/{service}',service);app.router.add_get('/api/states/{entity}',state)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        cfg.update(ha_url=str(server.make_url('')).rstrip('/'),ha_token_env='GEAR_TEST_HA')
        monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        result=await Lights(http,cfg).execute(batch(wohnzimmer='off',tv_backlight='on',kuche='off',flur='on'))
        assert len(requests)==2 and all(row['status']=='confirmed' for row in result)
        assert len({entity for _,entities in requests for entity in entities})==7
        assert set(requests[0][1]).isdisjoint(requests[1][1])
        with pytest.raises(ValueError):await Lights(http,cfg).execute(batch(flur='on',invalid='off'))
        assert len(requests)==2


async def test_service_acceptance_is_not_reported_as_verified(monkeypatch):
    cfg=watch_config();posts=[]
    async def service(request):posts.append(await request.json());return web.json_response([])
    async def state(request):return web.json_response({'state':'unavailable'})
    app=web.Application();app.router.add_post('/api/services/light/{service}',service);app.router.add_get('/api/states/{entity}',state)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        cfg.update(ha_url=str(server.make_url('')).rstrip('/'),ha_token_env='GEAR_TEST_HA');monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        result=await Lights(http,cfg).execute(batch(flur='on'))
        assert len(posts)==1 and result[0]['status']=='unconfirmed'


async def test_command_sends_watch_result_without_speech_or_cloud(monkeypatch):
    class NoModel:
        async def stream(self,*args):raise AssertionError('direct command should not use cloud')
    cfg=watch_config();messages=[]
    async with aiohttp.ClientSession() as http:
        convo=GearConversation(cfg,NoModel(),None,http)
        async def execute(calls, **kwargs):
            return [{'target':'flur','entity':'light.example_hallway','action':'on','status':'confirmed'}]
        monkeypatch.setattr(convo.lights,'execute',execute)
        async def send(state,**extra):messages.append(dict(state=state,**extra))
        await convo.answer('Flur an',Trace('test'),send)
        assert messages==[{'state':'result','kind':'home','text':'Erledigt',
                          'results':[{'label':'Flur','action':'on','value':'ON','status':'confirmed'}]}]


async def test_cloud_does_not_wait_for_local_inference_lock(monkeypatch):
    import desk_stream.model as module
    def blocked(*args,**kwargs):raise AssertionError('cloud must not acquire local inference lock')
    monkeypatch.setattr(module,'inference_slot',blocked)
    async def respond(request):return web.Response(text='data: '+json.dumps({'choices':[{'delta':{'content':'hello'},'finish_reason':'stop'}]})+'\n\n',content_type='text/event-stream')
    app=web.Application();app.router.add_post('/chat/completions',respond)
    async with TestServer(app) as server,aiohttp.ClientSession() as http:
        cfg=watch_config();cfg.update(llm_url=str(server.make_url('')).rstrip('/'))
        assert [e async for e in Model(http,cfg).stream([])]==[{'text':'hello'}]


async def test_watch_protocol_version_and_status(monkeypatch):
    class Conversation:
        def reset(self): pass
        async def connection_status(self):return {'backend':'online','home_assistant':'online'}
    cfg=watch_config()
    async with TestClient(TestServer(app_for(Backend(cfg,TOKEN,Vad(),ASR(),Conversation())))) as client:
        ws=await client.ws_connect('/audio',headers={'Authorization':'Bearer '+TOKEN})
        assert (await ws.receive_json())['version']==2
        await ws.send_json({'type':'start','id':'synthetic-v1'})
        assert (await until(ws,'error'))['reason']=='invalid_control'
        await ws.send_json({'type':'status'})
        assert (await until(ws,'status'))['services']['home_assistant']=='online'
