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

// Product Card Template
function createProductCard(product) {
  const discount = product.discount;
  const stockClass = product.in_stock ? 'in-stock' : 'out-of-stock';
  const stockText = product.in_stock ? `В наличии: ${product.stock}` : 'Нет в наличии';

  return `
    <div class="card">
      <div class="card-image">
        <img src="${product.image || '/placeholder.png'}" alt="${product.name}" onerror="this.src='/placeholder.png'">
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

// Initialize App
async function initApp() {
  console.log('App initialized');

  // Check admin key
  if (window.location.pathname.includes('admin')) {
    const adminKey = localStorage.getItem('admin_key');
    if (!adminKey) {
      const key = prompt('Введите ключ администратора:');
      if (key) {
        localStorage.setItem('admin_key', key);
      } else {
        window.location.href = 'index.html';
      }
    }
  }
}

// Show notifications
function showNotification(message, type = 'success') {
  const notification = document.createElement('div');
  notification.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 16px 24px;
    background: ${type === 'success' ? '#34c759' : '#ff3b30'};
    color: white;
    border-radius: 4px;
    font-weight: 600;
    z-index: 9999;
    animation: slideIn 0.3s ease;
  `;
  notification.textContent = message;
  document.body.appendChild(notification);

  setTimeout(() => {
    notification.style.animation = 'slideOut 0.3s ease';
    setTimeout(() => notification.remove(), 300);
  }, 3000);
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
