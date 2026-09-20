/* Слой совместимости с их бэкендом (Node + PostgreSQL).
 *
 * Страницы написаны под наш формат: product.sku, product.stock, дерево
 * категорий с детьми и счётчиками по всей ветке. Их API отдаёт article,
 * quantity и плоский список категорий. Вместо того чтобы править каждую
 * страницу, перевод делается здесь, в одном месте.
 *
 * Чего у них нет — брендов, слайдера, характеристик, исполнений — отдаётся
 * пустым. Пустое страницы уже умеют: фильтр брендов прячется сам, слайдер
 * уступает место шапке, блок исполнений не рисуется.
 */

const NodeAPI = (() => {
  const conf = () => (window.SHOP_CONFIG || {}).node || {};

  /* ---------- запросы ---------- */

  // Их админка принимает Basic, а не наш Bearer. Пароль обычно пуст,
  // но заголовок нужен и тогда: сервер отличает «не прислали» от «пусто».
  const CREDENTIALS_KEY = 'admin_basic';

  function basicHeader() {
    const stored = localStorage.getItem(CREDENTIALS_KEY);
    return stored ? { 'Authorization': `Basic ${stored}` } : {};
  }

  function rememberCredentials(user, password) {
    localStorage.setItem(CREDENTIALS_KEY, btoa(`${user}:${password}`));
  }

  function forgetCredentials() {
    localStorage.removeItem(CREDENTIALS_KEY);
  }

  async function call(base, path, options = {}) {
    const response = await fetch(`${base}${path}`, {
      method: options.method || 'GET',
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });

    if (!response.ok) {
      // Их формат ошибки — {error, code?, existing?}. Сообщение на русском
      // и предназначено для показа, а code и existing нужны обработчику 409.
      const details = await response.json().catch(() => null);
      const error = new Error(details?.error || `Сервер ответил ошибкой ${response.status}`);
      error.status = response.status;
      error.code = details?.code;
      error.existing = details?.existing;
      throw error;
    }

    return response.status === 204 ? null : response.json();
  }

  const readPublic = (path) => call(conf().public, path);
  const readAdmin = (path, options = {}) =>
    call(conf().admin, path, { ...options, headers: { ...basicHeader(), ...(options.headers || {}) } });

  /* ---------- товар ---------- */

  // Ссылки на медиа относительные, базовый адрес подставляет тот, кто
  // спрашивал: у публичного сервера и админки он разный
  const withBase = (base, url) =>
    url && url.startsWith('/') ? `${base}${url}` : url;

  function adaptProduct(item, base) {
    if (!item) return null;

    const media = item.media || [];
    const photos = media.filter(entry => entry.type === 'photo');
    const videos = media.filter(entry => entry.type === 'video');

    return {
      id: item.id,
      sku: item.article,
      name: item.name,
      category: item.category || null,
      // Брендов на той стороне нет вовсе
      brand: null,
      price: item.price,
      old_price: null,
      stock: item.quantity ?? 0,
      in_stock: item.in_stock ?? (item.quantity > 0),
      image: withBase(base, item.photo || photos[0]?.url || null),
      discount: null,

      description: item.description,
      images: photos.map(entry => ({
        id: entry.id, url: withBase(base, entry.url),
        alt: item.name, order: entry.sort_order,
      })),
      videos: videos.map(entry => ({
        id: entry.id, url: withBase(base, entry.url), title: '',
      })),
      // Характеристик у них нет: только описание текстом
      specifications: [],
      updated_at: item.updated_at,

      // Служебные поля админки
      is_active: item.is_active,
      manually_edited: item.manually_edited,
      is_linked_to_1c: item.is_linked_to_1c,
    };
  }

  function adaptList(data, base) {
    return {
      products: (data.items || []).map(item => adaptProduct(item, base)),
      total: data.total || 0,
      pages: data.pages || 1,
      current_page: data.page || 1,
    };
  }

  // Наши названия сортировок против их
  const SORTS = {
    newest: '-updated',
    name: 'name',
    price_asc: 'price',
    price_desc: '-price',
  };

  function listQuery(filters = {}) {
    const params = new URLSearchParams();

    // У них один параметр category, и он уже включает вложенные категории —
    // ровно то, что нам нужно от дерева
    if (filters.category_id) params.append('category', filters.category_id);
    if (filters.search) params.append('search', filters.search);
    if (filters.sort) params.append('sort', SORTS[filters.sort] || 'name');
    if (filters.page) params.append('page', filters.page);
    if (filters.per_page) params.append('limit', Math.min(filters.per_page, 100));

    // brand_id не передаём: брендов на той стороне нет, и фильтр по ним
    // на витрине не показывается

    return params.toString();
  }

  /* ---------- категории ---------- */

  // Их список плоский. Дерево собирается здесь, и здесь же считаются
  // счётчики по всей ветке: их products_count считает только саму
  // категорию, без вложенных.
  function buildTree(items) {
    const byId = new Map();
    items.forEach(item => byId.set(item.id, {
      id: item.id,
      name: item.name,
      slug: item.slug,
      description: null,
      icon: null,
      parent_id: item.parent_id,
      sort_order: 0,
      product_count: item.products_count || 0,
      total_count: 0,
      has_children: false,
      image: null,
      brand_id: null,
      brand: null,
      children: [],
    }));

    const roots = [];
    byId.forEach(node => {
      const parent = node.parent_id ? byId.get(node.parent_id) : null;
      if (parent) {
        parent.children.push(node);
        parent.has_children = true;
      } else {
        roots.push(node);
      }
    });

    // Сумма по ветке считается снизу вверх, поэтому обход в глубину
    const total = (node) =>
      (node.total_count = node.product_count
        + node.children.reduce((sum, child) => sum + total(child), 0));
    roots.forEach(total);

    const byName = (a, b) => a.name.localeCompare(b.name, 'ru');
    const sortDeep = (nodes) => {
      nodes.sort(byName);
      nodes.forEach(node => sortDeep(node.children));
    };
    sortDeep(roots);

    return roots;
  }

  function findInTree(tree, id) {
    for (const node of tree) {
      if (node.id === id) return node;
      const found = findInTree(node.children, id);
      if (found) return found;
    }
    return null;
  }

  function pathTo(tree, id, trail = []) {
    for (const node of tree) {
      const here = [...trail, node];
      if (node.id === id) return here;
      const found = pathTo(node.children, id, here);
      if (found) return found;
    }
    return null;
  }

  async function categoryTree() {
    const data = await readPublic('/categories');
    return buildTree(data.items || []);
  }

  /* ---------- то, чего у них нет ---------- */

  // Пустой ответ, а не ошибка: страница просто не покажет этот блок
  const nothing = () => Promise.resolve([]);

  return {
    basicHeader, rememberCredentials, forgetCredentials,
    adaptProduct, adaptList, listQuery, buildTree, findInTree, pathTo,
    categoryTree, readPublic, readAdmin, call, conf, nothing, withBase,
  };
})();
