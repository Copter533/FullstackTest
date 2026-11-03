import pytest

from recipes import GRAY, GREEN, YELLOW, ShapelessRecipe
from tests.conftest import grid, requires_game_data

pytestmark = requires_game_data
N = None


def one(recipes, result) -> ShapelessRecipe:
    """Единственный бесформенный рецепт предмета."""
    found = [r for r in recipes[result] if isinstance(r, ShapelessRecipe)]
    assert len(found) == 1, f"у {result} бесформенных рецептов: {len(found)}"
    return found[0]


# ---------- Загрузка ----------

def test_fire_charge_structure(recipes):
    charge = one(recipes, "fire_charge")
    assert charge.possible_items == {"gunpowder", "blaze_powder", "coal", "charcoal"}
    assert frozenset({"coal", "charcoal"}) in charge.required_items
    assert charge.items_in_recipe == 3


def test_book_counts(recipes):
    book = one(recipes, "book")
    assert book.counter[frozenset({"paper"})] == 3
    assert book.counter[frozenset({"leather"})] == 1


# ---------- can_craft ----------

@pytest.mark.parametrize("cells", [
    grid("gunpowder", "blaze_powder", "coal"),
    grid("charcoal", "gunpowder", "blaze_powder"),                       # другой вариант и другой порядок
    [N, N, "blaze_powder", N, "charcoal", N, "gunpowder", N, N],         # позиции не важны
])
def test_fire_charge_crafts(recipes, cells):
    assert one(recipes, "fire_charge").can_craft(cells)


@pytest.mark.parametrize("cells", [
    grid("gunpowder", "blaze_powder"),                                   # не хватает угля
    grid("gunpowder", "blaze_powder", "coal", "coal"),                   # лишний уголь
    grid("gunpowder", "blaze_powder", "coal", "charcoal"),               # оба угля — один лишний
    grid("gunpowder", "blaze_powder", "coal", "dirt"),                   # посторонний предмет
    grid("gunpowder", "gunpowder", "coal"),                              # не тот набор при том же количестве
    grid(),                                                              # пусто
])
def test_fire_charge_not_crafts(recipes, cells):
    assert not one(recipes, "fire_charge").can_craft(cells)


def test_book_needs_exact_counts(recipes):
    book = one(recipes, "book")
    assert book.can_craft(grid("paper", "leather", "paper", "paper"))
    assert not book.can_craft(grid("paper", "paper", "leather", "leather"))
    assert not book.can_craft(grid("paper", "paper", "paper", "paper", "leather"))


# ---------- get_colors ----------

def test_colors_exact_win(recipes):
    colors, solved = one(recipes, "fire_charge").check_guess([N, "coal", N, "gunpowder", N, N, N, "blaze_powder", N])
    assert solved
    assert colors == [N, GREEN, N, GREEN, N, N, N, GREEN, N]


def test_colors_alternative_is_green(recipes):
    colors, solved = one(recipes, "fire_charge").check_guess(grid("gunpowder", "blaze_powder", "charcoal"))
    assert solved
    assert colors[:3] == [GREEN, GREEN, GREEN]


def test_colors_both_alternatives(recipes):
    # уголь и древесный уголь закрывают одно требование: один зелёный, второй лишний — жёлтый
    colors, solved = one(recipes, "fire_charge").check_guess(grid("gunpowder", "blaze_powder", "coal", "charcoal"))
    assert not solved
    assert sorted(colors[2:4]) == [YELLOW, GREEN]


def test_colors_extra_copy_is_yellow(recipes):
    colors, solved = one(recipes, "book").check_guess(grid("paper", "paper", "paper", "paper", "leather"))
    assert not solved
    assert colors[:5].count(GREEN) == 4          # 3 бумаги + кожа
    assert colors[:5].count(YELLOW) == 1         # четвёртая бумага лишняя


def test_colors_foreign_item_is_gray(recipes):
    colors, solved = one(recipes, "fire_charge").check_guess(grid("gunpowder", "blaze_powder", "dirt"))
    assert not solved
    assert colors[:3] == [GREEN, GREEN, GRAY]


def test_colors_missing_ingredient(recipes):
    colors, solved = one(recipes, "fire_charge").check_guess(grid("gunpowder", "coal"))
    assert not solved
    assert colors[:2] == [GREEN, GREEN]