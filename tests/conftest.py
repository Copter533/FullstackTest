from collections import defaultdict
from pathlib import Path

import pytest

from recipes import RecipeParser, load_recipes

GAME_DATA = Path("mc_resources")
requires_game_data = pytest.mark.skipif(not GAME_DATA.exists(), reason="нет файлов игры")


@pytest.fixture(scope="session")
def recipes():
    """Все рецепты игры: предмет → список рецептов (у одного предмета их может быть несколько)."""
    by_result = defaultdict(list)
    for r in load_recipes():
        by_result[r.result].append(r)
    return by_result


@pytest.fixture(scope="session")
def by_file():
    return {r.filename: r for r in load_recipes()}


@pytest.fixture(scope="session")
def parser():
    return RecipeParser("mc_resources/data/tags/item")


def grid(*items):
    """Сетка из 9 клеток: перечисленные предметы, остальное пусто."""
    return list(items) + [None] * (9 - len(items))