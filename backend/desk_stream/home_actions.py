"""Fixed watch action allowlist. No model-controlled services, URLs or entity IDs."""
import asyncio
import re
import time
from dataclasses import dataclass

import aiohttp


class ActionError(ValueError):
    def __init__(self, label, message):
        self.label, self.message = label, message
        super().__init__(message)


@dataclass
class Job:
    target: str
    entity: str
    service: str
    data: dict
    expected: dict
    label: str
    value: str


class Devices:
    def __init__(self, lights, cfg):
        self.lights, self.cfg, self.http = lights, cfg, lights.http
        self.targets = lights.targets
        self.extra = cfg.get('watch_devices', {})

    async def status(self):
        return await self.lights.status()

    def schema(self):
        if not self.cfg.get('ha_enabled'):
            return []
        catalog = {k:v['label'] for k,v in self.targets.items()}
        catalog.update({k:v['label'] for k,v in self.extra.items()})
        apps = list(self.extra.get('tv',{}).get('apps',{}))
        sources = list(self.extra.get('tv',{}).get('sources',{}))
        parameters = {
            'target': {'type':'string','enum':list(catalog)},
            'action': {'type':'string','description':'TV power uses on or off. open_app REQUIRES app. select_input REQUIRES input. lock is door only. set_light requires brightness/color/kelvin.', 'enum':['on','off','set_light','lock','open_app','select_input']},
            'brightness_pct': {'type':'integer','minimum':0,'maximum':100},
            'rgb': {'type':'array','minItems':3,'maxItems':3,'items':{'type':'integer','minimum':0,'maximum':255}},
            'kelvin': {'type':'integer','minimum':1000,'maximum':10000}}
        if apps: parameters['app']={'type':'string','enum':apps}
        if sources: parameters['input']={'type':'string','enum':sources}
        caps = self.cfg.get('watch_light_capabilities',{})
        details = '; '.join(k+': '+str(v) for k,v in caps.items())
        return [{'type':'function','function':{
            'name':'apply_home',
            'description':'Apply ALL explicit home requests in one batch. '+
                'Targets: '+str(catalog)+'. Door: LOCK ONLY, never unlock/open. TV: on, off, open_app, select_input. '+
                'Lights: on/off/set_light. Brightness/color may only target capable lights. '+
                'For mixed-capability rooms ask to select a capable individual; do not silently omit a light. '+
                'Capabilities by entity: '+details,
            'parameters':{'type':'object','additionalProperties':False,
                'properties':{'actions':{'type':'array','minItems':1,'maxItems':12,
                    'items':{'type':'object','additionalProperties':False,'properties':parameters,
                             'required':['target','action']}}},'required':['actions']}}}]

    def direct(self, text):
        calls = self.lights.direct(text)
        if calls: calls[0]['name']='apply_home'
        return calls

    def _error(self,target,message):
        label = (self.targets.get(target) or self.extra.get(target) or {}).get('label','Anfrage')
        raise ActionError(label,message)

    @staticmethod
    def door_requested(text):
        # Fail closed for negation, inverse actions or conditional door requests.
        # A positive lock phrase is required independently of the cloud output.
        if not isinstance(text,str): return False
        s=text.lower()
        bad=r"\b(unlock|unlatch|don't|dont|not|never|nicht|kein|keine|entriegel\w*|aufschlie\w*|aufschl[ie]ss\w*|if|unless|wenn|falls)\b|قفل\s+نکن|قفلش\s+نکن"
        if re.search(bad,s) or re.search(r'[\"“”„«»]|(?:^|\s)\'|\b(how|why|whether|explain|describe|quote|means|wie|warum|bedeutet)\b|چگونه|چطور|آیا',s): return False
        for clause in re.split(r'\b(?:and|und)\b|\s+و\s+|[;,]',s):
            if re.search(r'\b(?:door|haustür|tür)\b|(?:درب|در)\s',clause) and re.search(r'\bopen\b|öffn\w*|باز\s+کن',clause):return False
        return bool(re.search(r'\block\s+(?:the\s+)?(?:front\s+)?door\b|\bverrieg(?:el|l)\w*\s+(?:(?:die|meine|unsere)\s+)?(?:(?:haus|wohnungs|eingangs)?(?:tür|tuer))\b|\bschlie(?:ß|ss)\w*\s+(?:(?:die|meine|unsere)\s+)?(?:(?:haus|wohnungs|eingangs)?(?:tür|tuer))\s+ab\b|\b(?:(?:haus|wohnungs|eingangs)?(?:tür|tuer))\s+(?:abschließen|abschliessen|verriegeln)\b|(?:درب|در)\s+.{0,25}قفل\s+کن',s))

    def validate(self, calls, *, utterance=None):
        if not self.cfg.get('ha_enabled') or len(calls)!=1:
            self._error('', 'Ungültige Anzahl von Aktionen')
        call=calls[0]
        if not isinstance(call,dict) or call.get('name')!='apply_home' or not isinstance(call.get('arguments'),dict) or set(call['arguments'])!={'actions'}:
            self._error('', 'Ungültige Aktion')
        rows=call['arguments']['actions']
        if not isinstance(rows,list) or not 1<=len(rows)<=12:
            self._error('', 'Zu viele Aktionen')
        jobs={};seen=set()
        for row in rows:
            if not isinstance(row,dict) or not {'target','action'}<=set(row):self._error('','Ungültige Aktion')
            target,action=row['target'],row['action']
            if not isinstance(target,str) or (target in seen and target!='tv'):self._error('','Doppeltes Ziel')
            seen.add(target)
            if target in self.targets:
                if set(row)-{'target','action','brightness_pct','rgb','kelvin'} or action not in ('on','off','set_light'):
                    self._error(target,'Aktion nicht unterstützt')
                attrs={k:row[k] for k in ('brightness_pct','rgb','kelvin') if k in row}
                if attrs and any(e in self.cfg.get('watch_onoff_only',[]) for e in self.targets[target]['entities']):
                    self._error(target,'Nur EIN/AUS freigegeben')
                pct=attrs.get('brightness_pct')
                if pct is not None and (type(pct) is not int or not 0<=pct<=100):self._error(target,'Helligkeit muss 0 bis 100 sein')
                rgb=attrs.get('rgb');kelvin=attrs.get('kelvin')
                if rgb is not None and (not isinstance(rgb,list) or len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb)):
                    self._error(target,'Ungültige Farbe')
                if kelvin is not None and (type(kelvin) is not int or not 1000<=kelvin<=10000):self._error(target,'Ungültige Weißtemperatur')
                if rgb is not None and kelvin is not None:self._error(target,'Farbe und Weißtemperatur widersprechen sich')
                if action=='set_light' and not attrs:self._error(target,'Keine Einstellung angegeben')
                if action=='off' and attrs:self._error(target,'Ausschalten mit Farbe ist widersprüchlich')
                if pct==0:
                    if rgb is not None or kelvin is not None:self._error(target,'0 Prozent mit Farbe ist widersprüchlich')
                    action='off';attrs={}
                data={k:v for k,v in [('brightness_pct',pct),('rgb_color',rgb),('color_temp_kelvin',kelvin)] if v is not None} if attrs else {}
                expected={'state':'off' if action=='off' else 'on',**data}
                value='OFF' if action=='off' else self.describe(data)
                for entity in self.targets[target]['entities']:
                    if not re.fullmatch(r'light\.[a-z0-9_]+',entity):self._error(target,'Ungültiges Licht')
                    job=Job(target,entity,'light/turn_off' if action=='off' else 'light/turn_on',data.copy(),expected.copy(),self.targets[target]['label'],value)
                    previous=jobs.get(entity)
                    if previous and previous.expected!=job.expected:
                        old_single=len(self.targets[previous.target]['entities'])==1
                        new_single=len(self.targets[target]['entities'])==1
                        if old_single==new_single:self._error(target,'Widersprüchliche Einstellungen')
                        if old_single:continue
                    jobs[entity]=job
            elif target=='door' and target in self.extra:
                if set(row)!={'target','action'} or action!='lock' or not self.door_requested(utterance):
                    self._error(target,'Nur ausdrücklich verriegeln erlaubt')
                entity=self.extra[target]['entity']
                if entity!='lock.example_front_door':self._error(target,'Tür nicht freigegeben')
                jobs[entity]=Job(target,entity,'lock/lock',{}, {'state':'locked'},self.extra[target]['label'],'VERRIEGELT')
            elif target=='tv' and target in self.extra:
                cfg=self.extra[target];entity=cfg['entity']
                if entity!='media_player.example_tv':self._error(target,'TV nicht freigegeben')
                if action=='on' and set(row)=={'target','action'}:
                    job=Job(target,entity,'media_player/turn_on',{}, {'state':'on'},'TV','ON')
                elif action=='off' and set(row)=={'target','action'}:
                    job=Job(target,entity,'media_player/turn_off',{}, {'state':'off'},'TV','OFF')
                elif action=='select_input' and set(row)=={'target','action','input'} and isinstance(row['input'],str) and row['input'] in cfg.get('sources',{}):
                    source=cfg['sources'][row['input']]
                    job=Job(target,entity,'media_player/select_source',{'source':source},{'state':'on','source':source},'TV',row['input'].upper())
                elif action=='open_app' and set(row)=={'target','action','app'} and isinstance(row['app'],str) and row['app'] in cfg.get('apps',{}):
                    app=cfg['apps'][row['app']]
                    if not re.fullmatch(r'[0-9]{1,32}',app['id']) or not cfg.get('rest_url'):
                        self._error(target,'TV-App-Verbindung noch nicht eingerichtet')
                    job=Job(target,entity,'tv/open_app',{'app':row['app']},
                            {'state':'on','app':app},'TV',app['label'])
                else:self._error(target,'TV-Aktion nicht freigegeben')
                previous=jobs.get(entity)
                if previous:
                    if 'media_player/turn_off' in (previous.service,job.service):self._error(target,'Widersprüchliche TV-Aktionen')
                    if previous.service=='media_player/turn_on' and job.service!='media_player/turn_on':pass
                    elif job.service=='media_player/turn_on' and previous.service!='media_player/turn_on':continue
                    else:self._error(target,'Widersprüchliche TV-Aktionen')
                jobs[entity]=job
            else:self._error('', 'Ziel nicht freigegeben')
        if len(jobs)>12:self._error('','Zu viele Geräte')
        return list(jobs.values())

    @staticmethod
    def describe(data):
        parts=[]
        if 'brightness_pct' in data:parts.append(str(data['brightness_pct'])+'%')
        if 'rgb_color' in data:
            names={(255,0,0):'Rot',(0,255,0):'Grün',(0,0,255):'Blau',(255,255,0):'Gelb',(255,165,0):'Orange',(128,0,128):'Violett',(255,255,255):'Weiß',(0,255,255):'Türkis',(255,192,203):'Rosa'}
            parts.append(names.get(tuple(data['rgb_color']),'Farbe'))
        if 'color_temp_kelvin' in data:parts.append(str(data['color_temp_kelvin'])+' K')
        return ' · '.join(parts) or 'ON'

    async def read(self,entity):
        try:
            async with self.http.get(self.cfg['ha_url'].rstrip('/')+'/api/states/'+entity,
                headers=self.lights.headers(),timeout=aiohttp.ClientTimeout(total=1)) as r:
                if r.status==200:return await r.json()
        except (TimeoutError,aiohttp.ClientError,ValueError):pass
        return {'state':'unavailable','attributes':{}}

    async def preflight(self,jobs):
        states=await asyncio.gather(*(self.read(j.entity) for j in jobs))
        for job,state in zip(jobs,states):
            if state.get('state') in ('unavailable','unknown'):
                if job.target=='tv' and 'app' in job.expected:
                    power=await self.read(self.extra['tv']['entity'])
                    if power.get('state')=='off':continue
                self._error(job.target,job.label+' ist nicht erreichbar')
            if not job.entity.startswith('light.') or not job.data:continue
            attrs=state.get('attributes',{});modes=set(attrs.get('supported_color_modes',[]))
            if 'brightness_pct' in job.data and not modes-{'onoff','unknown'}:
                self._error(job.target,job.label+' ist nicht dimmbar')
            if 'rgb_color' in job.data and not modes&{'hs','rgb','rgbw','rgbww','xy'}:
                self._error(job.target,job.label+' unterstützt keine Farbe')
            if 'color_temp_kelvin' in job.data:
                k=job.data['color_temp_kelvin']
                if 'color_temp' not in modes or not attrs.get('min_color_temp_kelvin',0)<=k<=attrs.get('max_color_temp_kelvin',0):
                    self._error(job.target,job.label+' unterstützt diese Weißtemperatur nicht')

    async def post(self,service,data):
        # This constant allowlist is the only route to a side effect. No unlock,
        # open, latch, toggle, arbitrary script or model-defined service exists.
        if service=='tv/open_app':
            app=self.extra['tv'].get('apps',{}).get(data.get('app'))
            if not app:raise ValueError('app not allowed')
            try:
                async with self.http.post(self.extra['tv']['rest_url'].rstrip('/')+'/api/v2/applications/'+app['id'],
                        timeout=aiohttp.ClientTimeout(total=4)) as r:
                    return 'accepted' if r.status==200 else 'rejected'
            except (TimeoutError,aiohttp.ClientError):return 'unknown'
        if service not in {'light/turn_on','light/turn_off','lock/lock','media_player/turn_on','media_player/turn_off','media_player/select_source'}:
            raise ValueError('service not allowed')
        try:
            async with self.http.post(self.cfg['ha_url'].rstrip('/')+'/api/services/'+service,
                headers=self.lights.headers(),json=data,timeout=aiohttp.ClientTimeout(total=4)) as r:
                return 'accepted' if r.status==200 else 'rejected'
        except (TimeoutError,aiohttp.ClientError):return 'unknown'

    @staticmethod
    def matches(job,state):
        expected=job.expected;actual=state.get('state');attrs=state.get('attributes',{})
        if job.entity.startswith('media_player.'):
            if expected['state']=='off':return actual=='off'
            if actual not in ('on','idle','playing','paused','buffering'):return False
        elif actual!=expected['state']:return False
        if 'brightness_pct' in expected:
            if not isinstance(attrs.get('brightness'),(int,float)) or abs(attrs['brightness']-round(expected['brightness_pct']*255/100))>4:return False
        if 'color_temp_kelvin' in expected:
            if not isinstance(attrs.get('color_temp_kelvin'),(int,float)) or abs(attrs['color_temp_kelvin']-expected['color_temp_kelvin'])>100:return False
        if 'rgb_color' in expected:
            actual_rgb=attrs.get('rgb_color');desired=expected['rgb_color']
            if not isinstance(actual_rgb,(list,tuple)) or len(actual_rgb)!=3:return False
            # Compare chromaticity so brightness scaling doesn't invalidate hue.
            a=max(actual_rgb);b=max(desired)
            if not a or not b:return False
            if max(abs(x/a-y/b) for x,y in zip(actual_rgb,desired))>.15:return False
        if 'source' in expected and attrs.get('source')!=expected['source']:return False
        if 'app' in expected:
            app=expected['app']
            if attrs.get('app_id')!=app['id'] and attrs.get('source')!=app['label']:return False
        return True

    async def wait(self,job,seconds):
        deadline=time.monotonic()+seconds;state={}
        while True:
            if 'app' in job.expected:
                try:
                    app=job.expected['app']
                    async with self.http.get(self.extra['tv']['rest_url'].rstrip('/')+'/api/v2/applications/'+app['id'],
                            timeout=aiohttp.ClientTimeout(total=1)) as response:
                        current=await response.json()
                        if response.status==200 and current.get('id')==app['id'] and current.get('visible') is True and current.get('running') is True:
                            return 'confirmed'
                except (TimeoutError,aiohttp.ClientError,ValueError):pass
            state=await self.read(job.entity)
            if 'app' not in job.expected and self.matches(job,state):return 'confirmed'
            if state.get('state')=='jammed':return 'jammed'
            if state.get('state') in ('unavailable','unknown'):return 'unavailable'
            if time.monotonic()>=deadline:break
            await asyncio.sleep(.25)
        return 'unconfirmed'

    async def execute(self,calls,*,utterance=None):
        jobs=self.validate(calls,utterance=utterance)
        await self.preflight(jobs)  # no effects before ALL targets/capabilities pass
        groups={}
        for job in jobs:
            if job.entity.startswith('light.'):
                key=(job.service,str(sorted(job.data.items())))
                groups.setdefault(key,[]).append(job)
        async def lights(group):
            result=await self.post(group[0].service,{'entity_id':[j.entity for j in group],**group[0].data})
            return [(job,result) for job in group]
        async def device(job):
            if job.target=='tv' and job.service in ('tv/open_app','media_player/select_source'):
                state=await self.read(self.extra['tv']['entity'])
                if state.get('state')=='off':
                    result=await self.post('media_player/turn_on',{'entity_id':self.extra['tv']['entity']})
                    power=Job('tv',self.extra['tv']['entity'],'media_player/turn_on',{}, {'state':'on'},'TV','ON')
                    if result=='rejected' or await self.wait(power,15)!='confirmed':return [(job,'unknown')]
            return [(job,await self.post(job.service,{'entity_id':job.entity,**job.data}))]
        tasks=[lights(group) for group in groups.values()]+[device(j) for j in jobs if not j.entity.startswith('light.')]
        submitted=[pair for pairs in await asyncio.gather(*tasks) for pair in pairs]
        async def result(job,accepted):
            status='rejected' if accepted=='rejected' else await self.wait(job,8 if job.target in ('tv','door') else 2)
            return {'target':job.target,'entity':job.entity,'action':job.expected['state'],
                    'status':status,'value':job.value,'label':job.label}
        return await asyncio.gather(*(result(job,accepted) for job,accepted in submitted))
