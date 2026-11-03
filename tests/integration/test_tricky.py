"""Каверзные рецепты без файлов игры: альтернативы, пересекающиеся группы, похожие id.

Все рецепты собираются из JSON прямо в тесте, поэтому тесты быстрые и не зависят от mc_resources.
"""
from itertools import permutations

import pytest

from recipes import RecipeParser, GREEN, YELLOW, GRAY

parser = RecipeParser()


def shapeless(*ingredients):
    recipe = parser.parse(
        {"type": "minecraft:crafting_shapeless", "ingredients": list(ingredients), "result": {"id": "minecraft:test"}},
        "test_shapeless.json",
    )
    assert recipe is not None, RecipeParser.failed_parsing.get("test_shapeless.json")
    return recipe


def shaped(pattern, key):
    recipe = parser.parse(
        {"type": "minecraft:crafting_shaped", "pattern": pattern, "key": key, "result": {"id": "minecraft:test"}},
        "test_shaped.json",
    )
    assert recipe is not None, RecipeParser.failed_parsing.get("test_shaped.json")
    return recipe


def g(*items):
    """Сетка 3×3: перечисленные предметы по порядку, остальное пусто."""
    return list(items) + [None] * (9 - len(items))


def colors_of(recipe, grid):
    # Переходник, пока get_colors возвращает (цвета, флаг). Когда останутся только цвета — можно убрать.
    result = recipe.get_colors(grid)
    return result[0] if isinstance(result, tuple) else result


# ---------- Бесформенные: одна группа-альтернатива несколько раз ----------

COAL_X2 = (["coal", "charcoal"], ["coal", "charcoal"], "gunpowder")


@pytest.mark.parametrize("items", [
    ("coal", "charcoal", "gunpowder"),          # смешать альтернативы в одной группе можно
    ("coal", "coal", "gunpowder"),
    ("charcoal", "charcoal", "gunpowder"),
    ("gunpowder", "charcoal", "coal"),
])
def test_mixed_alternatives_can_craft(items):
    assert shapeless(*COAL_X2).can_craft(g(*items))


def test_mixed_alternatives_all_green():
    assert colors_of(shapeless(*COAL_X2), g("coal", "charcoal", "gunpowder"))[:3] == [GREEN] * 3


def test_extra_alternative_is_yellow():
    # Нужен один уголь любого вида, положили оба: один зелёный, второй лишний
    colors = colors_of(shapeless(["coal", "charcoal"], "gunpowder"), g("coal", "charcoal", "gunpowder"))
    assert colors.count(GREEN) == 2
    assert colors.count(YELLOW) == 1


# ---------- Бесформенные: группы пересекаются ----------

OVERLAP = (["apple", "stick"], "apple")   # «яблоко или палка» + «яблоко»


@pytest.mark.parametrize("items, expected", [
    (("apple", "apple"), True),               # яблоко подходит в обе группы
    (("apple", "stick"), True),
    (("stick", "apple"), True),
    (("stick", "stick"), False),              # второй группе нужна именно яблоко
    (("apple",), False),
    (("apple", "apple", "stick"), False),
])
def test_overlap_can_craft(items, expected):
    assert shapeless(*OVERLAP).can_craft(g(*items)) is expected


@pytest.mark.parametrize("items", list(permutations(["apple", "stick"])) + [("apple", "apple")])
def test_overlap_all_green_in_any_order(items):
    # Жадный подход может отдать яблоко «широкой» группе, и тогда палке не останется места
    colors = colors_of(shapeless(*OVERLAP), g(*items))
    assert colors[:2] == [GREEN, GREEN]


def test_overlap_greens_do_not_depend_on_cell_order():
    recipe = shapeless(["a", "b"], ["b", "c"], "c")
    counts = {
        perm: colors_of(recipe, g(*perm)).count(GREEN)
        for perm in permutations(["a", "b", "c"])
    }
    assert set(counts.values()) == {3}, counts


# ---------- Бесформенные: решение ----------

@pytest.mark.parametrize("ingredients", [
    ("leather", "paper", "paper", "paper"),
    COAL_X2,
    OVERLAP,
])
def test_shapeless_solution_is_items_and_craftable(ingredients):
    recipe = shapeless(*ingredients)
    solution = recipe.get_solution_grid()
    assert len(solution) == 9
    assert all(cell is None or isinstance(cell, str) for cell in solution), solution
    assert recipe.can_craft(solution)


# ---------- С формой: альтернативы в клетке ----------

PLANKS = ["oak_planks", "birch_planks", "spruce_planks"]


@pytest.mark.parametrize("plank", PLANKS)
def test_shaped_any_alternative_is_green(plank):
    recipe = shaped(["#", "#"], {"#": PLANKS})          # как палка: 1×2
    guess = recipe.get_solution_grid()
    guess = [plank if cell else None for cell in guess]  # те же клетки, другие доски
    assert recipe.can_craft(guess)
    assert colors_of(recipe, guess).count(GREEN) == 2


@pytest.mark.parametrize("plank", PLANKS)
def test_shaped_mixed_alternatives_green(plank):
    recipe = shaped(["##", "##"], {"#": PLANKS})        # как верстак: доски можно смешивать
    guess = g(None, None, None, "oak_planks", plank, None, "spruce_planks", "birch_planks")
    assert recipe.can_craft(guess)
    assert colors_of(recipe, guess).count(GREEN) == 4


# ---------- С формой: похожие id не должны совпадать ----------

@pytest.mark.parametrize("expected, guessed", [
    ("cobblestone", "stone"),
    ("glass_pane", "glass"),
    ("oak_planks", "planks"),
])
def test_shaped_substring_is_not_green(expected, guessed):
    recipe = shaped(["#"], {"#": expected})
    guess = recipe.get_solution_grid()
    guess = [guessed if cell else None for cell in guess]
    assert colors_of(recipe, guess) == [None if cell is None else GRAY for cell in guess]
    assert not recipe.can_craft(guess)


def test_shaped_solution_is_items_and_craftable():
    recipe = shaped(["#S#", " S "], {"#": PLANKS, "S": "stick"})
    solution = recipe.get_solution_grid()
    assert all(cell is None or isinstance(cell, str) for cell in solution), solution
    assert recipe.can_craft(solution)