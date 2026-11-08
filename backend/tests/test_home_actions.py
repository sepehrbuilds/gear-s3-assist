import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from desk_stream.gear import Lights, GearConversation
from desk_stream.home_actions import Devices, ActionError
from desk_stream.common import Trace
from test_gear import watch_config


def cfg():
    c=watch_config();c['watch_devices']={
        'door':{'label':'Haustür','entity':'lock.example_front_door'},
        'tv':{'label':'TV','entity':'media_player.example_tv','rest_url':'http://127.0.0.1:1',
              'apps':{'youtube':{'id':'111299001912','label':'YouTube'},'jellyfin':{'id':'3202007021208','label':'Jellyfin'}},
              'sources':{'ps5':'hdmi1'}}}
    return c


def calls(*rows):return [{'name':'apply_home','arguments':{'actions':list(rows)}}]
def row(target,action,**kw):return dict(target=target,action=action,**kw)
def devices(http=None,c=None):
    c=c or cfg();return Devices(Lights(http,c),c)


@pytest.mark.parametrize('text', ['Lock the door','Please lock the front door','Schließe die Haustür ab',
    'Verriegele die Tür','Verriegle die Wohnungstür','Schließe die Eingangstür ab','در را قفل کن','Open YouTube and lock the door'])
def test_explicit_lock_only_positive_gate(text):
    plan=devices().validate(calls(row('door','lock')),utterance=text)
    assert plan[0].service=='lock/lock' and plan[0].expected=={'state':'locked'}


@pytest.mark.parametrize('text', ['Unlock the door','Do not lock the door',"Don't lock the door",'Open the door',
    'Schließe die Tür nicht ab','Entriegele die Tür','Wenn ich gehe, schließe die Tür ab','در را باز کن','در را قفل نکن',
    'Is the door locked?','What happens if I lock the door?', 'Explain how to lock the door', 'Say "lock the door"', "Read 'lock the door' aloud"])
def test_inverse_negated_conditional_and_questions_cannot_lock(text):
    with pytest.raises(ActionError):devices().validate(calls(row('door','lock')),utterance=text)


@pytest.mark.parametrize('action',['unlock','open','unlatch','toggle','on','off','set_light'])
def test_no_other_door_action_even_with_positive_transcript(action):
    with pytest.raises(ActionError):devices().validate(calls(row('flur','on'),row('door',action)),utterance='Lock the door')


def test_no_model_entities_services_urls_or_appids():
    for item in [row('door','lock',entity_id='lock.other'),row('tv','open_app',app='unknown'),
                 row('tv','open_app',app='youtube',url='http://127.0.0.1'),
                 row('tv','on',service='lock.unlock'),row('flur','on',service='lock.unlock')]:
        with pytest.raises(ActionError):devices().validate(calls(item),utterance='Lock the door')


def test_normalize_zero_before_specific_override_and_full_desired_state_conflict():
    plan=devices().validate(calls(row('wohnzimmer','on'),row('stehlampe','set_light',brightness_pct=0)))
    by={j.entity:j for j in plan}
    assert by['light.example_floor_lamp'].expected=={'state':'off'}
    assert by['light.example_tv_backlight'].expected=={'state':'on'}
    for value in [-1,101,True,4.5]:
        with pytest.raises(ActionError):devices().validate(calls(row('stehlampe','set_light',brightness_pct=value)))
    with pytest.raises(ActionError):devices().validate(calls(row('stehlampe','set_light',rgb=[255,0,0],kelvin=2700)))
    with pytest.raises(ActionError):devices().validate(calls(row('stehlampe','set_light')))


def test_tv_on_and_app_is_one_dependency_plan_in_either_order():
    for rows in [(row('tv','on'),row('tv','open_app',app='youtube')),(row('tv','open_app',app='youtube'),row('tv','on'))]:
        plan=devices().validate(calls(*rows));assert len(plan)==1 and plan[0].service=='tv/open_app'
    with pytest.raises(ActionError):devices().validate(calls(row('tv','open_app',app='youtube'),row('tv','open_app',app='jellyfin')))


async def fake_home(monkeypatch):
    c=cfg();posts=[]
    states={}
    for target in c['watch_targets'].values():
        for entity in target['entities']:
            modes=['rgb','color_temp'] if entity in ('light.example_floor_lamp','light.example_tv_backlight') else ['color_temp'] if entity=='light.example_bathroom' else ['onoff']
            states[entity]={'state':'off','attributes':{'supported_color_modes':modes,'min_color_temp_kelvin':2000,'max_color_temp_kelvin':6535 if entity=='light.example_bathroom' else 9000}}
    states['lock.example_front_door']={'state':'unlocked','attributes':{}}
    states['media_player.example_tv']={'state':'on','attributes':{'source':'digital_tv'}}
    apps={'111299001912':{'visible':False,'running':True,'id':'111299001912'},'3202007021208':{'visible':False,'running':True,'id':'3202007021208'}}
    async def read(request):return web.json_response(states.get(request.match_info['entity'],{'state':'unavailable','attributes':{}}))
    async def service(request):
        data=await request.json();name=request.match_info['domain']+'/'+request.match_info['service'];posts.append((name,data))
        entities=data['entity_id'] if isinstance(data['entity_id'],list) else [data['entity_id']]
        for entity in entities:
            states[entity]['state']='off' if name in ('light/turn_off','media_player/turn_off') else 'locked' if name=='lock/lock' else 'on'
            attrs=states[entity]['attributes']
            if 'brightness_pct' in data:attrs['brightness']=round(data['brightness_pct']*255/100)
            for k in ['rgb_color','color_temp_kelvin','source']:
                if k in data:attrs[k]=data[k]
        return web.json_response([])
    async def app(request):
        aid=request.match_info['app']
        if request.method=='POST':posts.append(('tv/open_app',aid));apps[aid]['visible']=True
        return web.json_response(apps[aid])
    server=web.Application();server.router.add_get('/api/states/{entity}',read);server.router.add_post('/api/services/{domain}/{service}',service)
    server.router.add_route('*','/api/v2/applications/{app}',app)
    return server,c,posts,states


async def test_full_preflight_rejects_unsupported_room_before_lock_or_other_effects(monkeypatch):
    app,c,posts,_=await fake_home(monkeypatch)
    async with TestServer(app) as server,aiohttp.ClientSession() as http:
        c.update(ha_url=str(server.make_url('')).rstrip('/'),ha_token_env='GEAR_TEST_HA');monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        d=devices(http,c)
        with pytest.raises(ActionError):
            await d.execute(calls(row('wohnzimmer','set_light',brightness_pct=50),row('door','lock')),utterance='Lock the door and dim the living room to 50 percent')
        assert posts==[]


async def test_brightness_rgb_white_and_lock_all_confirmed(monkeypatch):
    app,c,posts,states=await fake_home(monkeypatch)
    async with TestServer(app) as server,aiohttp.ClientSession() as http:
        c.update(ha_url=str(server.make_url('')).rstrip('/'),ha_token_env='GEAR_TEST_HA');monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        d=devices(http,c)
        result=await d.execute(calls(row('stehlampe','set_light',brightness_pct=50,rgb=[0,0,255]),row('bad','set_light',brightness_pct=40,kelvin=2700),row('door','lock')),utterance='Lock the door and adjust the lights')
        assert all(r['status']=='confirmed' for r in result)
        assert states['lock.example_front_door']['state']=='locked'
        assert set(s for s,_ in posts)<={'light/turn_on','lock/lock'}
        assert len(posts)==3


async def test_app_powers_tv_then_opens_fixed_id_once_and_hdmi_works(monkeypatch):
    app,c,posts,states=await fake_home(monkeypatch)
    async with TestServer(app) as server,aiohttp.ClientSession() as http:
        base=str(server.make_url('')).rstrip('/');c.update(ha_url=base,ha_token_env='GEAR_TEST_HA');c['watch_devices']['tv']['rest_url']=base;monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        states['media_player.example_tv']['state']='off';d=devices(http,c)
        result=await d.execute(calls(row('tv','open_app',app='youtube')))
        assert result[0]['status']=='confirmed'
        assert [name for name,_ in posts]==['media_player/turn_on','tv/open_app']
        result=await d.execute(calls(row('tv','select_input',input='ps5')))
        assert result[0]['status']=='confirmed' and states['media_player.example_tv']['attributes']['source']=='hdmi1'


@pytest.mark.parametrize('initial',['on','off'])
async def test_tv_off_never_wakes_tv_and_requires_observed_off(monkeypatch,initial):
    app,c,posts,states=await fake_home(monkeypatch)
    async with TestServer(app) as server,aiohttp.ClientSession() as http:
        c.update(ha_url=str(server.make_url('')).rstrip('/'),ha_token_env='GEAR_TEST_HA');monkeypatch.setenv('GEAR_TEST_HA','synthetic-'*8)
        states['media_player.example_tv']['state']=initial;d=devices(http,c)
        result=await d.execute(calls(row('tv','off'),row('flur','on')))
        assert all(r['status']=='confirmed' for r in result)
        assert [name for name,_ in posts].count('media_player/turn_off')==1
        assert 'media_player/turn_on' not in [name for name,_ in posts]
        assert states['media_player.example_tv']['state']=='off' and states['light.example_hallway']['state']=='on'
        assert next(r for r in result if r['target']=='tv')['value']=='OFF'
        job=d.validate(calls(row('tv','off')))[0]
        for state in ('on','playing','idle','paused','unavailable','unknown'):
            assert not d.matches(job,{'state':state})


@pytest.mark.parametrize('other',[row('tv','on'),row('tv','open_app',app='youtube'),row('tv','select_input',input='ps5'),row('tv','off')])
def test_tv_off_conflicts_with_other_tv_actions_in_either_order(other):
    for rows in [(row('tv','off'),other),(other,row('tv','off'))]:
        with pytest.raises(ActionError):devices().validate(calls(*rows))
    with pytest.raises(ActionError):devices().validate(calls(row('tv','off',app='youtube')))


async def test_refused_lock_never_reaches_http_or_speech(monkeypatch):
    class Model:
        async def stream(self,*args):yield {'tool':{'name':'apply_home','arguments':{'actions':[row('door','lock'),row('flur','on')]}}}
    async with aiohttp.ClientSession() as http:
        convo=GearConversation(cfg(),Model(),None,http);messages=[]
        async def forbidden(*args,**kwargs):raise AssertionError('no HTTP permitted')
        monkeypatch.setattr(convo.lights,'read',forbidden)
        async def send(state,**extra):messages.append({'state':state,**extra})
        await convo.answer('Do not lock the door',Trace('synthetic'),send)
        assert len(messages)==1 and messages[0]['text']=='Nicht ausgeführt'


def test_user_policy_keeps_bad_onoff_only_despite_hardware_capabilities():
    c=cfg();c['watch_onoff_only']=['light.example_bathroom'];d=devices(c=c)
    for attrs in [{'brightness_pct':50},{'brightness_pct':0},{'kelvin':2700},{'rgb':[255,0,0]}]:
        with pytest.raises(ActionError):d.validate(calls(row('bad','set_light',**attrs)))
    assert d.validate(calls(row('bad','on')))[0].service=='light/turn_on'
    assert d.validate(calls(row('bad','off')))[0].service=='light/turn_off'
    assert d.validate(calls(row('stehlampe','set_light',brightness_pct=50)))[0].data=={'brightness_pct':50}
