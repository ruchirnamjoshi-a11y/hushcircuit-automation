/* Shared infrastructure for canvas-animation "explainer" pieces (see
 * pipeline/canvas_video.py). Loaded via a plain <script src="..."> from each
 * piece's HTML file -- these pieces are rendered locally by Playwright, not
 * published as self-contained Artifacts, so there's no CSP constraint
 * forcing everything into one file the way there is for claude.ai Artifacts.
 *
 * Each piece still hand-authors its own visual logic (draw function) -- this
 * library only covers the boilerplate that's identical across every piece:
 * easing curves, canvas/DPR setup, the deterministic render-mode seek hook,
 * and the caption-line sync pattern. Mirrors how pipeline/textcard.py's
 * zoompan engine is shared across story tracks while each story's content
 * stays hand-written.
 */
window.CanvasPiece = (function () {
  'use strict';

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function easeOutCubic(x) { return 1 - Math.pow(1 - x, 3); }
  function easeInOutSine(x) { return -(Math.cos(Math.PI * x) - 1) / 2; }
  function easeOutBack(x) {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
  }

  function hexToRgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function lerpColor(a, b, t) {
    const c1 = hexToRgb(a), c2 = hexToRgb(b);
    const r = Math.round(c1[0] + (c2[0] - c1[0]) * t);
    const g = Math.round(c1[1] + (c2[1] - c1[1]) * t);
    const bch = Math.round(c1[2] + (c2[2] - c1[2]) * t);
    return `rgb(${r},${g},${bch})`;
  }

  // Canvas setup with devicePixelRatio handling, resize-aware.
  function setupCanvas(canvas, stage) {
    let W = 0, H = 0;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const ctx = canvas.getContext('2d');
    function resize() {
      W = stage.clientWidth;
      H = stage.clientHeight;
      canvas.width = W * DPR;
      canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    }
    window.addEventListener('resize', resize);
    resize();
    return {
      ctx,
      get W() { return W; },
      get H() { return H; },
      resize,
    };
  }

  // Caption-line sync: lines = [{t, hold, text}], all in seconds.
  // Returns updateStory(t) to call from the draw loop.
  function createCaptionSync(el, lines) {
    let shownIdx = -1;
    return function updateStory(t) {
      let idx = -1;
      for (let i = 0; i < lines.length; i++) {
        const s = lines[i];
        if (t >= s.t && t < s.t + s.hold) idx = i;
      }
      if (idx !== shownIdx) {
        shownIdx = idx;
        if (idx === -1) {
          el.classList.remove('show');
        } else {
          el.textContent = lines[idx].text;
          el.classList.add('show');
        }
      }
    };
  }

  // Wires up the three playback modes every piece needs:
  //  - live viewing (real requestAnimationFrame loop, wall-clock time)
  //  - prefers-reduced-motion (draws one static settled frame, no motion)
  //  - render mode (?render=1): does nothing on its own -- pipeline.
  //    canvas_video's Playwright renderer drives window.__seek(t) with a
  //    deterministic clock instead, so frame capture is glitch-free
  //    regardless of how fast/slow the render machine is.
  //
  // opts: { draw(t), updateStory(t)?, total, reducedMotionAt? }
  function createTimeline(opts) {
    const { draw, updateStory, total } = opts;
    const reducedMotionAt = opts.reducedMotionAt != null ? opts.reducedMotionAt : total;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const renderMode = new URLSearchParams(location.search).has('render');

    function tick(t) {
      draw(Math.min(t, total + 2));
      if (updateStory) updateStory(t);
    }

    window.__seek = tick;

    let start = null;
    function frame(ts) {
      if (start === null) start = ts;
      tick((ts - start) / 1000);
      requestAnimationFrame(frame);
    }

    if (renderMode) {
      document.documentElement.classList.add('render-mode');
      tick(0);
    } else if (reduceMotion) {
      tick(reducedMotionAt);
    } else {
      requestAnimationFrame(frame);
    }

    return { reset: () => { start = null; } };
  }

  return {
    clamp01, easeOutCubic, easeInOutSine, easeOutBack,
    hexToRgb, lerpColor,
    setupCanvas, createCaptionSync, createTimeline,
  };
})();
