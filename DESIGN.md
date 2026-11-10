# Riptide design

## Who and what
A power user who leaves the client open all day. The main job is scanning a dense table and acting fast:
select, pause, prioritise, remove. Everything else is secondary.

## Palette: night sea
Tokens live in `web/src/styles.css` `:root`. Never hardcode a hex in components.

| token | dark | role |
|---|---|---|
| --sea | #0E1C2A | page background |
| --hull / --hull-2 | #152739 / #1A3046 | panels, hover |
| --rigging / --rigging-2 | #22384E / #2D4862 | borders, dividers |
| --foam | #DCE7EE | primary text |
| --mist / --fog | #9DB1C2 / #6F8699 | secondary text, labels |
| --tide | #4FD1C1 | download, primary action, focus |
| --dawn | #F2B544 | upload, seeding, "on" toggles |
| --flare | #FF5D6C | errors, destructive |

Color carries meaning: teal always means download, amber always means upload or seeding. Do not use them decoratively.
Light theme ("Shallows") redefines the same tokens under `[data-theme="light"]`.

## Type
Instrument Sans Variable for everything, `font-variant-numeric: tabular-nums` globally so numbers do not jitter.
Scale: 11.5 / 12.5 / 13.5 / 16 / 28 px. Sentence case everywhere. No all-caps labels, no monospace for data labels.

## The one bold thing: the tide band
The header is a live area chart of total download (teal) and upload (amber) over the last 5 minutes, with the
current speeds as the largest type on screen. Everything else stays quiet and dense. Do not add a second hero
element, gradient washes, or decorative motion elsewhere.

## Density and layout
```
+----------------------------------------------------------------------+
| tide band: logo | 13.8 MB/s down | 1.2 MB/s up | ~~wave~~ | Add | gear |
+-----------+----------------------------------------------------------+
| Status    | toolbar: Resume Pause Remove | up down | panel | search  |
| Categories| table (virtualised, 30px rows, sortable, multi-select)   |
| Tags      +----------------------------------------------------------+
|           | details: General | Files | Peers | Trackers (resizable)  |
+-----------+----------------------------------------------------------+
| status bar: counts, peers, DHT, free space, port, slow mode, links    |
+----------------------------------------------------------------------+
```
Rows are 30 px. Radius 4 px on controls, 8 px on dialogs and menus. One shadow token, only on floating layers.
Under 900 px the sidebar hides, the table keeps name, progress and down speed, details start closed.

## Progress bars
Water fill: tinted fill plus a 2 px solid edge at the current level. Teal while downloading, amber while
seeding, grey when paused or queued, flare on error. Percentage text centred inside.

## Motion
Only in answer to an action (dialog rise 160 ms) or to show live state (checking dots pulse). Respect
`prefers-reduced-motion`. No entrance animations on load.

## Copy
Plain verbs, sentence case, no filler. Buttons say exactly what happens: "Add 3 torrents", "Remove and delete data".
The toast after an action uses the same verb ("Paused 2 torrents"). Errors say what happened and how to fix it,
never apologise. Empty states invite the next action. No em dashes in UI copy.
