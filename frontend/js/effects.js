/* Motion, ported from React Bits (github.com/DavidHDev/react-bits, MIT +
   Commons Clause) to plain JavaScript — the site has no React and no build
   step. Four effects, kept restrained: a catalogue of 300 near-identical parts
   is scanned, and anything that moves while someone is scanning gets in the way.

   CountUp        → the numbers under the hero
   SpotlightCard  → the light that follows the cursor across a product card
   ClickSpark     → the spark when a button is pressed
   AnimatedContent → a category block rising into place as it is scrolled to

   Everything below stands down under prefers-reduced-motion. */

(() => {
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* ---------- CountUp ---------- */

  // React Bits drives the number with a spring rather than a duration, so it
  // slows into its final value instead of stopping dead. Critically damped
  // (damping = 2√stiffness): no overshoot, and no long crawl at the end either.
  const STIFFNESS = 170;
  const DAMPING = 2 * Math.sqrt(STIFFNESS);
  const GIVE_UP = 2000;

  function springTo({ from, to, onUpdate }) {
    let value = from;
    let velocity = 0;
    let last = null;
    let elapsed = 0;

    const step = now => {
      if (last === null) last = now;
      const dt = Math.min((now - last) / 1000, 1 / 30);
      elapsed += now - last;
      last = now;

      velocity += (-STIFFNESS * (value - to) - DAMPING * velocity) * dt;
      value += velocity * dt;

      // Settle when the remainder can no longer change the rounded number,
      // and never let a slow tail hold the final value back
      if (Math.abs(to - value) < 0.4 || elapsed > GIVE_UP) {
        onUpdate(to);
        return;
      }

      onUpdate(value);
      requestAnimationFrame(step);
    };

    requestAnimationFrame(step);
  }

  function countUp(element) {
    const to = Number(element.dataset.countTo);
    if (!Number.isFinite(to)) return;

    const format = value => Math.round(value).toLocaleString('ru-RU');

    if (still) {
      element.textContent = format(to);
      return;
    }

    element.textContent = format(0);
    springTo({ from: 0, to, onUpdate: value => (element.textContent = format(value)) });
  }

  /* ---------- AnimatedContent + CountUp trigger ---------- */

  // React Bits uses GSAP ScrollTrigger; an IntersectionObserver and a CSS
  // transition do the same job here without pulling in a library
  const seen = new WeakSet();
  const observer = new IntersectionObserver(entries => {
    // Several blocks usually come into view together — on the first screen,
    // all of them at once. Revealed at the same instant they read as one
    // flat jump, so each one waits a little longer than the one above it.
    let inBatch = 0;

    entries.forEach(entry => {
      if (!entry.isIntersecting || seen.has(entry.target)) return;
      seen.add(entry.target);
      observer.unobserve(entry.target);

      if (entry.target.dataset.countTo !== undefined) {
        countUp(entry.target);
        return;
      }

      // Four deep the wait is already long enough to feel like a delay
      const step = Math.min(inBatch, 4) * 90;
      inBatch += 1;

      if (step) {
        entry.target.style.transitionDelay = `${step}ms`;
        // Custom properties inherit, so whatever is inside the block —
        // the tiles of a catalogue section — waits for the block itself
        // before starting its own stagger
        entry.target.style.setProperty('--reveal-delay', `${step}ms`);
      }

      entry.target.classList.add('is-revealed');

      // Left in place they would also slow down anything the block
      // transitions later, such as folding it shut by hand
      setTimeout(() => {
        entry.target.style.transitionDelay = '';
        entry.target.style.removeProperty('--reveal-delay');
      }, step + 900);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });

  function watch(selector) {
    document.querySelectorAll(selector).forEach(element => {
      if (!seen.has(element)) observer.observe(element);
    });
  }

  // Sections and numbers are rendered after a request, so they are picked up
  // as they appear rather than once at load
  function scan() {
    watch('[data-count-to]');
    if (still) {
      document.querySelectorAll('.reveal').forEach(el => el.classList.add('is-revealed'));
      return;
    }
    watch('.reveal');
  }

  /* ---------- SpotlightCard ---------- */

  // One listener on the document instead of one per card: the catalogue can
  // hold hundreds of them
  function trackSpotlight(event) {
    const card = event.target.closest('.card');
    if (!card) return;

    const rect = card.getBoundingClientRect();
    card.style.setProperty('--mouse-x', `${event.clientX - rect.left}px`);
    card.style.setProperty('--mouse-y', `${event.clientY - rect.top}px`);
  }

  /* ---------- ClickSpark ---------- */

  const SPARKS = 8;
  const SPARK_RADIUS = 16;
  const SPARK_SIZE = 9;
  const SPARK_DURATION = 380;

  let canvas = null;
  let context = null;
  let sparks = [];
  let frame = null;

  function sizeCanvas() {
    const ratio = window.devicePixelRatio || 1;
    canvas.width = window.innerWidth * ratio;
    canvas.height = window.innerHeight * ratio;
    canvas.style.width = `${window.innerWidth}px`;
    canvas.style.height = `${window.innerHeight}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function drawSparks(timestamp) {
    context.clearRect(0, 0, window.innerWidth, window.innerHeight);

    sparks = sparks.filter(spark => {
      const elapsed = timestamp - spark.start;
      if (elapsed >= SPARK_DURATION) return false;

      const progress = elapsed / SPARK_DURATION;
      const eased = progress * (2 - progress);          // ease-out, as in the original
      const distance = eased * SPARK_RADIUS;
      const length = SPARK_SIZE * (1 - eased);

      context.strokeStyle = spark.color;
      context.globalAlpha = 1 - eased;
      context.lineWidth = 1.6;
      context.lineCap = 'round';
      context.beginPath();
      context.moveTo(spark.x + distance * Math.cos(spark.angle),
                     spark.y + distance * Math.sin(spark.angle));
      context.lineTo(spark.x + (distance + length) * Math.cos(spark.angle),
                     spark.y + (distance + length) * Math.sin(spark.angle));
      context.stroke();

      return true;
    });

    context.globalAlpha = 1;
    frame = sparks.length ? requestAnimationFrame(drawSparks) : null;
  }

  function spark(event) {
    // Only where something was actually pressed — not on every click on the page
    const target = event.target.closest(
      '.btn, .search-submit, .pagination button, .login-submit');
    if (!target) return;

    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.className = 'spark-canvas';
      document.body.appendChild(canvas);
      context = canvas.getContext('2d');
      sizeCanvas();
      window.addEventListener('resize', sizeCanvas);
    }

    // The spark takes the colour of what it came from, so it reads on a dark
    // button and on a white one alike
    const background = getComputedStyle(target).backgroundColor;
    const dark = isDark(background);
    const color = dark ? '#ffffff' : getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim() || '#0d5ef4';

    const now = performance.now();
    for (let i = 0; i < SPARKS; i++) {
      sparks.push({
        x: event.clientX,
        y: event.clientY,
        angle: (2 * Math.PI * i) / SPARKS,
        start: now,
        color
      });
    }

    if (!frame) frame = requestAnimationFrame(drawSparks);
  }

  function isDark(color) {
    const parts = color.match(/[\d.]+/g);
    if (!parts || parts.length < 3) return false;
    const [r, g, b, alpha = 1] = parts.map(Number);
    if (alpha < 0.5) return false;
    return (r * 299 + g * 587 + b * 114) / 1000 < 140;
  }

  /* ---------- wiring ---------- */

  function start() {
    scan();
    // Cards and sections arrive with the data, not with the document
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });

    if (fine) document.addEventListener('pointermove', trackSpotlight, { passive: true });
    if (!still) document.addEventListener('click', spark);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
