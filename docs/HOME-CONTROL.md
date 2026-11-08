# Home-control behavior

The public `backend/watch-targets.json` is a fictionalized test fixture. Replace its entity IDs and review every alias/capability before enabling home control. The runnable example starts with `ha_enabled=false`.

| Target | Supported behavior |
| --- | --- |
| Room / named light | On/off; group membership configured explicitly |
| Capable individual light | Brightness percentage, RGB or white temperature, bounded by configuration and actual HA capabilities |
| Example bathroom | On/off only by policy, despite any richer hardware support |
| TV | On/off; fixed configured app; fixed configured input |
| Front door | Lock only, independently gated positive utterance |

Examples from the tested command patterns:

- `Flur an.`
- `Turn the kitchen lights off.`
- `Wohnzimmer und Küche aus, Flur und TV Backlight an.`
- `Stehlampe blau auf 50 Prozent.`
- `Öffne YouTube auf dem Fernseher.` / `Öffne Jellyfin.`
- `Wechsle zum PS5-Eingang.` / `Schalte den Fernseher aus.`
- `Schließe die Haustür ab.` (only when physically closed and ready)

A room request covers all configured room lights. An explicit individual target can override its room action. Zero brightness normalizes to off. A request for unsupported attributes on an on/off-only device is refused; unsupported members of a room are not silently omitted. Conflicting TV power/app/input intents are refused before effects. Only app/input actions auto-wake the TV; TV-off does not.

The model chooses from fixed aliases/actions and configured app/input names. It never supplies arbitrary HA entity IDs, service paths, TV app IDs or URLs. Door unlock/open/unlatch/toggle are not part of the exposed watch action surface. Inverse, negated, conditional, quoted and explanatory door requests are rejected by a separate positive-utterance gate, with synthetic regression coverage.

Confirmation means observed state, not merely an accepted request. Some devices may respond slowly or go unavailable; partial/unknown results are shown honestly. Preflight cannot make a multi-device batch atomic. Once effects start, the system does not fall back to another model and repeat the command.

Speech/model errors and environmental failures remain possible. Physically validate your own device map, prohibited commands, failure behavior and lock use before relying on the prototype.
