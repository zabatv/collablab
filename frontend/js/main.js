// Utility Functions
function formatPrice(price) {
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    minimumFractionDigits: 0
  }).format(price);
}

function getDiscountPercent(oldPrice, newPrice) {
  if (!oldPrice) return null;
  return Math.round((1 - newPrice / oldPrice) * 100);
}

// DOM Helpers
const el = (selector) => document.querySelector(selector);
const elAll = (selector) => document.querySelectorAll(selector);

function show(element) {
  if (element) element.style.display = '';
}

function hide(element) {
  if (element) element.style.display = 'none';
}

function clear(element) {
  if (element) element.innerHTML = '';
}

// Drawn inline: pointing a failed image at another file made the browser
// request a missing placeholder over and over
const NO_IMAGE = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">' +
  '<rect width="240" height="240" fill="#f2f2f2"/>' +
  '<text x="120" y="126" font-family="Arial, sans-serif" font-size="15" fill="#999" ' +
  'text-anchor="middle" letter-spacing="1">НЕТ ФОТО</text></svg>'
);

// Names reach the page from the supplier's file, so nothing goes into the
// markup unescaped
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
}

// The supplier puts the parameters into the name: «Фитинг угловой, 8мм x 8мм,
// T=(0...+60)°C». The first clause says what it is, the rest tells two
// near-identical parts apart — so they get different weight on the card.
function splitName(name) {
  const text = String(name || '').replace(/\xa0/g, ' ').replace(/\s+/g, ' ').trim()
    .replace(/^\([^)]*\)\s*/, '');

  // Запятая внутри числа не разделяет: иначе «РДД-2Р-0,2МПа» превращалось
  // в заголовок «РДД-2Р-0», а остаток уезжал в строку характеристик
  const cut = text.search(/,(?!\d)/);

  if (cut < 10) return { title: text, specs: '' };
  return { title: text.slice(0, cut), specs: text.slice(cut + 1).trim() };
}

function stockLabel(product) {
  return product.in_stock
    ? { className: 'in-stock', text: `В наличии: ${product.stock}` }
    // A part that is ordered in is not an error state
    : { className: 'out-of-stock', text: 'Под заказ' };
}

// Product Card Template — the whole card is the link, so no button repeats it
function createProductCard(product) {
  const { title, specs } = splitName(product.name);
  const stock = stockLabel(product);

  return `
    <a class="card" href="product.html?id=${product.id}">
      <div class="card-image">
        <img src="${escapeHtml(product.image || NO_IMAGE)}" alt="${escapeHtml(title)}"
             loading="lazy" onerror="this.onerror=null; this.src=NO_IMAGE">
        ${product.discount ? `<div class="card-badge">-${product.discount}%</div>` : ''}
        ${product.brand?.logo ? `
          <img class="card-brand" src="${escapeHtml(product.brand.logo)}"
               alt="${escapeHtml(product.brand.name)}" loading="lazy">` : ''}
      </div>
      <div class="card-content">
        <div class="card-sku">${escapeHtml(product.sku || '')}</div>
        <div class="card-title">${escapeHtml(title)}</div>
        <div class="card-specs">${escapeHtml(specs)}</div>
        <div class="card-foot">
          <div class="card-price">
            <span class="card-price-current">${formatPrice(product.price)}</span>
            ${product.old_price ? `<span class="card-price-old">${formatPrice(product.old_price)}</span>` : ''}
          </div>
          <span class="stock ${stock.className}">${stock.text}</span>
        </div>
      </div>
    </a>
  `;
}

/* Список таблицей: наименование из 1С, остаток и цена. Так выбирают, когда
   уже знают, что нужно, и сравнивают десяток похожих позиций по числам, а
   не по фотографии. Кнопки «Купить» нет: заказывают звонком. */
function productTable(products, currentId = null) {
  const rows = products.map(product => {
    const stock = stockLabel(product);
    // Ноль в столбце «Кол-во» читается как «кончилось», а деталь возят
    // под заказ — так и написано
    const quantity = product.in_stock ? `${product.stock} шт.` : 'под заказ';

    // Тот самый товар, на странице которого стоит таблица: ссылка на себя
    // сбивает с толку, поэтому строка просто отмечена
    const here = product.id === currentId;

    return `
      <tr${here ? ' class="ptable-here" aria-current="true"' : ''}>
        <td class="ptable-name">
          ${here
            ? `<span>${escapeHtml(product.name)}</span>`
            : `<a href="product.html?id=${product.id}">${escapeHtml(product.name)}</a>`}
          ${product.sku ? `<span class="ptable-sku">${escapeHtml(product.sku)}</span>` : ''}
        </td>
        <td class="ptable-stock num">
          <span class="stock ${stock.className}">${quantity}</span>
        </td>
        <td class="ptable-price num">${formatPrice(product.price)}</td>
      </tr>`;
  }).join('');

  return `
    <table class="ptable">
      <thead>
        <tr>
          <th>Наименование</th>
          <th class="ptable-stock">Кол-во</th>
          <th class="ptable-price">Цена</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Placeholders hold the grid's shape while the request is in flight
function renderSkeletons(container, count = 8) {
  if (!container) return;
  container.innerHTML = Array.from({ length: count }, () => `
    <div class="skeleton-card">
      <div class="skeleton-image"></div>
      <div class="skeleton-body">
        <div class="skeleton-line" style="width: 35%"></div>
        <div class="skeleton-line" style="width: 90%"></div>
        <div class="skeleton-line" style="width: 60%"></div>
      </div>
    </div>
  `).join('');
}

// The admin page checks its own session on load, so nothing to do here
async function initApp() {}

// Show notifications
function showNotification(message, type = 'success') {
  // Messages share a stack, otherwise several at once land on top of each other
  let stack = document.getElementById('notification-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'notification-stack';
    document.body.appendChild(stack);
  }

  const notification = document.createElement('div');
  notification.className = `toast toast-${type}`;
  notification.textContent = message;
  stack.appendChild(notification);

  // Transitions, not keyframes: messages can arrive faster than one plays out,
  // and a transition retargets from where it is instead of restarting
  requestAnimationFrame(() => notification.classList.add('toast-shown'));

  setTimeout(() => {
    notification.classList.remove('toast-shown');
    setTimeout(() => notification.remove(), 200);
  }, type === 'error' ? 6000 : 3000);
}

let searchClearHandler = null;

// Shared behaviour of the header search box across pages
function initSearchBar({ onSearch, onClear, live = false, delay = 400 }) {
  const input = el('#search-input');
  const bar = el('#search-bar');
  if (!input || !bar) return;

  searchClearHandler = onClear;
  let timer;
  const syncClearButton = () => bar.classList.toggle('has-text', input.value.length > 0);

  input.addEventListener('input', () => {
    syncClearButton();
    if (!live) return;
    // Wait for a pause in typing instead of querying on every keystroke
    clearTimeout(timer);
    timer = setTimeout(onSearch, delay);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      clearTimeout(timer);
      onSearch();
    }
  });

  syncClearButton();
}

function clearSearch() {
  const input = el('#search-input');
  if (!input) return;

  input.value = '';
  el('#search-bar').classList.remove('has-text');
  input.focus();
  if (searchClearHandler) searchClearHandler();
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', initApp);
