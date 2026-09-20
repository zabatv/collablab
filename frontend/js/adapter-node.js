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

  // Ссылки на медиа относительные. Базовым всегда берётся публичный
  // сервер: он отдаёт файлы без пароля, а к <img> браузер заголовок
  // авторизации не приложит — из-под админки фотографии были бы битыми.
  const mediaBase = () => conf().public || conf().admin || '';

  const withBase = (url) =>
    url && url.startsWith('/') ? `${mediaBase()}${url}` : url;

  function adaptProduct(item) {
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
      image: withBase(item.photo || photos[0]?.url || null),
      discount: null,

      description: item.description,
      images: photos.map(entry => ({
        id: entry.id, url: withBase(entry.url),
        alt: item.name, order: entry.sort_order,
      })),
      videos: videos.map(entry => ({
        id: entry.id, url: withBase(entry.url), title: '',
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

  function adaptList(data) {
    return {
      products: (data.items || []).map(adaptProduct),
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

  /* ---------- картинки категорий ---------- */

  // Картинки у категории в их API нет, а плитки на главной без неё пустые.
  // Поэтому фотография берётся взаймы у товара, как это делал наш бэкенд.
  // Ради этого список товаров прочитывается целиком — по сотне за раз,
  // первая страница последовательно, остальные разом — и раскладывается
  // по категориям. На сессию результат запоминается.
  const PREVIEW_KEY = 'category_previews';
  let previews = null;

  function rememberPreviews(found) {
    previews = found;
    try {
      sessionStorage.setItem(PREVIEW_KEY, JSON.stringify(found));
    } catch (error) {
      // приватное окно или запрет на хранилище: обойдёмся памятью
    }
    return found;
  }

  function collectPhotos(items, into) {
    items.forEach(item => {
      const id = item.category?.id;
      if (id && item.photo && !into[id]) into[id] = withBase(item.photo);
    });
    return into;
  }

  async function categoryPreviews() {
    if (previews) return previews;
    try {
      const stored = sessionStorage.getItem(PREVIEW_KEY);
      if (stored) return (previews = JSON.parse(stored));
    } catch (error) {
      // хранилище недоступно, читаем как в первый раз
    }

    const first = await readPublic('/products?limit=100&page=1');
    const found = collectPhotos(first.items || [], {});

    const rest = [];
    for (let page = 2; page <= Math.min(first.pages || 1, 20); page++) {
      rest.push(readPublic(`/products?limit=100&page=${page}`));
    }
    (await Promise.all(rest)).forEach(data => collectPhotos(data.items || [], found));

    return rememberPreviews(found);
  }

  // Своё фото, а если товары лежат глубже — первое найденное в ветке
  function paintPreviews(nodes, found) {
    nodes.forEach(node => {
      paintPreviews(node.children, found);
      node.image = found[node.id]
        || node.children.map(child => child.image).find(Boolean)
        || null;
    });
    return nodes;
  }

  async function categoryTree() {
    const [data, found] = await Promise.all([
      readPublic('/categories'),
      categoryPreviews().catch(() => ({})),
    ]);
    return paintPreviews(buildTree(data.items || []), found);
  }

  // Ветка нужна дереву, но не тому, кто спрашивал про одну категорию
  const strip = ({ children, ...rest }) => rest;

  async function categoriesFlat() {
    const flat = [];
    const walk = (nodes) => nodes.forEach(node => {
      flat.push(strip(node));
      walk(node.children);
    });
    walk(await categoryTree());
    return flat;
  }

  // Наш формат: сама категория, путь до корня и прямые дети
  async function category(id) {
    const tree = await categoryTree();
    const wanted = Number(id);
    const node = findInTree(tree, wanted);
    if (!node) throw new Error('Категория не найдена');

    return {
      ...strip(node),
      path: (pathTo(tree, wanted) || []).map(strip),
      children: node.children.map(strip),
    };
  }

  /* ---------- витрина ---------- */

  async function products(filters = {}) {
    return adaptList(await readPublic(`/products?${listQuery(filters)}`));
  }

  const product = async (id) => adaptProduct(await readPublic(`/products/${id}`));

  /* ---------- админка ---------- */

  // Дерево для админки берётся у админского сервера: там видны и скрытые
  // товары, и открыта она бывает, когда публичный сервер ещё не поднят
  async function adminCategoryTree() {
    const data = await readAdmin('/categories');
    return buildTree(data.items || []);
  }

  async function adminProducts(page = 1, perPage = 20) {
    const data = await readAdmin(`/products?page=${page}&limit=${perPage}`);
    const { products: items, total, pages } = adaptList(data);
    return { products: items, total, pages };
  }

  // «Цена по запросу» это null, а не ноль: ноль они не примут
  function asPrice(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0
      ? Math.round(number * 100) / 100
      : null;
  }

  const asCategory = (value) =>
    value === '' || value === undefined || value === null ? null : Number(value);

  async function createProduct(data) {
    const created = await readAdmin('/products', {
      method: 'POST',
      body: {
        article: String(data.sku || '').trim(),
        name: String(data.name || '').trim(),
        description: data.description || '',
        category_id: asCategory(data.category_id),
        price: asPrice(data.price),
        is_active: data.is_active !== false,
      },
    });
    return adaptProduct(created);
  }

  // Цену, остаток и артикул они менять не дают — это поля 1С. Всё лишнее
  // здесь же и отсекается, иначе сервер ответит 400 на целую карточку.
  const PATCHABLE = ['name', 'description', 'is_active', 'manually_edited'];

  async function updateProduct(id, data) {
    const body = {};
    PATCHABLE.forEach(field => {
      if (data[field] !== undefined) body[field] = data[field];
    });
    if (data.category_id !== undefined) body.category_id = asCategory(data.category_id);

    const saved = await readAdmin(`/products/${id}`, { method: 'PATCH', body });
    return adaptProduct(saved);
  }

  const deleteMedia = (productId, mediaId) =>
    readAdmin(`/products/${productId}/media/${mediaId}`, { method: 'DELETE' });

  // Пароль они не проверяют отдельным эндпоинтом: единственный способ
  // узнать, подходит ли он — сходить за данными и посмотреть на ответ
  async function checkCredentials() {
    await readAdmin('/products?limit=1');
    return true;
  }

  /* ---------- то, чего у них нет ---------- */

  // Пустой ответ, а не ошибка: страница просто не покажет этот блок
  const nothing = () => Promise.resolve([]);

  // А вот запись молча терять нельзя: пусть скажет, почему не вышло
  const missing = (what) => Promise.reject(new Error(
    `${what} нет в API этого бэкенда. Раздел заработает, когда на той стороне появится эндпоинт.`));

  return {
    basicHeader, rememberCredentials, forgetCredentials, checkCredentials,
    adaptProduct, adaptList, listQuery, buildTree, findInTree, pathTo,
    categoryTree, categoriesFlat, category, products, product,
    adminCategoryTree, adminProducts, createProduct, updateProduct, deleteMedia,
    readPublic, readAdmin, call, conf, nothing, missing, withBase,
  };
})();
