"""Собрать дерево обратно, если оно сплющилось.

Импорт Excel до сих пор не понимал вложенности: в столбце «Категория» стояло
имя подкатегории, и каждое такое имя заводилось отдельным разделом. После
такого импорта на главной вместо «Фитингов» и «Пневмоцилиндров» выстраивались
«Заглушки», «Угловые», «Диаметр 100 мм» — по полосе на подкатегорию.

Скрипт ставит их на место: каждая подкатегория уходит под свой раздел, а если
такая подкатегория уже есть внутри раздела, товары из плоского двойника
переезжают в неё, и двойник удаляется.

    python3 scripts/repair_tree.py            # показать, что будет сделано
    python3 scripts/repair_tree.py --apply    # записать в базу

Запускать можно сколько угодно раз: на уже правильном дереве он ничего не
меняет.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

os.environ.setdefault('ADMIN_USERNAME', 'script')
os.environ.setdefault('ADMIN_PASSWORD', 'script')

from build_subcategories import RULES  # noqa: E402  (нужен путь выше)

# Пневмоцилиндры разложены по диаметру, а не по списку правил
CYLINDER_SUBCATEGORY = re.compile(r'^Диаметр\s+\d+\s*мм$', re.IGNORECASE)


def section_for_subcategory():
    """Имя подкатегории → имя раздела, в котором ей место."""
    owner = {}
    for section, rules in RULES.items():
        for title, _words in rules:
            owner.setdefault(title, section)
    return owner


def plan(session, Category):
    """Что нужно поправить. Ничего не трогает."""
    owner = section_for_subcategory()
    moves = []

    flat = (session.query(Category)
            .filter(Category.parent_id.is_(None))
            .order_by(Category.name).all())

    for category in flat:
        section_name = owner.get(category.name)
        if not section_name and CYLINDER_SUBCATEGORY.match(category.name or ''):
            section_name = 'Пневмоцилиндры'

        if not section_name:
            continue  # это настоящий раздел, его не трогаем

        # Внутри раздела уже может лежать такая же подкатегория — тогда
        # плоский двойник не переносится, а вливается в неё
        section = (session.query(Category)
                   .filter_by(name=section_name, parent_id=None).first())
        twin = None
        if section:
            twin = next((child for child in section.children
                         if child.name == category.name and child.id != category.id), None)

        moves.append((category, section_name, twin))

    return moves


def main():
    apply_changes = '--apply' in sys.argv

    import app as backend
    from models import Category

    with backend.app.app_context():
        session = backend.db.session
        moves = plan(session, Category)

        if not moves:
            print('Дерево в порядке — плоских подкатегорий нет.')
            return

        for category, section_name, twin in moves:
            count = len(category.products)
            if twin:
                print(f'{category.name}: {count} товаров → в существующую '
                      f'«{section_name} / {category.name}», пустой двойник удалить')
            else:
                print(f'{category.name}: {count} товаров → под «{section_name}»')

            if not apply_changes:
                continue

            section = (session.query(Category)
                       .filter_by(name=section_name, parent_id=None).first())
            if not section:
                section = Category(
                    name=section_name,
                    slug=backend.make_slug(section_name),
                    sort_order=backend.next_sort_order(None),
                )
                session.add(section)
                session.flush()
                print(f'   создан раздел «{section_name}»')

            if twin:
                for product in list(category.products):
                    product.category_id = twin.id
                session.flush()
                session.delete(category)
            else:
                category.parent_id = section.id
                category.sort_order = backend.next_sort_order(section.id)

        if not apply_changes:
            print('\nЭто предпросмотр. Чтобы записать: --apply')
            return

        session.flush()

        # Разделы по величине ветки, а не по тому, что лежит прямо в них
        sections = (session.query(Category)
                    .filter(Category.parent_id.is_(None)).all())
        for order, section in enumerate(
                sorted(sections, key=lambda node: -node.total_product_count()), 1):
            section.sort_order = order * 10

        session.commit()
        print('\nЗаписано в базу.')


if __name__ == '__main__':
    main()
