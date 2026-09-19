"""Split the flat catalogue into subcategories.

The supplier's names carry the type of part in their opening words, so the
products can be sorted into subsections the same way they were sorted into
sections. Ordering and naming are the admin's afterwards — this only gives
the tree something to start from.

    python3 scripts/build_subcategories.py            # показать, что получится
    python3 scripts/build_subcategories.py --apply    # записать в базу

A section with fewer products than SPLIT_THRESHOLD is left alone: two
subcategories over nine products help nobody.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

os.environ.setdefault('ADMIN_USERNAME', 'script')
os.environ.setdefault('ADMIN_PASSWORD', 'script')

SPLIT_THRESHOLD = 15

# Each section lists (subcategory name, words that must all be in the product
# name). The first rule that matches wins, so narrow rules come first.
RULES = {
    'Фитинги': [
        ('Тройники Y-образные', ('тройник', 'y-образный')),
        ('Тройники T-образные', ('тройник',)),
        ('Угловые', ('угловой',)),
        ('Заглушки', ('заглушка',)),
        ('С регулировкой расхода', ('регулировкой расхода',)),
        ('Быстросъёмные', ('быстросем',)),
        ('Прямые', ('прямой',)),
    ],
    'Крепление пневмоцилиндров': [
        ('Вилки на шток', ('вилка на шток',)),
        ('Наконечники сферические', ('сферический наконечник',)),
        ('Наконечники шарнирные', ('шарнирный наконечник',)),
        ('Монтажные скобы', ('монтажная скоба',)),
        ('Монтажные стойки', ('монтажная стойка',)),
        ('Монтажные фланцы', ('монтажный фланец',)),
    ],
    'Пневмораспределители': [
        ('Монтажные плиты', ('монтажная плита',)),
        ('Заглушки для плит', ('заглушка',)),
        ('Распределители 3/2', ('3/2',)),
        ('Распределители 5/2', ('5/2',)),
        ('Распределители 5/3', ('5/3',)),
    ],
    'Реле и таймеры': [
        ('Реле твердотельные', ('твердотельное реле',)),
        ('Радиаторы и колодки', ('радиатор',)),
        ('Радиаторы и колодки', ('колодка',)),
        ('Реле времени и таймеры', ('реле времени',)),
        ('Реле времени и таймеры', ('таймер',)),
        ('Реле времени и таймеры', ('модульное рв',)),
        ('Реле промежуточные', ('промежуточное',)),
    ],
    'Клапаны': [
        ('Соленоидные', ('соленоидный',)),
        ('Обратные', ('обратный клапан',)),
        ('Перекидные', ('перекидной',)),
        ('Сдвижные', ('сдвижной',)),
    ],
    'Датчики и приборы контроля': [
        ('Индуктивные датчики', ('индуктивный датчик',)),
        ('Реле давления', ('реле давления',)),
        ('Реле дифференциального давления', ('диф',)),
        ('Реле контроля напряжения', ('реле контроля',)),
        ('Регуляторы уровня', ('уровня жидкости',)),
        ('Преобразователи сигналов', ('преобразователь сигналов',)),
    ],
    'Пневмотрубки': [
        ('Полиуретановые чёрные', ('чёрн',)),
        ('Полиуретановые синие', ('син',)),
    ],
    'Контроллеры и терморегуляторы': [
        ('ПИД-регуляторы', ('пид-регулятор',)),
        ('Программируемые контроллеры', ('плк',)),
        ('Терморегуляторы и термостаты', ('терморегулятор',)),
        ('Терморегуляторы и термостаты', ('термостат',)),
        ('Терморегуляторы и термостаты', ('реле температуры',)),
    ],
    # The block comes first: its name contains «фильтр-регулятор» too
    'Подготовка воздуха': [
        ('Блоки подготовки воздуха', ('блок подготовки',)),
        ('Фильтры-регуляторы', ('фильтр-регулятор',)),
    ],
}

# Pneumatic cylinders differ by bore, which is what a buyer picks by
CYLINDER = re.compile(r'D\s*=\s*(\d+)\s*мм', re.IGNORECASE)


def subcategory_for(product, section):
    name = (product.name or '').lower()

    if section == 'Пневмоцилиндры':
        found = CYLINDER.search(product.name or '')
        return f'Диаметр {found.group(1)} мм' if found else None

    for title, words in RULES.get(section, []):
        if all(word in name for word in words):
            return title

    return None


def plan(session, Category, Product):
    """What would be created, without touching anything."""
    sections = (session.query(Category)
                .filter(Category.parent_id.is_(None)).all())

    result = []
    for section in sections:
        products = list(section.products)

        groups, leftover = {}, []
        for product in products:
            title = subcategory_for(product, section.name)
            if title:
                groups.setdefault(title, []).append(product)
            else:
                leftover.append(product)

        # In a large section a subcategory holding one product is noise and
        # goes back to the section. In a small one it is the whole point:
        # a section has to show subcategories, not a heap of products.
        smallest = 2 if len(products) >= SPLIT_THRESHOLD else 1
        for title in [t for t, items in groups.items() if len(items) < smallest]:
            leftover.extend(groups.pop(title))

        result.append((section, groups, leftover))

    return result


def main():
    apply_changes = '--apply' in sys.argv

    import app as backend
    from models import Category, Product

    with backend.app.app_context():
        session = backend.db.session
        layout = plan(session, Category, Product)

        for section, groups, leftover in layout:
            total = sum(len(items) for items in groups.values()) + len(leftover)
            print(f'\n{section.name} — {total}')
            if not groups:
                print('   без подкатегорий')
                continue

            for order, (title, items) in enumerate(sorted(groups.items(),
                                                          key=lambda pair: -len(pair[1])), 1):
                print(f'   {title}: {len(items)}')

                if apply_changes:
                    existing = (session.query(Category)
                                .filter_by(name=title, parent_id=section.id).first())
                    child = existing or Category(
                        name=title,
                        slug=backend.make_slug(f'{section.name} {title}'),
                        parent_id=section.id,
                    )
                    child.sort_order = order * 10
                    session.add(child)
                    session.flush()

                    for product in items:
                        product.category_id = child.id

            if leftover:
                print(f'   (остаётся в разделе: {len(leftover)})')

        if apply_changes:
            session.flush()

            # Biggest section first — by everything in the branch, not by what
            # happens to sit directly in the section. Counting the latter put
            # the sections split on an earlier run at the bottom, because
            # splitting had left them with nothing of their own.
            for order, section in enumerate(
                    sorted((row[0] for row in layout),
                           key=lambda node: -node.total_product_count()), 1):
                section.sort_order = order * 10

            session.commit()
            print('\nЗаписано в базу.')
        else:
            print('\nЭто предпросмотр. Чтобы записать: --apply')


if __name__ == '__main__':
    main()
