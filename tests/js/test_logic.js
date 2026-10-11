// Unit tests for extension/sharel@hawkins.app/logic.js, run with `gjs -m` (tests/test_extension.py).
import * as L from '../../extension/sharel@hawkins.app/logic.js';

let failures = 0;
let count = 0;

function eq(actual, expected, what) {
    count++;
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failures++;
        print(`FAIL ${what}: expected ${e}, got ${a}`);
    }
}

// rects
eq(L.rectContains([0, 0, 10, 10], 9, 9), true, 'contains inner');
eq(L.rectContains([0, 0, 10, 10], 10, 5), false, 'contains right edge exclusive');
eq(L.rectIntersects([0, 0, 10, 10], [9, 9, 5, 5]), true, 'intersects');
eq(L.rectIntersects([0, 0, 10, 10], [10, 0, 5, 5]), false, 'touching does not intersect');
eq(L.unionRect([[0, 0, 10, 10], [20, 5, 5, 20]]), [0, 0, 25, 25], 'union');
eq(L.unionRect([]), [0, 0, 0, 0], 'union empty');
eq(L.scaleRect([10, 20, 30, 40], 1.5), [15, 30, 45, 60], 'scale');
eq(L.frameCropInBuffer([610, 296, 700, 520], [585, 271, 750, 570], 2), [50, 50, 1400, 1040], 'frame crop');

// window at point: topmost by stacking, skipping minimized / other workspaces / excluded
const wins = [
    {id: 1, frame: [0, 0, 500, 500], stacking: 1, minimized: false, on_current_workspace: true},
    {id: 2, frame: [100, 100, 200, 200], stacking: 3, minimized: false, on_current_workspace: true},
    {id: 3, frame: [100, 100, 200, 200], stacking: 5, minimized: true, on_current_workspace: true},
    {id: 4, frame: [100, 100, 200, 200], stacking: 6, minimized: false, on_current_workspace: false},
];
eq(L.windowAtPoint(wins, 150, 150)?.id, 2, 'topmost visible');
eq(L.windowAtPoint(wins, 50, 50)?.id, 1, 'only window there');
eq(L.windowAtPoint(wins, 150, 150, [2])?.id, 1, 'excluded id');
eq(L.windowAtPoint(wins, 900, 900), null, 'nothing');

const mons = [{x: 0, y: 0, width: 1920, height: 1080, primary: true},
    {x: 1920, y: 0, width: 1600, height: 900, primary: false}];
eq(L.monitorAt(mons, 2000, 10).x, 1920, 'second monitor');
eq(L.monitorAt(mons, 5000, 5000).x, 0, 'outside -> primary');

// styles
const hs = L.highlighterStyle('{"size": 1000, "shape": "square"}');
eq(hs.size, 400, 'highlighter size clamped');
eq(L.highlighterCss(hs, false).css.includes('border-radius: 0px'), true, 'square shape');
eq(L.highlighterCss(L.highlighterStyle(''), true).size, 48, 'click scale 0.8 of 60');
eq(L.highlighterStyle('not json').size, 60, 'bad json -> defaults');
eq(L.borderStyle('{"width": 0}').width, 2, 'border width default on 0');
eq(L.outsideBorderRect([100, 100, 50, 50], 3), [97, 97, 56, 56], 'border outside');

// accelerators
eq(L.normalizeAccelerator('<Ctrl><shift>print'), L.normalizeAccelerator('<Shift><Control>Print'),
    'normalize order/case/aliases');
eq(L.normalizeAccelerator('<Super>a') === L.normalizeAccelerator('<Meta>A'), true, 'super alias');
const reg = new L.AcceleratorRegistry();
const h1 = reg.add('<Control>Print', 'RectangleRegion');
const h2 = reg.add('Print', 'PrintScreen');
eq([h1, h2], [1, 2], 'handles increment');
eq(reg.pending().length, 2, 'pending before grab');
reg.attempted(h1, 77, 3);
eq(reg.get(h1).state, 'grabbed', 'grabbed');
eq(reg.byAction(77).name, 'RectangleRegion', 'by action');
reg.attempted(h2, 0, 2);
eq(reg.get(h2).state, 'pending', 'still pending after 1 failure');
reg.attempted(h2, 0, 2);
eq(reg.get(h2).state, 'failed', 'failed after max attempts');
eq(L.findDuplicate(reg, '<ctrl>print')?.handle, h1, 'duplicate found');
eq(reg.remove(h1).name, 'RectangleRegion', 'remove');
eq(reg.byAction(77), null, 'gone');
eq(reg.clear().length, 1, 'clear returns rest');

eq(L.borderlessMethod('x11'), 'motif-hints', 'x11 borderless');
eq(L.borderlessMethod('wayland'), 'fullscreen', 'wayland borderless');

print(`${count - failures}/${count} passed`);
if (failures)
    imports.system.exit(1);
