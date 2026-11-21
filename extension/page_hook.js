/*
 * Runs in the page's own JavaScript world (MAIN), before the site's scripts.
 *
 * Some web apps open the file picker from an <input type="file"> that was never
 * added to the page. Events from such a detached element never reach `window`,
 * so content.js could not hold the upload. This shim quietly attaches a detached
 * file input (hidden) right before its picker opens. Nothing else is changed.
 */
(() => {
  'use strict';
  const attach = (input) => {
    if (input.type === 'file' && !input.isConnected) {
      input.style.display = 'none';
      (document.body || document.documentElement).appendChild(input);
    }
  };
  const proto = HTMLInputElement.prototype;
  const originalClick = proto.click;
  proto.click = function click(...args) { attach(this); return originalClick.apply(this, args); };
  if (typeof proto.showPicker === 'function') {
    const originalShowPicker = proto.showPicker;
    proto.showPicker = function showPicker(...args) { attach(this); return originalShowPicker.apply(this, args); };
  }
})();
