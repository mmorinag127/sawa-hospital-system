"""Explicit menu-line settings, without adopting unconfirmed shared values."""
from types import SimpleNamespace


FIELDS = ("category", "stock_unit", "order_unit", "conversion_rate")


def unconfirmed_fields(payload):
    return [field for field in FIELDS if payload.get(field) is None]


def for_row(row):
    pending = set(row.unconfirmed_ingredient_fields or [])
    ingredient = row.ingredient
    return SimpleNamespace(id=ingredient.id, name=ingredient.name, **{
        field: None if field in pending else getattr(ingredient, field) for field in FIELDS
    })


def for_menus(menus):
    """An inventory balance cannot use a setting unconfirmed on any demand row."""
    result = {}
    for menu in menus:
        for row in menu.menu_ingredients:
            value = for_row(row)
            if value.id not in result:
                result[value.id] = value
            else:
                for field in FIELDS:
                    if getattr(value, field) is None:
                        setattr(result[value.id], field, None)
    return result
