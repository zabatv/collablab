// Где наш API.
//
// На боевом сайте — тот же домен, что и страница: nginx отдаёт /api тому же
// Flask. Иначе никак: страница по https не может ходить на http-адрес, браузер
// такие запросы блокирует, а сертификат на голый IP с портом не выпишешь.
//
// На машине разработчика backend стоит рядом, на порту 5000.
// js/config.js может задать адрес вручную — apiBase.
const API_BASE = (() => {
  const configured = (window.SHOP_CONFIG || {}).apiBase;
  if (configured) return configured.replace(/\/+$/, '');

  const local = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  if (local && window.location.port !== '5000') {
    return `${window.location.protocol}//${window.location.hostname}:5000/api`;
  }

  return `${window.location.origin}/api`;
})();

// Which backend serves the site. js/config.js holds the switch; the pages
// below never ask, they call ProductAPI and AdminAPI as they always did.
const ON_NODE = (window.SHOP_CONFIG || {}).backend === 'node';

// The browser holds a session token, never the password
const TOKEN_KEY = 'admin_token';

function adminToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

function authHeader() {
  // Their admin server speaks HTTP Basic, ours a session token
  return ON_NODE ? NodeAPI.basicHeader() : { 'Authorization': `Bearer ${adminToken()}` };
}

const AuthAPI = {
  login: async (username, password) => {
    // У их админки нет входа как такового: логин и пароль едут в каждом
    // запросе. Значит и проверить их можно только запросом.
    if (ON_NODE) {
      NodeAPI.rememberCredentials(username, password);
      try {
        await NodeAPI.checkCredentials();
      } catch (error) {
        NodeAPI.forgetCredentials();
        throw new Error(error.status === 401
          ? 'Неверный логин или пароль' : error.message);
      }
      return { user: { username } };
    }

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
    if (ON_NODE) {
      NodeAPI.forgetCredentials();
      return;
    }

    try {
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: authHeader() });
    } finally {
      localStorage.removeItem(TOKEN_KEY);
    }
  },

  isSignedIn: async () => {
    if (ON_NODE) {
      if (!NodeAPI.basicHeader().Authorization) return false;
      try {
        return await NodeAPI.checkCredentials();
      } catch (error) {
        if (error.status === 401) NodeAPI.forgetCredentials();
        // Недоступный сервер не значит, что пароль перестал подходить
        return error.status !== 401;
      }
    }

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
    // Заголовок авторизации зависит от бэкенда: у нас токен, у них Basic
    Object.entries(authHeader()).forEach(([name, value]) =>
      xhr.setRequestHeader(name, value));

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
        const error = new Error(result.error || `Сервер ответил ошибкой ${xhr.status}`);
        error.status = xhr.status;
        error.code = result.code;
        error.existing = result.existing;
        reject(error);
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
      // The server explains itself — «Сначала удалите или перенесите
      // подкатегории», «В категории 37 товаров». Throwing away that text
      // and showing the bare status code left the admin staring at
      // «API Error» with no idea what to do about it.
      const details = await response.json().catch(() => null);
      throw new Error(details?.error || `Сервер ответил ошибкой ${response.status}`);
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
    if (ON_NODE) return NodeAPI.products(filters);

    const params = new URLSearchParams();
    if (filters.category_id) params.append('category_id', filters.category_id);
    if (filters.brand_id) params.append('brand_id', filters.brand_id);
    if (filters.search) params.append('search', filters.search);
    if (filters.sort) params.append('sort', filters.sort);
    if (filters.page) params.append('page', filters.page);
    if (filters.per_page) params.append('per_page', filters.per_page);

    return apiCall(`/products?${params.toString()}`);
  },

  getProduct: (id) => ON_NODE ? NodeAPI.product(id) : apiCall(`/products/${id}`),

  // The same part in its other sizes, from the same subcategory
  getVariants: (id) =>
    ON_NODE ? NodeAPI.nothing() : apiCall(`/products/${id}/variants`),

  search: (query) =>
    ON_NODE
      ? NodeAPI.products({ search: query }).then(result => result.products)
      : apiCall(`/products/search?q=${encodeURIComponent(query)}`),

  // The tree: every section with its subcategories nested inside
  getCategories: () => ON_NODE ? NodeAPI.categoryTree() : apiCall('/categories'),

  // One flat list, when a screen only needs the names
  getCategoriesFlat: () =>
    ON_NODE ? NodeAPI.categoriesFlat() : apiCall('/categories?flat=1'),

  // A category with its children and the path back to the root
  getCategory: (id) => ON_NODE ? NodeAPI.category(id) : apiCall(`/categories/${id}`),

  getBrands: () => ON_NODE ? NodeAPI.brands() : apiCall('/brands'),

  // The slides on the home page
  getBanners: () => ON_NODE ? NodeAPI.banners() : apiCall('/banners')
};

// Admin API
const AdminAPI = {
  getProducts: (page = 1) => {
    if (ON_NODE) return NodeAPI.adminProducts(page);

    const headers = authHeader();
    return apiCall(`/admin/products?page=${page}`, { headers });
  },

  createProduct: (data) => {
    if (ON_NODE) return NodeAPI.createProduct(data);

    const headers = authHeader();
    return apiCall('/admin/products', {
      method: 'POST',
      headers,
      body: data
    });
  },

  updateProduct: (id, data) => {
    if (ON_NODE) return NodeAPI.updateProduct(id, data);

    const headers = authHeader();
    return apiCall(`/admin/products/${id}`, {
      method: 'PUT',
      headers,
      body: data
    });
  },

  deleteProduct: (id) => {
    // Удаления у них нет, и это не упущение: товар живёт в 1С. Снятый с
    // витрины товар с неё пропадает, а в админке остаётся.
    if (ON_NODE) {
      return Promise.reject(new Error(
        'Этот бэкенд не умеет удалять товары: они приходят из 1С. ' +
        'Снимите галочку «Показывать на витрине» — товар исчезнет с сайта.'));
    }

    const headers = authHeader();
    return apiCall(`/admin/products/${id}`, {
      method: 'DELETE',
      headers
    });
  },

  uploadImage: (productId, file, onProgress) => {
    const formData = new FormData();
    formData.append(ON_NODE ? 'files' : 'image', file);

    return uploadWithProgress(ON_NODE
      ? `${NodeAPI.conf().admin}/products/${productId}/media`
      : `${API_BASE}/admin/products/${productId}/upload-image`, formData, onProgress);
  },

  uploadVideoFile: (productId, file, title = '', onProgress) => {
    const formData = new FormData();
    // У них фото и видео идут одной дорогой, тип определяется по файлу
    formData.append(ON_NODE ? 'files' : 'video', file);
    if (!ON_NODE) formData.append('title', title);

    return uploadWithProgress(ON_NODE
      ? `${NodeAPI.conf().admin}/products/${productId}/media`
      : `${API_BASE}/admin/products/${productId}/upload-video-file`,
      formData, onProgress);
  },

  addVideo: (productId, data) => {
    // Они хранят только загруженные файлы, ссылки на YouTube им некуда деть
    if (ON_NODE) return NodeAPI.missing('Видео по ссылке');

    const headers = authHeader();
    return apiCall(`/admin/products/${productId}/upload-video`, {
      method: 'POST',
      headers,
      body: data
    });
  },

  deleteVideo: (productId, videoId) => {
    if (ON_NODE) return NodeAPI.deleteMedia(productId, videoId);

    const headers = authHeader();
    return apiCall(`/admin/products/${productId}/videos/${videoId}`, {
      method: 'DELETE',
      headers
    });
  },

  getCategories: () => {
    if (ON_NODE) return NodeAPI.adminCategoryTree();

    const headers = authHeader();
    return apiCall('/admin/categories', { headers });
  },

  createCategory: (data) => {
    if (ON_NODE) return NodeAPI.missing('Создания категорий');

    const headers = authHeader();
    return apiCall('/admin/categories', {
      method: 'POST',
      headers,
      body: data
    });
  },

  // Rename a category, or move it under another one
  updateCategory: (id, data) => {
    if (ON_NODE) return NodeAPI.missing('Изменения категорий');

    const headers = authHeader();
    return apiCall(`/admin/categories/${id}`, {
      method: 'PUT',
      headers,
      body: data
    });
  },

  deleteCategory: (id) => {
    if (ON_NODE) return NodeAPI.missing('Удаления категорий');

    const headers = authHeader();
    return apiCall(`/admin/categories/${id}`, {
      method: 'DELETE',
      headers
    });
  },

  /* Когда каталог последний раз менялся. Их бэкенд не сообщает, когда
     отработала синхронизация, — считается по времени правки товаров.
     На нашем бэкенде 1С нет вовсе, поэтому и сведений нет. */
  getLastChange: () => ON_NODE ? NodeAPI.lastChange() : Promise.resolve(null),
  get1cLinked: () => ON_NODE ? NodeAPI.linkedTo1c() : Promise.resolve(null),

  /* Документация к товару: паспорт, чертёж, каталог производителя.
     Их медиа принимает только картинки и видео — файлы лежат в нашем
     сервисе, по артикулу. */
  getDocuments: (article) => ON_NODE ? NodeAPI.productDocs(article) : Promise.resolve([]),

  uploadDocument: (article, file, title) =>
    ON_NODE ? NodeAPI.uploadProductDoc(article, file, title)
            : NodeAPI.missing('Документации к товару'),

  renameDocument: (id, title) =>
    ON_NODE ? NodeAPI.renameProductDoc(id, title)
            : NodeAPI.missing('Документации к товару'),

  deleteDocument: (id) =>
    ON_NODE ? NodeAPI.deleteProductDoc(id)
            : NodeAPI.missing('Документации к товару'),

  /* Характеристики товара. На их бэкенде полей под них нет — таблицу
     хранит наш сервис по артикулу, а в теле товара они просто игнорируются.
     Пустой список возвращает позицию к разбору наименования из 1С. */
  setSpecifications: (article, rows) => {
    if (ON_NODE) return NodeAPI.saveProductSpecs(article, rows);
    return Promise.resolve(null);   // на нашем бэкенде они едут вместе с товаром
  },

  // Текст под заголовком раздела. У их бэкенда такого поля нет, поэтому
  // на нём описание хранит наш сервис, а на нашем — сама категория.
  setCategoryDescription: (id, description) => {
    if (ON_NODE) return NodeAPI.saveCategoryText(id, description);

    const headers = authHeader();
    return apiCall(`/admin/categories/${id}`, {
      method: 'PUT',
      headers,
      body: { description }
    });
  },

  // The picture on a category's tile, instead of one borrowed from a product
  uploadCategoryIcon: (id, file, onProgress) => {
    if (ON_NODE) return NodeAPI.missing('Картинок категорий');

    const formData = new FormData();
    formData.append('image', file);

    return uploadWithProgress(
      `${API_BASE}/admin/categories/${id}/upload-icon`, formData, onProgress);
  },

  clearCategoryIcon: (id) => {
    if (ON_NODE) return NodeAPI.missing('Картинок категорий');

    const headers = authHeader();
    return apiCall(`/admin/categories/${id}/upload-icon`, {
      method: 'DELETE',
      headers
    });
  },

  // ----- banners -----

  // Списки пустые, а не с ошибкой: админка открывается и работает в той
  // части, которую их бэкенд умеет. Ошибку скажет попытка что-то записать.
  getBanners: () =>
    ON_NODE ? NodeAPI.adminBanners()
            : apiCall('/admin/banners', { headers: authHeader() }),

  createBanner: (file, fields = {}, onProgress) => {
    const formData = new FormData();
    formData.append('image', file);
    Object.entries(fields).forEach(([name, value]) =>
      formData.append(name, value ?? ''));

    return uploadWithProgress(ON_NODE
      ? `${NodeAPI.siteBase()}/admin/banners`
      : `${API_BASE}/admin/banners`, formData, onProgress);
  },

  updateBanner: (id, data) => {
    if (ON_NODE) return NodeAPI.updateBanner(id, data);

    const headers = authHeader();
    return apiCall(`/admin/banners/${id}`, { method: 'PUT', headers, body: data });
  },

  deleteBanner: (id) => {
    if (ON_NODE) return NodeAPI.deleteBanner(id);

    const headers = authHeader();
    return apiCall(`/admin/banners/${id}`, { method: 'DELETE', headers });
  },

  reorderBanners: (ids) => {
    if (ON_NODE) return NodeAPI.reorderBanners(ids);

    const headers = authHeader();
    return apiCall('/admin/banners/order', { method: 'PUT', headers, body: { ids } });
  },

  // The order of one row of siblings, top to bottom
  reorderCategories: (ids) => {
    if (ON_NODE) return NodeAPI.missing('Порядка категорий');

    const headers = authHeader();
    return apiCall('/admin/categories/order', {
      method: 'PUT',
      headers,
      body: { ids }
    });
  },

  // How far a running import has got
  importProgress: (jobId) =>
    ON_NODE ? NodeAPI.missing('Импорта из Excel') :
    apiCall(`/admin/import/progress/${encodeURIComponent(jobId)}`,
            { headers: authHeader() }),

  // ----- выгрузка 1С -----

  // Разбор файла выгрузки: что в нём есть, чего нет на сайте
  analyze1c: (file, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);

    return ON_NODE
      ? uploadWithProgress(`${NodeAPI.siteBase()}/admin/1c/analyze`,
                           formData, onProgress)
      : NodeAPI.missing('Разбора выгрузки 1С');
  },

  // Заводит отмеченные товары через их же админский API
  create1c: (items, categoryId) =>
    ON_NODE ? NodeAPI.create1c(items, categoryId)
            : NodeAPI.missing('Заведения товаров из 1С'),

  seoCatalog: () =>
    ON_NODE ? NodeAPI.seoCatalog()
            : apiCall('/admin/seo/catalog', { headers: authHeader() }),

  seoTraffic: (days = 30) =>
    ON_NODE ? NodeAPI.seoTraffic(days)
            : apiCall(`/admin/seo/traffic?days=${days}`, { headers: authHeader() }),

  getBrands: () => {
    if (ON_NODE) return NodeAPI.brandsAdmin();

    const headers = authHeader();
    return apiCall('/admin/brands', { headers });
  },

  // The logo rides along with the name, so a brand is created in one go
  createBrand: async (name, logoFile, onProgress) => {
    // У сервиса брендов имя и логотип идут двумя запросами: сначала бренд,
    // потом картинка — ей нужен id, которого до создания нет
    if (ON_NODE) {
      const brand = await NodeAPI.createBrand(name);
      return logoFile ? AdminAPI.uploadBrandLogo(brand.id, logoFile, onProgress)
                      : brand;
    }

    if (!logoFile) {
      const headers = authHeader();
      return apiCall('/admin/brands', { method: 'POST', headers, body: { name } });
    }

    const formData = new FormData();
    formData.append('name', name);
    formData.append('image', logoFile);
    return uploadWithProgress(`${API_BASE}/admin/brands`, formData, onProgress);
  },

  // What a selection would hit, before anything is changed
  previewBrandSelection: (selection) => {
    if (ON_NODE) return NodeAPI.previewRules(selection);

    const headers = authHeader();
    return apiCall('/admin/brands/preview', {
      method: 'POST',
      headers,
      body: selection
    });
  },

  // The brand for the rows ticked in the product list
  setProductsBrand: (productIds, brandId) => {
    if (ON_NODE) return NodeAPI.missing('Брендов');

    const headers = authHeader();
    return apiCall('/admin/products/brand', {
      method: 'PUT',
      headers,
      body: { product_ids: productIds, brand_id: brandId }
    });
  },

  updateBrand: (id, data) => {
    if (ON_NODE) return NodeAPI.updateBrand(id, data);

    const headers = authHeader();
    return apiCall(`/admin/brands/${id}`, { method: 'PUT', headers, body: data });
  },

  // Deleting a brand unlabels its products; it does not take them with it
  deleteBrand: (id) => {
    if (ON_NODE) return NodeAPI.deleteBrand(id);

    const headers = authHeader();
    return apiCall(`/admin/brands/${id}`, { method: 'DELETE', headers });
  },

  uploadBrandLogo: (id, file, onProgress) => {
    const formData = new FormData();
    formData.append('image', file);

    return uploadWithProgress(ON_NODE
      ? `${NodeAPI.siteBase()}/admin/brands/${id}/logo`
      : `${API_BASE}/admin/brands/${id}/upload-logo`, formData, onProgress)
      .then(brand => (ON_NODE && NodeAPI.forgetBrands(), brand));
  },

  clearBrandLogo: (id) => {
    if (ON_NODE) return NodeAPI.clearBrandLogo(id);

    const headers = authHeader();
    return apiCall(`/admin/brands/${id}/upload-logo`, { method: 'DELETE', headers });
  },

  // Labels a whole branch of the catalogue, or every article number that
  // starts the same way. `clear: true` takes the label back off.
  // На их бэкенде бренд не висит на товаре: он описан рядами артикулов
  // в нашем сервисе, и «назначить» значит переписать эти ряды
  setBrandRules: (id, rules) =>
    ON_NODE ? NodeAPI.setBrandRules(id, rules)
            : NodeAPI.missing('Правил брендов'),

  assignBrand: (id, selection) => {
    if (ON_NODE) return NodeAPI.missing('Назначения бренда по разделам');

    const headers = authHeader();
    return apiCall(`/admin/brands/${id}/assign`, {
      method: 'POST',
      headers,
      body: selection
    });
  }
};
