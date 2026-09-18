/* The storage notice.
   What the site actually stores today is worth stating plainly, because it is
   very little: no cookies at all, no address, no identifier. The admin panel
   keeps a session token in its own browser, and the catalogue keeps a count of
   which product pages were opened and what was searched for — numbers with
   nothing attached to them.

   The banner still records a choice, so that the day Метрика or any other
   counter is added, it can be loaded only for visitors who agreed:

       if (consentGiven('analytics')) { …загрузить счётчик… }
*/

(() => {
  const KEY = 'consent';
  const VERSION = 1;

  function stored() {
    try {
      const raw = localStorage.getItem(KEY);
      const value = raw ? JSON.parse(raw) : null;
      return value && value.version === VERSION ? value : null;
    } catch (error) {
      // Private mode, blocked storage: the notice simply shows again
      return null;
    }
  }

  function remember(analytics) {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        version: VERSION, analytics, at: new Date().toISOString(),
      }));
    } catch (error) {
      // Nothing to do — the choice holds for this page either way
    }
  }

  // Read by whatever gets added later; false until someone agrees
  window.consentGiven = kind => {
    const choice = stored();
    return kind === 'analytics' ? Boolean(choice && choice.analytics) : Boolean(choice);
  };

  if (stored()) return;

  const banner = document.createElement('div');
  banner.className = 'consent';
  banner.setAttribute('role', 'dialog');
  banner.setAttribute('aria-label', 'Данные и файлы cookie');
  banner.innerHTML = `
    <div class="consent-text">
      <strong>Сайт не использует файлы cookie.</strong>
      В браузере хранятся только технические данные, нужные для работы страниц,
      а посещаемость считается обезличенно: сколько раз открыли товар и что искали,
      без имени, адреса и идентификаторов.
      <a href="privacy.html">Политика конфиденциальности</a>
    </div>
    <div class="consent-actions">
      <button type="button" class="btn btn-secondary" data-choice="necessary">Только необходимые</button>
      <button type="button" class="btn btn-primary" data-choice="all">Принять</button>
    </div>`;

  banner.addEventListener('click', event => {
    const button = event.target.closest('button[data-choice]');
    if (!button) return;

    remember(button.dataset.choice === 'all');
    banner.classList.remove('is-shown');
    setTimeout(() => banner.remove(), 220);
  });

  const mount = () => {
    document.body.appendChild(banner);
    // Enters from the edge it sits on, never from nothing
    requestAnimationFrame(() => banner.classList.add('is-shown'));
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
