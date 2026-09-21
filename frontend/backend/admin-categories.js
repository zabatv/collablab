// Заведение и правка категорий. Подключается ТОЛЬКО в сервере админки
// (src/admin-app.js), в публичном API их нет: там категории только читают.
//
// Таблица categories держит любую вложенность (parent_id), поэтому третий
// уровень — «подкатегория подкатегории» — работает без правок схемы.
//
// Синхронизация 1С ведёт только те категории, у которых проставлен
// external_id, и только когда в классификаторе выгрузки больше одной группы
// (см. update-catalog.js). Заведённые здесь external_id не получают: 1С их
// не трогает и не переименовывает обратно.
import { Router } from 'express';
import { withTransaction } from '../db.js';
import { HttpError } from '../http-error.js';
import { cleanText, slugify } from '../normalize.js';

const MAX_NAME = 100;

const FIELD_VALIDATORS = {
  name(v) {
    if (typeof v !== 'string') throw new HttpError(400, 'name должен быть строкой');
    const name = cleanText(v);
    if (!name) throw new HttpError(400, 'Название категории не может быть пустым');
    if (name.length > MAX_NAME) throw new HttpError(400, `Название длиннее ${MAX_NAME} символов`);
    return name;
  },
  parent_id(v) {
    if (v === null) return null;
    if (!(Number.isInteger(v) && v > 0)) throw new HttpError(400, 'parent_id должен быть целым числом или null');
    return v;
  },
};

const ALLOWED_FIELDS = Object.keys(FIELD_VALIDATORS);

function validateFields(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new HttpError(400, 'Ожидается JSON-объект');
  const unknown = Object.keys(body).filter((k) => !ALLOWED_FIELDS.includes(k));
  if (unknown.length) throw new HttpError(400, `Недопустимые поля: ${unknown.join(', ')}. Разрешены: ${ALLOWED_FIELDS.join(', ')}`);
  return Object.fromEntries(Object.entries(body).map(([k, v]) => [k, FIELD_VALIDATORS[k](v)]));
}

/** Проверяет тело POST. slug собирается сервером: адреса на сайте строятся по id. */
export function validateCategoryCreate(body) {
  const fields = validateFields(body);
  if (!('name' in fields)) throw new HttpError(400, 'Обязательное поле: name');
  return { parent_id: null, ...fields };
}

/** Проверяет тело PATCH и возвращает только переданные поля. */
export function validateCategoryPatch(body) {
  const fields = validateFields(body);
  if (Object.keys(fields).length === 0) throw new HttpError(400, 'Не передано ни одного поля');
  return fields;
}

export function parseCategoryId(value) {
  if (!/^\d{1,9}$/.test(String(value))) throw new HttpError(400, 'id должен быть целым числом');
  return Number(value);
}

/**
 * Уникальный slug. Повторяет freeSlug из src/categories.js: та не
 * экспортирована, а править чужой файл ради пяти строк значит поставить
 * запуск сервера в зависимость от того, переживёт ли правка обновление.
 */
async function freeSlug(client, base) {
  const { rows } = await client.query('SELECT slug FROM categories WHERE slug = $1 OR slug LIKE $2', [base, `${base}-%`]);
  const taken = new Set(rows.map((r) => r.slug));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i += 1) if (!taken.has(`${base}-${i}`)) return `${base}-${i}`;
}

async function checkParent(client, parentId) {
  if (parentId === null) return;
  const { rowCount } = await client.query('SELECT 1 FROM categories WHERE id = $1', [parentId]);
  if (rowCount === 0) throw new HttpError(400, 'Раздел, в который переносим, не найден');
}

/**
 * Тёзка в том же разделе — почти всегда опечатка, а не замысел.
 *
 * Сравнение делается здесь, а не запросом: lower() в PostgreSQL зависит от
 * локали базы и при locale=C кириллицу не сворачивает вовсе — «Реле» и «реле»
 * прошли бы как разные. toLowerCase в JS от локали базы не зависит.
 */
async function checkNameFree(client, parentId, name, skipId = null) {
  const { rows } = await client.query(
    'SELECT id, name FROM categories WHERE parent_id IS NOT DISTINCT FROM $1',
    [parentId],
  );
  const wanted = name.toLowerCase();
  const taken = rows.find((r) => r.id !== skipId && cleanText(r.name).toLowerCase() === wanted);
  if (taken) {
    const where = parentId === null ? 'на верхнем уровне' : 'в этом разделе';
    throw new HttpError(409, `«${taken.name}» уже есть ${where}`, { code: 'name_exists', existing: { id: taken.id, name: taken.name } });
  }
}

/** Категория и всё, что под ней: чтобы не перенести раздел внутрь самого себя. */
async function branchIds(client, id) {
  const { rows } = await client.query(
    `WITH RECURSIVE tree AS (
       SELECT id FROM categories WHERE id = $1
       UNION ALL SELECT c.id FROM categories c JOIN tree t ON c.parent_id = t.id
     ) SELECT id FROM tree`,
    [id],
  );
  return new Set(rows.map((r) => r.id));
}

/** Категория в формате GET /categories. */
async function loadCategory(db, id) {
  const { rows } = await db.query(
    `SELECT c.id, c.name, c.slug, c.parent_id,
            count(p.id) FILTER (WHERE p.is_active)::int AS products_count
       FROM categories c LEFT JOIN products p ON p.category_id = c.id
      WHERE c.id = $1 GROUP BY c.id`,
    [id],
  );
  return rows[0] ?? null;
}

export function adminCategoriesRouter(pool) {
  const router = Router();

  // POST /categories  { name, parent_id? }  ->  201 + категория
  router.post('/', async (req, res) => {
    const f = validateCategoryCreate(req.body);

    const id = await withTransaction(pool, async (client) => {
      await checkParent(client, f.parent_id);
      await checkNameFree(client, f.parent_id, f.name);
      try {
        const inserted = await client.query(
          'INSERT INTO categories (name, slug, parent_id) VALUES ($1, $2, $3) RETURNING id',
          [f.name, await freeSlug(client, slugify(f.name)), f.parent_id],
        );
        return inserted.rows[0].id;
      } catch (err) {
        // параллельный запрос успел завести ту же категорию
        if (err.code === '23505') throw new HttpError(409, `Категория «${f.name}» в этом разделе уже есть`, { code: 'name_exists' });
        throw err;
      }
    });

    res.status(201).location(`/categories/${id}`).json(await loadCategory(pool, id));
  });

  // PATCH /categories/:id  { name?, parent_id? }   переименовать или перенести
  router.patch('/:id', async (req, res) => {
    const id = parseCategoryId(req.params.id);
    const patch = validateCategoryPatch(req.body);

    await withTransaction(pool, async (client) => {
      const { rows } = await client.query('SELECT name, parent_id FROM categories WHERE id = $1 FOR UPDATE', [id]);
      if (rows.length === 0) throw new HttpError(404, 'Категория не найдена');
      const current = rows[0];

      const name = patch.name ?? current.name;
      const parentId = 'parent_id' in patch ? patch.parent_id : current.parent_id;
      if (name === current.name && parentId === current.parent_id) return;

      if (parentId !== null && (await branchIds(client, id)).has(parentId)) {
        // иначе ветка замкнётся сама на себя и пропадёт со страницы
        throw new HttpError(400, parentId === id
          ? 'Категория не может лежать сама в себе'
          : 'Раздел нельзя перенести внутрь самого себя');
      }
      await checkParent(client, parentId);
      await checkNameFree(client, parentId, name, id);

      await client.query('UPDATE categories SET name = $2, parent_id = $3 WHERE id = $1', [id, name, parentId]);
    });

    res.json(await loadCategory(pool, id));
  });

  // DELETE /categories/:id   только пустую: удалять молча вместе с
  // содержимым опаснее, чем отказать
  router.delete('/:id', async (req, res) => {
    const id = parseCategoryId(req.params.id);

    await withTransaction(pool, async (client) => {
      const found = await client.query('SELECT 1 FROM categories WHERE id = $1 FOR UPDATE', [id]);
      if (found.rowCount === 0) throw new HttpError(404, 'Категория не найдена');

      const { rows } = await client.query(
        `SELECT (SELECT count(*)::int FROM categories WHERE parent_id = $1) AS children,
                (SELECT count(*)::int FROM products WHERE category_id = $1) AS products`,
        [id],
      );
      const { children, products } = rows[0];
      if (children) throw new HttpError(409, 'Сначала удалите или перенесите подкатегории', { code: 'not_empty', children, products });
      if (products) throw new HttpError(409, `Категория не пуста: товаров внутри — ${products}. Перенесите их в другой раздел`, { code: 'not_empty', children, products });

      await client.query('DELETE FROM categories WHERE id = $1', [id]);
    });

    res.status(204).end();
  });

  return router;
}
