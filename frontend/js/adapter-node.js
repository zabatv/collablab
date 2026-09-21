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

  /* ---------- наш сервис: бренды, слайдер, счётчики, SEO ---------- */

  // Адрес сервиса. Старое имя brands понимается тоже: у кого-то в config.js
  // осталось оно.
  const siteBase = () => conf().site || conf().brands || '';

  // Описания разделов: их бэкенд такого поля не знает, текст живёт у нас
  async function categoryTexts() {
    if (!siteBase()) return {};
    try {
      const data = await call(siteBase(), '/categories/text');
      const byId = {};
      (data.items || []).forEach(item => {
        byId[item.category_id] = item.description;
      });
      return byId;
    } catch (error) {
      console.warn('Описания разделов недоступны:', error.message);
      return {};
    }
  }

  function saveCategoryText(categoryId, description) {
    if (!siteBase()) throw new Error('Сервис сайта не настроен');
    // call сам превращает тело в JSON — сюда идёт объект, не строка
    return call(siteBase(), `/admin/categories/${categoryId}/text`, {
      method: 'PUT',
      headers: basicHeader(),
      body: { description },
    });
  }

  // Слайды на главной
  async function banners() {
    if (!siteBase()) return [];
    try {
      const list = await call(siteBase(), '/banners');
      return list.map(withPicture);
    } catch (error) {
      console.warn('Слайдер недоступен:', error.message);
      return [];
    }
  }

  const withPicture = (slide) => ({
    ...slide,
    image: slide.image ? `${siteBase()}${slide.image}` : null,
  });

  const adminBanners = () =>
    callSite('/admin/banners').then(list => list.map(withPicture));

  const updateBanner = (id, data) =>
    callSite(`/admin/banners/${id}`, { method: 'PUT', body: data })
      .then(withPicture);

  const deleteBanner = (id) =>
    callSite(`/admin/banners/${id}`, { method: 'DELETE' });

  const reorderBanners = (ids) =>
    callSite('/admin/banners/order', { method: 'PUT', body: { ids } });

  // Счётчики: сколько раз открыли товар и что искали. Ни адреса, ни
  // идентификатора посетителя — только сами числа.
  function trackView(productId) {
    if (!siteBase()) return;
    call(siteBase(), `/track/view/${productId}`, { method: 'POST' })
      .catch(() => {});
  }

  function trackSearch(query, results) {
    if (!siteBase() || !query) return;
    call(siteBase(), '/track/search',
         { method: 'POST', body: { query, results } }).catch(() => {});
  }

  // Выгрузка 1С: что в ней есть сверх того, что уже на сайте
  const create1c = (items, categoryId) =>
    callSite('/admin/1c/create',
             { method: 'POST', body: { items, category_id: categoryId } });

  const seoTraffic = (days = 30) => callSite(`/admin/seo/traffic?days=${days}`);
  const seoCatalog = () => callSite('/admin/seo/catalog');

  /* ---------- бренды ---------- */

  // Бренды живут в нашем сервисе: их бэкенд про них не знает. Связь с
  // товаром — по артикулу, потому что товары теперь чужие и их id нам не
  // принадлежат, а артикул общий и у них, и у нас, и в 1С.
  const NO_BRANDS = { brands: [], prefixes: [], articles: {}, byId: new Map() };
  let brandsLoaded = null;

  // Артикул без регистра, пробелов и знаков: FA40 и FA-40 — одно и то же,
  // ровно как сопоставляет их бэкенд с 1С
  const looseKey = (article) =>
    String(article || '').replace(/[^0-9a-zA-Zа-яА-ЯёЁ]+/g, '').toLowerCase();

  async function brandMap() {
    if (brandsLoaded) return brandsLoaded;

    const base = siteBase();
    if (!base) return (brandsLoaded = NO_BRANDS);

    try {
      const data = await call(base, '/map');
      // Длинный ряд артикулов точнее короткого, поэтому проверяется первым
      data.prefixes.sort((a, b) => b.value.length - a.value.length);
      data.byId = new Map(data.brands.map(brand => [brand.id, {
        ...brand,
        logo: brand.logo ? `${base}${brand.logo}` : null,
      }]));
      return (brandsLoaded = data);
    } catch (error) {
      // Сервис брендов молчит — витрина работает без них, а не падает
      console.warn('Бренды недоступны:', error.message);
      return (brandsLoaded = NO_BRANDS);
    }
  }

  // Синхронно, по уже загруженной карте: adaptProduct вызывается пачками
  function brandOf(article) {
    const map = brandsLoaded;
    if (!map) return null;

    const key = looseKey(article);
    if (!key) return null;

    const exact = map.articles[key];
    if (exact) return map.byId.get(exact) || null;

    const rule = map.prefixes.find(prefix => key.startsWith(prefix.value));
    return rule ? map.byId.get(rule.brand_id) || null : null;
  }

  // Бренды, за которыми стоят товары. Пустой бренд в фильтре — строка,
  // которая всегда возвращает ничего.
  async function brands() {
    const [map, items] = await Promise.all([brandMap(), catalogue()]);
    const counts = new Map();

    items.forEach(item => {
      const brand = brandOf(item.article);
      if (brand) counts.set(brand.id, (counts.get(brand.id) || 0) + 1);
    });

    return map.brands
      .filter(brand => counts.get(brand.id))
      .map(brand => ({ ...map.byId.get(brand.id),
                       product_count: counts.get(brand.id) }));
  }

  /* ---------- бренды: правка ---------- */

  // Наш сервис проверяет тот же логин и пароль, что их админский сервер, —
  // в админке один вход на оба
  const callSite = (path, options = {}) =>
    call(siteBase(), path, {
      ...options,
      headers: { ...basicHeader(), ...(options.headers || {}) },
    });

  const callBrands = callSite;

  // После правки карту надо перечитать, иначе витрина в этой вкладке
  // останется со старыми привязками
  const forgetBrands = () => { brandsLoaded = null; };

  const withLogo = (brand) => ({
    ...brand,
    logo: brand.logo ? `${siteBase()}${brand.logo}` : null,
  });

  // Список для админки: с рядами артикулов и с тем, сколько товаров
  // каждый бренд реально накрывает
  async function brandsAdmin() {
    forgetBrands();
    const [list, items] = await Promise.all([
      callBrands('/brands'),
      catalogue().catch(() => []),
      brandMap(),
    ]);

    const counts = new Map();
    items.forEach(item => {
      const brand = brandOf(item.article);
      if (brand) counts.set(brand.id, (counts.get(brand.id) || 0) + 1);
    });

    return list.map(brand => ({
      ...withLogo(brand),
      product_count: counts.get(brand.id) || 0,
    }));
  }

  const createBrand = (name) =>
    callBrands('/admin/brands', { method: 'POST', body: { name } })
      .then(brand => (forgetBrands(), withLogo(brand)));

  const updateBrand = (id, data) =>
    callBrands(`/admin/brands/${id}`, { method: 'PUT', body: data })
      .then(brand => (forgetBrands(), withLogo(brand)));

  const deleteBrand = (id) =>
    callBrands(`/admin/brands/${id}`, { method: 'DELETE' })
      .then(() => forgetBrands());

  const clearBrandLogo = (id) =>
    callBrands(`/admin/brands/${id}/logo`, { method: 'DELETE' })
      .then(brand => (forgetBrands(), withLogo(brand)));

  // Ряды артикулов бренда и отдельные артикулы — целиком, одним списком
  const setBrandRules = (id, rules) =>
    callBrands(`/admin/brands/${id}/rules`, { method: 'PUT', body: rules })
      .then(brand => (forgetBrands(), withLogo(brand)));

  // Что накроет правило, до того как его сохранят
  async function previewRules({ prefixes = [], articles = [] }) {
    const [items] = await Promise.all([catalogue(), brandMap()]);
    const heads = prefixes.map(looseKey).filter(Boolean);
    const exact = new Set(articles.map(looseKey).filter(Boolean));

    const hit = items.filter(item => {
      const key = looseKey(item.article);
      return exact.has(key) || heads.some(head => key.startsWith(head));
    });

    return {
      count: hit.length,
      sample: hit.slice(0, 8).map(item => ({
        sku: item.article,
        name: item.name,
        brand: brandOf(item.article)?.name || null,
      })),
    };
  }

  // Артикулы раздела вместе с подкатегориями. Раздел — понятие их каталога,
  // а правило бренда живёт на артикулах, поэтому одно разворачивается в
  // другое: это снимок, новые товары раздела сами бренд не получат.
  async function articlesInCategory(categoryId) {
    const [items, tree] = await Promise.all([catalogue(), categoryTree()]);

    const branch = new Set();
    const walk = (node) => {
      if (!node) return;
      branch.add(node.id);
      node.children.forEach(walk);
    };
    walk(findInTree(tree, Number(categoryId)));

    return items.filter(item => branch.has(item.category?.id))
                .map(item => item.article);
  }

  /* ---------- товар ---------- */

  // Ссылки на медиа относительные. Базовым всегда берётся публичный
  // сервер: он отдаёт файлы без пароля, а к <img> браузер заголовок
  // авторизации не приложит — из-под админки фотографии были бы битыми.
  const mediaBase = () => conf().public || conf().admin || '';

  const withBase = (url) =>
    url && url.startsWith('/') ? `${mediaBase()}${url}` : url;

  /* ---------- характеристики из названия ---------- */

  /* 1С отдаёт характеристики не полями, а одной строкой:
     «(-0,6...6 бар) Реле давления, диф.=0,6...4 бар, Рмакс=16 бар,
     (-10...+110С), G1/4, 8А». Имя параметра там есть далеко не у каждого
     куска, поэтому кусок без имени показывается целиком: придумывать
     названия («8А» — это ток или напряжение?) в техническом каталоге
     дороже, чем оставить ровно так, как написал поставщик. */
  /* Запятая делит строку только снаружи скобок и только если за ней стоит
     пробел. Иначе разлетаются и дробные числа («0,6»), и диапазоны внутри
     скобок («T=(0...+60)°C» — это один параметр, а не три). */
  function splitTop(text) {
    const parts = [];
    let depth = 0;
    let start = 0;

    for (let i = 0; i < text.length; i++) {
      if (text[i] === '(') depth++;
      else if (text[i] === ')') depth = Math.max(0, depth - 1);
      else if (text[i] === ',' && depth === 0 && /\s/.test(text[i + 1] || '')) {
        parts.push(text.slice(start, i));
        start = i + 1;
      }
    }

    parts.push(text.slice(start));
    return parts;
  }

  function specsFromName(name) {
    // Пробелы приводятся к одному так же, как в splitName: иначе кусок из
    // названия не совпадёт с заголовком страницы и удвоится строкой таблицы
    const whole = String(name || '').replace(/\xa0/g, ' ').replace(/\s+/g, ' ').trim();

    // Скобка со списком внутри — это перечисление, а не одно значение:
    // «датчик (М12х1, PNP, IP67)» разворачивается в три строки
    const parts = [];
    splitTop(whole).forEach(part => {
      const group = part.match(/^(.*?)\(([^()]*)\)(.*)$/);
      const inside = group && splitTop(group[2]);

      if (inside && inside.length > 1) {
        parts.push(group[1], ...inside, group[3]);
      } else {
        parts.push(part);
      }
    });

    // То, что уже стоит заголовком страницы, повторять строкой таблицы незачем
    const heading = String(splitName(whole).title).trim().toLowerCase();

    const clean = (part) => part
      .trim()
      // Одинокая скобка вокруг всего куска ничего не сообщает: «(-10...+110С)»
      .replace(/^\((.*)\)$/, '$1')
      .replace(/^[.;:,\s]+|[.;,\s]+$/g, '')
      .trim();

    return parts
      .map(part => {
        const text = clean(part);
        // «(-0,6...6 бар) Реле давления» — название детали отрезается,
        // диапазон остаётся: он и есть характеристика
        const without = clean(text.toLowerCase().endsWith(heading)
          ? text.slice(0, text.length - heading.length)
          : text);
        return heading && without !== text ? without : text;
      })
      // Заголовок страницы обрывается на первой запятой, поэтому кусок
      // может быть его началом, а не всем целиком — такой тоже лишний
      .filter(part => {
        const lower = part.toLowerCase();
        return part && lower !== heading
          && !(lower.length >= 6 && heading.startsWith(lower));
      })
      .map(part => {
        // «диф.=0,6...4 бар», «корпус - пластик» — имя параметра 1С дала сама
        const named = part.match(/^([^=:]{1,24}?)\s*(?:[=:]|\s[-—]\s)\s*(.+)$/);
        if (!named) return { name: '', value: part };

        // «Pвх=(1...10) бар» — скобка вокруг диапазона ничего не добавляет
        return { name: named[1], value: named[2].replace(/^\(([^()]*)\)/, '$1') };
      });
  }

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
      // Бренда у них нет; он приходит из нашего сервиса, по артикулу
      brand: brandOf(item.article),
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
      // Полей с характеристиками у них нет — разбирается строка из 1С.
      // Что заказчик вписал руками, перебьёт это в product().
      specifications: specsFromName(item.name),
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

    // brand_id не передаём: бренды наши, их API про них не знает. Отбор
    // по бренду идёт другой дорогой, см. byBrand.

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

  /* ---------- каталог целиком ---------- */

  // Список товаров читается один раз и пригождается трижды: из него берутся
  // фотографии для плиток категорий, из него же считаются бренды и по нему
  // отбираются товары бренда — фильтровать по бренду их API не умеет, бренды
  // не его. По сотне за раз: первая страница последовательно, остальные разом.
  let everything = null;

  async function catalogue() {
    if (everything) return everything;

    const first = await readPublic('/products?limit=100&page=1');
    const items = [...(first.items || [])];

    const rest = [];
    for (let page = 2; page <= Math.min(first.pages || 1, 20); page++) {
      rest.push(readPublic(`/products?limit=100&page=${page}`));
    }
    (await Promise.all(rest)).forEach(data => items.push(...(data.items || [])));

    return (everything = items);
  }

  // Что список товаров знает о категории: фотографию для плитки и какие
  // бренды в ней лежат. Ноль в наборе — товар без бренда.
  function categoryFacts(items) {
    const photo = {};
    const inside = {};

    items.forEach(item => {
      const id = item.category?.id;
      if (!id) return;

      if (item.photo && !photo[id]) photo[id] = withBase(item.photo);
      (inside[id] = inside[id] || new Set()).add(brandOf(item.article)?.id || 0);
    });

    return { photo, inside };
  }

  // Фотография — своя или первая найденная в ветке. Бренд — только если он
  // у всей ветки один: раздел с товарами двух марок ничей.
  function paintNode(node, facts, byId) {
    const found = new Set(facts.inside[node.id] || []);
    node.children.forEach(child =>
      paintNode(child, facts, byId).forEach(id => found.add(id)));

    node.image = facts.photo[node.id]
      || node.children.map(child => child.image).find(Boolean)
      || null;

    const single = found.size === 1 ? [...found][0] : 0;
    node.brand_id = single || null;
    node.brand = single ? byId.get(single) || null : null;

    return found;
  }

  async function categoryTree() {
    const [data, map, items, texts] = await Promise.all([
      readPublic('/categories'),
      brandMap(),
      catalogue().catch(() => []),
      categoryTexts(),
    ]);

    const roots = buildTree(data.items || []);
    const facts = categoryFacts(items);
    roots.forEach(node => paintNode(node, facts, map.byId));

    const describe = (nodes) => nodes.forEach(node => {
      node.description = texts[node.id] || null;
      describe(node.children);
    });
    describe(roots);

    return roots;
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
    if (filters.brand_id) return byBrand(filters);
    if (filters.exact_category) return ownProducts(filters);

    // Бренды должны быть под рукой раньше товаров: adaptProduct берёт их
    // из уже загруженной карты
    const [, data] = await Promise.all([
      brandMap(),
      readPublic(`/products?${listQuery(filters)}`),
    ]);

    // Что ищут — в счётчики, но только первая страница запроса, иначе
    // перелистывание засчитается как новый поиск
    if (filters.search && (filters.page || 1) === 1) {
      trackSearch(filters.search, data.total || 0);
    }

    return adaptList(data);
  }

  // Характеристики, вписанные заказчиком. Ключ — артикул: он переживает
  // любую перевыгрузку из 1С, а id товара — нет.
  async function productSpecs(article) {
    if (!siteBase() || !article) return [];
    try {
      const data = await call(siteBase(), `/specs/${encodeURIComponent(article)}`);
      return data.rows || [];
    } catch (error) {
      console.warn('Характеристики недоступны:', error.message);
      return [];
    }
  }

  function saveProductSpecs(article, rows) {
    if (!siteBase()) throw new Error('Сервис сайта не настроен');
    return call(siteBase(), `/admin/specs/${encodeURIComponent(article)}`, {
      method: 'PUT',
      headers: basicHeader(),
      body: { rows },
    });
  }

  /* ---------- категории ---------- */

  /* Их админский сервер: POST/PATCH/DELETE /categories
     (docs/backend/admin-categories.js). Дерево читается оттуда же. */

  // Форма админки шлёт всё разом, включая description и brand_id. У их
  // категории таких полей нет, а на лишнее она отвечает 400 — описание
  // живёт в нашем сервисе и сохраняется отдельной кнопкой.
  const categoryFields = (data) => ({
    name: data.name,
    parent_id: data.parent_id || null,
  });

  const createCategory = (data) =>
    readAdmin('/categories', { method: 'POST', body: categoryFields(data) });

  const updateCategory = (id, data) =>
    readAdmin(`/categories/${id}`, { method: 'PATCH', body: categoryFields(data) });

  const deleteCategory = (id) =>
    readAdmin(`/categories/${id}`, { method: 'DELETE' });

  /* ---------- документация ---------- */

  /* Паспорт, чертёж, каталог производителя. Их медиа принимает только
     картинки и видео, PDF оно отклоняет, — поэтому файлы держит наш
     сервис, снова по артикулу. */
  async function productDocs(article) {
    if (!siteBase() || !article) return [];
    try {
      const data = await call(siteBase(), `/docs/${encodeURIComponent(article)}`);
      return (data.items || []).map(item => ({
        ...item,
        url: `${siteBase()}${item.url}`,
      }));
    } catch (error) {
      console.warn('Документация недоступна:', error.message);
      return [];
    }
  }

  async function uploadProductDoc(article, file, title = '') {
    if (!siteBase()) throw new Error('Сервис сайта не настроен');

    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);

    // FormData отправляется сам: call() кладёт JSON, а здесь нужен multipart
    const response = await fetch(
      `${siteBase()}/admin/docs/${encodeURIComponent(article)}`,
      { method: 'POST', headers: basicHeader(), body: form });

    const details = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(details?.error || `Сервер ответил ошибкой ${response.status}`);
    }
    return details;
  }

  const renameProductDoc = (docId, title) =>
    call(siteBase(), `/admin/docs/${docId}`,
         { method: 'PUT', headers: basicHeader(), body: { title } });

  const deleteProductDoc = (docId) =>
    call(siteBase(), `/admin/docs/${docId}`,
         { method: 'DELETE', headers: basicHeader() });

  async function product(id) {
    const [, item] = await Promise.all([
      brandMap(),
      readPublic(`/products/${id}`),
    ]);

    const adapted = adaptProduct(item);
    if (!adapted) return adapted;

    const [stored, docs] = await Promise.all([
      productSpecs(adapted.sku),
      productDocs(adapted.sku),
    ]);

    // Вписанное руками важнее разобранного из наименования
    if (stored.length) adapted.specifications = stored;
    adapted.documents = docs;

    return adapted;
  }

  // Отбор по бренду их API не умеет — бренды не его. Поэтому фильтр,
  // сортировка и страницы считаются здесь, по уже прочитанному каталогу.
  const LOCAL_SORTS = {
    name: (a, b) => a.name.localeCompare(b.name, 'ru'),
    price_asc: (a, b) => (a.price ?? Infinity) - (b.price ?? Infinity),
    price_desc: (a, b) => (b.price ?? -Infinity) - (a.price ?? -Infinity),
    newest: (a, b) => String(b.updated_at).localeCompare(String(a.updated_at)),
  };

  async function byBrand(filters) {
    const [, items, tree] = await Promise.all([
      brandMap(), catalogue(), categoryTree(),
    ]);

    const wanted = Number(filters.brand_id);
    let list = items.filter(item => brandOf(item.article)?.id === wanted);

    if (filters.category_id) {
      const branch = new Set();
      const walk = (node) => {
        if (!node) return;
        branch.add(node.id);
        node.children.forEach(walk);
      };
      walk(findInTree(tree, Number(filters.category_id)));
      list = list.filter(item => branch.has(item.category?.id));
    }

    if (filters.search) {
      const needle = String(filters.search).toLowerCase();
      list = list.filter(item =>
        String(item.name).toLowerCase().includes(needle)
        || String(item.article).toLowerCase().includes(needle));
    }

    list = [...list].sort(LOCAL_SORTS[filters.sort] || LOCAL_SORTS.name);

    const perPage = filters.per_page || 24;
    const page = filters.page || 1;

    return {
      products: list.slice((page - 1) * perPage, page * perPage).map(adaptProduct),
      total: list.length,
      pages: Math.max(1, Math.ceil(list.length / perPage)),
      current_page: page,
    };
  }

  /* Только товары самой категории, без вложенных. Их API так не умеет:
     параметр category всегда берёт ветку целиком. Страница раздела с
     подкатегориями просит именно это: то, что лежит в подкатегориях, уже
     показано плитками выше, и повторять его списком незачем.

     Каталог всё равно загружен целиком — ради плиток и брендов, — поэтому
     отбор и постраничка делаются здесь, тем же способом, что и для брендов. */
  async function ownProducts(filters) {
    const [, items] = await Promise.all([brandMap(), catalogue()]);

    const wanted = Number(filters.exact_category);
    let list = items.filter(item => item.category?.id === wanted);

    if (filters.search) {
      const needle = String(filters.search).toLowerCase();
      list = list.filter(item =>
        String(item.name).toLowerCase().includes(needle)
        || String(item.article).toLowerCase().includes(needle));
    }

    list = [...list].sort(LOCAL_SORTS[filters.sort] || LOCAL_SORTS.name);

    const perPage = filters.per_page || 24;
    const page = filters.page || 1;

    return {
      products: list.slice((page - 1) * perPage, page * perPage).map(adaptProduct),
      total: list.length,
      pages: Math.max(1, Math.ceil(list.length / perPage)),
      current_page: page,
    };
  }

  /* ---------- админка ---------- */

  // Дерево для админки берётся у админского сервера: там видны и скрытые
  // товары, и открыта она бывает, когда публичный сервер ещё не поднят
  async function adminCategoryTree() {
    const [data, texts] = await Promise.all([
      readAdmin('/categories'),
      categoryTexts(),
    ]);

    const roots = buildTree(data.items || []);
    const describe = (nodes) => nodes.forEach(node => {
      node.description = texts[node.id] || '';
      describe(node.children);
    });
    describe(roots);

    return roots;
  }

  /* ---------- когда каталог последний раз менялся ---------- */

  /* Эндпоинта «когда отработала синхронизация» у них нет. Единственный
     честный след — сами товары: 1С правит цену и остаток, и вместе с ними
     меняется updated_at. Самое свежее время в каталоге и есть время
     последнего изменения — с оговоркой, что руками из админки товар
     правят тем же полем. */
  async function lastChange() {
    const data = await readAdmin('/products?limit=1&sort=-updated');
    const item = (data.items || [])[0];
    if (!item) return null;

    return {
      at: item.updated_at,
      article: item.article,
      name: item.name,
      linked: Boolean(item.is_linked_to_1c),
    };
  }

  /* Сколько позиций 1С узнала по артикулу. Это и есть здоровье обмена:
     связанным она возит цену и остаток, остальные стоят как заведены. */
  async function linkedTo1c() {
    const first = await readAdmin('/products?limit=100&page=1');
    const items = [...(first.items || [])];

    const rest = [];
    for (let page = 2; page <= Math.min(first.pages || 1, 40); page++) {
      rest.push(readAdmin(`/products?limit=100&page=${page}`));
    }
    (await Promise.all(rest)).forEach(data => items.push(...(data.items || [])));

    return {
      total: items.length,
      linked: items.filter(item => item.is_linked_to_1c).length,
    };
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
    brandMap, brandOf, brands, catalogue, looseKey,
    banners, adminBanners, updateBanner, deleteBanner, reorderBanners,
    trackView, trackSearch, seoTraffic, seoCatalog, siteBase, create1c,
    categoryTexts, saveCategoryText, productSpecs, saveProductSpecs,
    lastChange, linkedTo1c, createCategory, updateCategory, deleteCategory,
    productDocs, uploadProductDoc, renameProductDoc, deleteProductDoc,
    brandsAdmin, createBrand, updateBrand, deleteBrand, clearBrandLogo,
    setBrandRules, previewRules, forgetBrands, articlesInCategory,
    adminCategoryTree, adminProducts, createProduct, updateProduct, deleteMedia,
    readPublic, readAdmin, call, conf, nothing, missing, withBase,
  };
})();
