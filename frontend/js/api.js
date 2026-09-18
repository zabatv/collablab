// API Helper Functions
// Opened from a developer's own machine the backend is next door on port 5000;
// everywhere else it is the server the site is published on
const API_BASE = ['localhost', '127.0.0.1'].includes(window.location.hostname)
  ? `${window.location.protocol}//${window.location.hostname}:5000/api`
  : 'http://45.143.93.41:5000/api';

// The browser holds a session token, never the password
const TOKEN_KEY = 'admin_token';

function adminToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

function authHeader() {
  return { 'Authorization': `Bearer ${adminToken()}` };
}

const AuthAPI = {
  login: async (username, password) => {
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });

    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.error || `Сервер ответил ошибкой ${response.status}`);
    }

    localStorage.setItem(TOKEN_KEY, result.token);
    return result;
  },

  logout: async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: authHeader() });
    } finally {
      localStorage.removeItem(TOKEN_KEY);
    }
  },

  isSignedIn: async () => {
    if (!adminToken()) return false;
    try {
      const response = await fetch(`${API_BASE}/auth/check`, { headers: authHeader() });
      if (!response.ok) localStorage.removeItem(TOKEN_KEY);
      return response.ok;
    } catch (error) {
      // A server that cannot be reached is not proof the token went bad
      return true;
    }
  }
};

// XHR rather than fetch: only XHR reports how much of the file has gone out
function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('Authorization', `Bearer ${adminToken()}`);

    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      let result = {};
      try {
        result = JSON.parse(xhr.responseText);
      } catch (error) {
        // keep result empty, the status code below carries the message
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(result);
      } else if (xhr.status === 404) {
        reject(new Error('Сервер не знает этот запрос — на нём старая версия backend'));
      } else if (xhr.status === 413) {
        reject(new Error('Файл слишком большой для сервера'));
      } else {
        reject(new Error(result.error || `Сервер ответил ошибкой ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('Нет связи с сервером'));
    xhr.ontimeout = () => reject(new Error('Сервер не ответил вовремя'));

    xhr.send(formData);
  });
}

async function apiCall(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  };

  try {
    const response = await fetch(url, {
      method: options.method || 'GET',
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status}`);
    }

    // Handle empty response
    if (response.status === 204) {
      return null;
    }

    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
}

// Product API
const ProductAPI = {
  getProducts: (filters = {}) => {
    const params = new URLSearchParams();
    if (filters.category_id) params.append('category_id', filters.category_id);
    if (filters.brand_id) params.append('brand_id', filters.brand_id);
    if (filters.search) params.append('search', filters.search);
    if (filters.sort) params.append('sort', filters.sort);
    if (filters.page) params.append('page', filters.page);
    if (filters.per_page) params.append('per_page', filters.per_page);

    return apiCall(`/products?${params.toString()}`);
  },

  getProduct: (id) => apiCall(`/products/${id}`),

  search: (query) => apiCall(`/products/search?q=${encodeURIComponent(query)}`),

  getCategories: () => apiCall('/categories'),

  getBrands: () => apiCall('/brands')
};

// Admin API
const AdminAPI = {
  getProducts: (page = 1) => {
    const headers = authHeader();
    return apiCall(`/admin/products?page=${page}`, { headers });
  },

  createProduct: (data) => {
    const headers = authHeader();
    return apiCall('/admin/products', {
      method: 'POST',
      headers,
      body: data
    });
  },

  updateProduct: (id, data) => {
    const headers = authHeader();
    return apiCall(`/admin/products/${id}`, {
      method: 'PUT',
      headers,
      body: data
    });
  },

  deleteProduct: (id) => {
    const headers = authHeader();
    return apiCall(`/admin/products/${id}`, {
      method: 'DELETE',
      headers
    });
  },

  uploadImage: (productId, file, onProgress) => {
    const formData = new FormData();
    formData.append('image', file);

    return uploadWithProgress(
      `${API_BASE}/admin/products/${productId}/upload-image`, formData, onProgress);
  },

  uploadVideoFile: (productId, file, title = '', onProgress) => {
    const formData = new FormData();
    formData.append('video', file);
    formData.append('title', title);

    return uploadWithProgress(
      `${API_BASE}/admin/products/${productId}/upload-video-file`, formData, onProgress);
  },

  addVideo: (productId, data) => {
    const headers = authHeader();
    return apiCall(`/admin/products/${productId}/upload-video`, {
      method: 'POST',
      headers,
      body: data
    });
  },

  deleteVideo: (productId, videoId) => {
    const headers = authHeader();
    return apiCall(`/admin/products/${productId}/videos/${videoId}`, {
      method: 'DELETE',
      headers
    });
  },

  getCategories: () => {
    const headers = authHeader();
    return apiCall('/admin/categories', { headers });
  },

  createCategory: (data) => {
    const headers = authHeader();
    return apiCall('/admin/categories', {
      method: 'POST',
      headers,
      body: data
    });
  },

  seoCatalog: () => apiCall('/admin/seo/catalog', { headers: authHeader() }),

  seoTraffic: (days = 30) => apiCall(`/admin/seo/traffic?days=${days}`, { headers: authHeader() }),

  getBrands: () => {
    const headers = authHeader();
    return apiCall('/admin/brands', { headers });
  },

  createBrand: (data) => {
    const headers = authHeader();
    return apiCall('/admin/brands', {
      method: 'POST',
      headers,
      body: data
    });
  }
};
