// API Helper Functions
const API_BASE = 'http://45.143.93.41:5000/api';

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
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall(`/admin/products?page=${page}`, { headers });
  },

  createProduct: (data) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall('/admin/products', {
      method: 'POST',
      headers,
      body: data
    });
  },

  updateProduct: (id, data) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall(`/admin/products/${id}`, {
      method: 'PUT',
      headers,
      body: data
    });
  },

  deleteProduct: (id) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall(`/admin/products/${id}`, {
      method: 'DELETE',
      headers
    });
  },

  uploadImage: (productId, file) => {
    const formData = new FormData();
    formData.append('image', file);

    return fetch(`${API_BASE}/admin/products/${productId}/upload-image`, {
      method: 'POST',
      headers: { 'X-Admin-Key': localStorage.getItem('admin_key') },
      body: formData
    }).then(async res => {
      const result = await res.json();
      if (!res.ok) throw new Error(result.error);
      return result;
    });
  },

  uploadVideoFile: (productId, file, title = '') => {
    const formData = new FormData();
    formData.append('video', file);
    formData.append('title', title);

    return fetch(`${API_BASE}/admin/products/${productId}/upload-video-file`, {
      method: 'POST',
      headers: { 'X-Admin-Key': localStorage.getItem('admin_key') },
      body: formData
    }).then(async res => {
      const result = await res.json();
      if (!res.ok) throw new Error(result.error);
      return result;
    });
  },

  addVideo: (productId, data) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall(`/admin/products/${productId}/upload-video`, {
      method: 'POST',
      headers,
      body: data
    });
  },

  deleteVideo: (productId, videoId) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall(`/admin/products/${productId}/videos/${videoId}`, {
      method: 'DELETE',
      headers
    });
  },

  getCategories: () => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall('/admin/categories', { headers });
  },

  createCategory: (data) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall('/admin/categories', {
      method: 'POST',
      headers,
      body: data
    });
  },

  getBrands: () => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall('/admin/brands', { headers });
  },

  createBrand: (data) => {
    const headers = { 'X-Admin-Key': localStorage.getItem('admin_key') };
    return apiCall('/admin/brands', {
      method: 'POST',
      headers,
      body: data
    });
  }
};
