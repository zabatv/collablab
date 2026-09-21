-- Завести подкатегорию прямо в базе каталога.
--
-- Зачем файл, а не кнопка в админке: в их API нет эндпоинтов на создание
-- категорий — только чтение (docs/api-contract.md, «Сервер админки»).
-- Пока разработчик их не добавит, категория заводится так. Таблица у них
-- обычная: id, name, slug, parent_id, — поэтому вложенность любой глубины
-- она держит, и сайт её показывает.
--
-- На машине с базой:
--
--     psql -U shop -d shop -f add-category.sql
--
-- Перед запуском поменять две строки ниже, больше ничего. Если что-то не
-- так — не найден раздел, нашлось два с таким именем, такая подкатегория
-- уже есть — скрипт скажет словами и не применит ничего.

-- Куда заводить. Обычно хватает имени раздела; если разделов с таким
-- именем в базе несколько, скрипт об этом скажет — тогда впишите id
-- нужного в parent_id (его видно в списке внизу прошлого запуска или в
-- адресе страницы раздела на сайте: catalog.html?category=24).
\set parent 'Реле давления'
\set parent_id 0

-- Что заводить
\set name 'Малогабаритные'

-- Кириллица в psql под Windows иначе читается набором вопросов
\encoding UTF8
SET client_encoding TO 'UTF8';

BEGIN;

-- Внутрь блока переменные psql не попадают: подстановка не заходит в
-- $$ ... $$. Поэтому они кладутся в настройки сеанса, откуда блок их и берёт.
SET shop.parent = :'parent';
SET shop.parent_id = :'parent_id';
SET shop.name = :'name';

DO $$
DECLARE
  want_parent text    := btrim(current_setting('shop.parent'));
  want_id     integer := current_setting('shop.parent_id')::integer;
  want_name   text    := btrim(current_setting('shop.name'));
  v_parent    integer;
  found       integer;
  v_new       integer;
BEGIN
  IF want_name = '' THEN
    RAISE EXCEPTION 'Название новой подкатегории пустое';
  END IF;

  IF want_id > 0 THEN
    -- Раздел задан прямо: имя не проверяется
    SELECT id INTO v_parent FROM categories WHERE id = want_id;
    IF v_parent IS NULL THEN
      RAISE EXCEPTION 'Раздела с id % в базе нет', want_id;
    END IF;
    SELECT name INTO want_parent FROM categories WHERE id = v_parent;
  ELSE
    -- Обычный случай: раздел ищется по имени, id человеку неоткуда взять
    SELECT count(*) INTO found FROM categories WHERE btrim(name) = want_parent;

    IF found = 0 THEN
      RAISE EXCEPTION 'Раздел «%» не найден. Имя должно совпадать с тем, что на сайте, буква в букву', want_parent;
    END IF;

    IF found > 1 THEN
      RAISE EXCEPTION 'Разделов с именем «%» несколько. Впишите id нужного в parent_id вверху файла — id видно в адресе страницы раздела на сайте', want_parent;
    END IF;

    SELECT id INTO v_parent FROM categories WHERE btrim(name) = want_parent;
  END IF;

  IF EXISTS (SELECT 1 FROM categories c
              WHERE c.parent_id = v_parent AND btrim(c.name) = want_name) THEN
    RAISE EXCEPTION 'В разделе «%» уже есть «%»', want_parent, want_name;
  END IF;

  -- slug таблице нужен уникальным; адреса на сайте строятся по id, так что
  -- достаточно, чтобы он ни с чем не совпал
  INSERT INTO categories (name, slug, parent_id)
  VALUES (want_name, 'tmp-' || md5(random()::text || clock_timestamp()::text), v_parent)
  RETURNING id INTO v_new;

  UPDATE categories SET slug = 'cat-' || v_new WHERE id = v_new;

  RAISE NOTICE 'Готово: «%» заведена внутри «%», id = %', want_name, want_parent, v_new;
END $$;

COMMIT;

-- Что теперь лежит в этом разделе
SELECT c.id, c.name, p.id AS "id раздела", p.name AS "раздел"
  FROM categories c
  JOIN categories p ON p.id = c.parent_id
 WHERE (:parent_id > 0 AND p.id = :parent_id)
    OR (:parent_id = 0 AND btrim(p.name) = btrim(:'parent'))
 ORDER BY c.name;
