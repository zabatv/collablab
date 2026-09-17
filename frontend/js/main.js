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

// Product Card Template
function createProductCard(product) {
  const discount = product.discount;
  const stockClass = product.in_stock ? 'in-stock' : 'out-of-stock';
  const stockText = product.in_stock ? `В наличии: ${product.stock}` : 'Нет в наличии';

  return `
    <div class="card">
      <div class="card-image">
        <img src="${product.image || NO_IMAGE}" alt="" onerror="this.onerror=null; this.src=NO_IMAGE">
        ${discount ? `<div class="card-badge">-${discount}%</div>` : ''}
      </div>
      <div class="card-content">
        <div class="card-category">${product.category?.name || ''}</div>
        <h4 class="card-title">${product.name}</h4>
        <div class="card-price">
          <span class="card-price-current">${formatPrice(product.price)}</span>
          ${product.old_price ? `<span class="card-price-old">${formatPrice(product.old_price)}</span>` : ''}
        </div>
        <div class="card-stock ${stockClass}">${stockText}</div>
        <a href="product.html?id=${product.id}" class="btn btn-primary" style="width: 100%;">ПОДРОБНЕЕ</a>
      </div>
    </div>
  `;
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
    stack.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      left: 20px;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 8px;
      pointer-events: none;
      z-index: 9999;
    `;
    document.body.appendChild(stack);
  }

  const notification = document.createElement('div');
  notification.style.cssText = `
    max-width: min(420px, 100%);
    padding: 14px 20px;
    background: ${type === 'success' ? '#34c759' : '#ff3b30'};
    color: white;
    border-radius: 4px;
    font-weight: 600;
    line-height: 1.35;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
    animation: slideIn 0.3s ease;
  `;
  notification.textContent = message;
  stack.appendChild(notification);

  setTimeout(() => {
    notification.style.animation = 'slideOut 0.3s ease';
    setTimeout(() => notification.remove(), 300);
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

// Add styles for animations
const style = document.createElement('style');
style.textContent = `
  @keyframes slideIn {
    from {
      transform: translateX(400px);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }

  @keyframes slideOut {
    from {
      transform: translateX(0);
      opacity: 1;
    }
    to {
      transform: translateX(400px);
      opacity: 0;
    }
  }
`;
document.head.appendChild(style);

// Initialize on page load
document.addEventListener('DOMContentLoaded', initApp);
