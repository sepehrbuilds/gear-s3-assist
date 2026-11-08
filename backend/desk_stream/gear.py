"""Opt-in Gear watch conversation: bounded light batches and private text results."""
import asyncio
import json
import re
import time

import aiohttp

from .model import SYSTEM
from .server import Conversation
from .speech import Speech, speak_stream


class Lights:
    def __init__(self, http, cfg):
        self.http, self.cfg = http, cfg
        self.targets = cfg['watch_targets']

    def schema(self):
        if not self.cfg.get('ha_enabled'):
            return []
        return [{'type': 'function', 'function': {
            'name': 'apply_lights',
            'description': 'Apply the complete explicitly requested light changes in one batch. ' +
                'Targets: ' + json.dumps({k: v['label'] for k, v in self.targets.items()}, ensure_ascii=False),
            'parameters': {'type': 'object', 'additionalProperties': False,
                'properties': {'actions': {'type': 'array', 'minItems': 1, 'maxItems': 12,
                    'items': {'type': 'object', 'additionalProperties': False,
                        'properties': {'target': {'type': 'string', 'enum': list(self.targets)},
                                       'action': {'type': 'string', 'enum': ['on', 'off']}},
                        'required': ['target', 'action']}}}, 'required': ['actions']}}}]

    def validate(self, calls):
        if not self.cfg.get('ha_enabled') or len(calls) != 1:
            raise ValueError('exactly one enabled light batch required')
        call = calls[0]
        args = call.get('arguments')
        if call.get('name') != 'apply_lights' or not isinstance(args, dict) or set(args) != {'actions'}:
            raise ValueError('invalid light batch')
        rows = args['actions']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError('invalid action count')
        expanded, seen_targets = {}, set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'target', 'action'}:
                raise ValueError('invalid light action')
            target, action = row['target'], row['action']
            if not isinstance(target, str) or target not in self.targets or action not in ('on', 'off'):
                raise ValueError('unknown light target')
            if target in seen_targets:
                raise ValueError('duplicate or conflicting target')
            seen_targets.add(target)
            entities = self.targets[target]['entities']
            for entity in entities:
                if not re.fullmatch(r'light\.[a-z0-9_]+', entity):
                    raise ValueError('invalid configured entity')
                previous = expanded.get(entity)
                if previous and previous[2] != action:
                    old_specific = len(self.targets[previous[0]]['entities']) == 1
                    new_specific = len(entities) == 1
                    if old_specific == new_specific:
                        raise ValueError('conflicting targets')
                    if old_specific:
                        continue  # explicit individual request overrides room
                expanded[entity] = (target, entity, action)
        if len(expanded) > 12:
            raise ValueError('too many expanded targets')
        return list(expanded.values())

    def direct(self, text):
        """Only exact single-target grammar; all compound/unclear requests use cloud."""
        text = text.strip().lower().rstrip('.!?').strip()
        for target, item in self.targets.items():
            for alias in item.get('aliases', []):
                name = re.escape(alias.lower())
                patterns = [rf'(?:turn|switch|schalte|mach|mache)\s+(?:the\s+|das\s+|die\s+|den\s+)?{name}\s+(on|off|an|aus)',
                            rf'(?:turn|switch)\s+(on|off)\s+(?:the\s+)?{name}',
                            rf'{name}\s+(on|off|an|aus)']
                for pattern in patterns:
                    match = re.fullmatch(pattern, text)
                    if match:
                        action = 'on' if match[1] in ('on', 'an') else 'off'
                        return [{'name': 'apply_lights', 'arguments': {'actions': [{'target': target, 'action': action}]}}]
        return None

    def headers(self):
        from .common import token_from_env
        return {'Authorization': 'Bearer ' + token_from_env(self.cfg['ha_token_env'])}

    async def status(self):
        try:
            async with self.http.get(self.cfg['ha_url'].rstrip('/') + '/api/', headers=self.headers(),
                                     timeout=aiohttp.ClientTimeout(total=1)) as response:
                return 'online' if response.status == 200 else 'unavailable'
        except (ValueError, TimeoutError, aiohttp.ClientError):
            return 'unavailable'

    async def execute(self, calls):
        expanded = self.validate(calls)  # complete validation before any effects
        headers = self.headers()
        root = self.cfg['ha_url'].rstrip('/')
        async def submit(action):
            entities = [entity for _, entity, wanted in expanded if wanted == action]
            if not entities:
                return action, 'unused'
            try:
                async with self.http.post(root + '/api/services/light/turn_' + action,
                        headers=headers, json={'entity_id': entities},
                        timeout=aiohttp.ClientTimeout(total=3)) as response:
                    return action, 'accepted' if response.status == 200 else 'rejected'
            except (TimeoutError, aiohttp.ClientError):
                return action, 'unknown'  # never retry a possibly executed request
        submitted = dict(await asyncio.gather(submit('on'), submit('off')))
        async def confirm(target, entity, wanted):
            outcome = submitted[wanted]
            if outcome == 'rejected':
                return {'target': target, 'entity': entity, 'action': wanted, 'status': 'rejected'}
            deadline = time.monotonic() + 2
            observed = 'unknown'
            while True:
                try:
                    async with self.http.get(root + '/api/states/' + entity, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=.7)) as response:
                        if response.status == 200:
                            observed = (await response.json()).get('state', 'unknown')
                except (TimeoutError, aiohttp.ClientError, ValueError):
                    observed = 'unknown'
                if observed == wanted or time.monotonic() >= deadline or observed == 'unavailable':
                    break
                await asyncio.sleep(.15)
            return {'target': target, 'entity': entity, 'action': wanted,
                    'status': 'confirmed' if observed == wanted else 'unconfirmed'}
        return await asyncio.gather(*(confirm(*row) for row in expanded))


class GearConversation(Conversation):
    def __init__(self, cfg, model, control, http):
        super().__init__(cfg, model, control, http)
        from .home_actions import Devices
        self.lights = Devices(Lights(http, cfg), cfg)
        from .jev import Jev
        self.jev = Jev(http, cfg)

    async def connection_status(self):
        return {'backend': 'online', 'home_assistant': await self.lights.status(),
                'cloud': 'configured' if self.cfg['provider'] != 'ollama' else 'local',
                'model': self.cfg['model']}

    async def answer(self, text, trace, send):
        if time.monotonic() - self.last > self.cfg['history_ttl']:
            self.reset()
        calls = self.lights.direct(text) if self.cfg.get('ha_enabled') else None
        if not calls:
            calls = await self.jev.plan(text, trace)
            if calls:
                # Keep the same complete validation boundary before all effects.
                # Invalid Jev proposals fall back before execution starts.
                try:self.lights.validate(calls, utterance=text)
                except ValueError:
                    trace.mark('jev_validation_refused')
                    calls = None
        reply = ''
        if not calls:
            prompt = SYSTEM + (' You are a watch assistant. Use apply_home once with ALL requested '
                'actions for explicit supported home requests. Never call it for quotations, questions about '
                'capabilities, hypothetical requests, or unclear targets. Ask a short clarification '
                'when the target is ambiguous. Room targets contain all listed room lights; '
                'An explicit individual light request may override a room request. Never claim success before tool results. Keep ordinary answers under 400 characters. '
                'Door control is LOCK ONLY. Never map unlock, open-door, negation, or hypothetical requests to lock. '
                "A plain TV power request MUST use target tv, action on or off according to the requested power state. Turning off the TV is separate from turning off TV backlight. open_app is ONLY for an explicitly named app and MUST include app. select_input MUST include input. "
                'Only app/input requests power on the TV automatically. '
                'For brightness/colors honor each light capability; never silently omit unsupported room lights. '
                'Answer in the language of this turn; Persian stays in Persian script.')
            messages = [{'role': 'system', 'content': prompt}] + list(self.history) + [{'role': 'user', 'content': text}]
            while len(messages) > 2 and sum(len(m['content'].encode()) for m in messages) > 9000:
                del messages[1:3]
            trace.mark('llm_start')
            calls = []
            async for event in self.model.stream(messages, self.lights.schema()):
                if 'tool' in event:
                    calls.append(event['tool'])
                else:
                    reply += event['text']
                    if len(reply) > self.cfg['max_reply_chars']:
                        raise ValueError('reply too long')
            trace.mark('llm_complete')
        if calls:
            # Do not attach device execution to a cancellable playback job.
            # Once started, finish the bounded batch and retain its result even if
            # the watch disconnects; never re-run it as a recovery action.
            task = asyncio.create_task(self.lights.execute(calls, utterance=text))
            try:
                results = await asyncio.shield(task)
            except ValueError as error:
                from .home_actions import ActionError
                if not isinstance(error, ActionError):raise
                trace.mark('action_refused')
                reply = error.message
                self.commit(text, reply)
                await send('result', kind='home', text='Nicht ausgeführt', results=[{'label':error.label,'action':'none','status':'unsupported','value':reply}])
                return reply
            except asyncio.CancelledError:
                results = await task
                self.commit(text, 'Device request attempted; do not automatically repeat.')
                raise
            trace.mark('ha_result', confirmed=sum(r['status'] == 'confirmed' for r in results), count=len(results))
            rows = []
            for target in dict.fromkeys(r['target'] for r in results):
                group = [r for r in results if r['target'] == target]
                rows.append({'label': group[0].get('label') or self.cfg['watch_targets'][target]['label'], 'action': group[0]['action'],
                             'value': group[0].get('value', 'ON' if group[0]['action']=='on' else 'OFF'),
                             'status': group[0]['status'] if len({r['status'] for r in group})==1 else 'unconfirmed'})
            ok = all(r['status'] == 'confirmed' for r in results)
            fa = any('\u0600' <= ch <= '\u06ff' for ch in text)
            en = bool(re.search(r'\b(turn|switch|lights|kitchen|living)\b', text, re.I))
            reply = ('انجام شد' if ok else 'بعضی تغییرات تأیید نشد') if fa else (('Done' if ok else 'Some changes unconfirmed') if en else ('Erledigt' if ok else 'Teilweise unbestätigt'))
            self.commit(text, reply+'; '+ '; '.join(r['label']+': '+r['value']+' '+r['status'] for r in rows))
            await send('result', kind='home', text=reply, results=rows)
            return reply
        reply = reply.strip()
        if not reply:
            raise ValueError('empty reply')
        await send('result', kind='answer', text=reply, results=[])
        try:
            async with self.http.get(self.cfg['tts_url'].rstrip('/') + '/health',
                    timeout=aiohttp.ClientTimeout(total=1)) as response:
                health = await response.json()
                available = response.status == 200 and health.get('status') == 'ok' and health.get('busy') is False
        except (TimeoutError, aiohttp.ClientError, ValueError):
            available = False
        if not available:
            await send('result', kind='answer', text=reply, results=[], voice='unavailable')
            self.commit(text, reply)
            return reply
        speech = Speech(self.http, self.cfg, trace)
        async def tokens():
            yield reply
        try:
            await send('responding')
            if self.cfg.get('watch_tts_streaming'):
                # One continuous MP3/AirPlay session. Tool decisions were already
                # resolved above; device results remain quiet watch-only output.
                await speech.say(reply, 0)
            else:
                await speak_stream(tokens(), speech, self.cfg['max_reply_chars'])
        except asyncio.CancelledError:
            await speech.cancel()
            raise
        except Exception:
            trace.mark('voice_unavailable')
            await send('result', kind='answer', text=reply, results=[], voice='unavailable')
        self.commit(text, reply)
        return reply
