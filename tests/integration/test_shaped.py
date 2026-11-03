import pytest

from recipes import GRAY, GREEN, YELLOW, ShapedRecipe
from tests.conftest import requires_game_data

pytestmark = requires_game_data
N = None


@pytest.fixture
def recipe(by_file, request):
    """Рецепт по имени файла: @pytest.mark.parametrize("recipe", ["torch.json"], indirect=True)."""
    r = by_file[request.param]
    assert isinstance(r, ShapedRecipe)
    return r


# ---------- Загрузка ----------

def test_torch_structure(by_file):
    torch = by_file["torch.json"]
    assert torch.possible_items == {"coal", "charcoal", "stick"}
    assert frozenset({"coal", "charcoal"}) in torch.required_items


def test_stick_any_planks(by_file):
    planks = next(r for r in by_file["stick.json"].required_items)
    assert {"oak_planks", "spruce_planks", "cherry_planks"} <= planks     # тег раскрыт целиком


# ---------- can_craft: сдвиги ----------

@pytest.mark.parametrize("cells", [
    ["coal", N, N, "stick", N, N, N, N, N],              # левый верхний угол
    [N, N, N, N, "coal", N, N, "stick", N],              # по центру внизу
    [N, N, "charcoal", N, N, "stick", N, N, N],          # правый столбец, другой вариант угля
])
def test_torch_any_position(by_file, cells):
    assert by_file["torch.json"].can_craft(cells)


@pytest.mark.parametrize("cells", [
    ["coal", "stick", N, N, N, N, N, N, N],              # горизонтально
    ["stick", N, N, "coal", N, N, N, N, N],              # перевёрнут
    ["coal", N, N, "stick", N, N, "stick", N, N],        # лишняя палка
    ["coal", N, N, "stick", N, N, N, N, "dirt"],         # посторонний предмет в стороне
    [N] * 9,
])
def test_torch_wrong(by_file, cells):
    assert not by_file["torch.json"].can_craft(cells)


# ---------- can_craft: зеркало ----------

AXE = ["iron_ingot", "iron_ingot", N,
       "iron_ingot", "stick", N,
       N, "stick", N]
AXE_MIRRORED = [N, "iron_ingot", "iron_ingot",
                N, "stick", "iron_ingot",
                N, "stick", N]


def test_axe_and_mirror(by_file):
    axe = by_file["iron_axe.json"]
    assert axe.can_craft(AXE)
    assert axe.can_craft(AXE_MIRRORED)


def test_axe_upside_down_is_wrong(by_file):
    upside_down = AXE[6:] + AXE[3:6] + AXE[:3]           # строки в обратном порядке
    assert not by_file["iron_axe.json"].can_craft(upside_down)


# ---------- can_craft: теги по клеткам ----------

def test_chest_mixed_planks(by_file):
    # каждая клетка проверяется отдельно, так что доски можно смешивать — как в игре
    mixed = ["oak_planks", "spruce_planks", "birch_planks",
             "oak_planks", N, "oak_planks",
             "cherry_planks", "oak_planks", "acacia_planks"]
    assert by_file["chest.json"].can_craft(mixed)


def test_chest_hole_must_be_empty(by_file):
    assert not by_file["chest.json"].can_craft(["oak_planks"] * 9)


def test_ladder_symmetric(by_file):
    ladder = ["stick", N, "stick",
              "stick", "stick", "stick",
              "stick", N, "stick"]
    assert by_file["ladder.json"].can_craft(ladder)


# ---------- get_colors ----------

def test_colors_exact_win(by_file):
    colors, solved = by_file["torch.json"].check_guess([N, "coal", N, N, "stick", N, N, N, N])
    assert solved
    assert colors == [N, GREEN, N, N, GREEN, N, N, N, N]


def test_colors_alternative_is_green(by_file):
    colors, solved = by_file["torch.json"].check_guess([N, "charcoal", N, N, "stick", N, N, N, N])
    assert solved
    assert colors[1] == GREEN


def test_colors_swapped_takes_best_alignment(by_file):
    # уголь совпадает с факелом, сдвинутым вниз, — зелёный; палка в другом месте — жёлтая
    colors, solved = by_file["torch.json"].check_guess([N, "stick", N, N, "coal", N, N, N, N])
    assert not solved
    assert colors[1] == YELLOW and colors[4] == GREEN


def test_colors_extra_items(by_file):
    colors, solved = by_file["golden_boots.json"].check_guess(["gold_ingot"] * 9)
    assert not solved
    assert colors.count(GREEN) == 4
    assert colors.count(YELLOW) == 5                     # лишние — жёлтые, по правилу игры


def test_colors_foreign_item_is_gray(by_file):
    colors, solved = by_file["torch.json"].check_guess([N, "coal", N, N, "stick", N, N, "dirt", N])
    assert not solved
    assert colors[7] == GRAY


def test_colors_mirror_is_win(by_file):
    colors, solved = by_file["iron_axe.json"].check_guess(AXE_MIRRORED)
    assert solved
    assert all(c in (N, GREEN) for c in colors)


def test_colors_mixed_planks_win(by_file):
    mixed = ["oak_planks", "spruce_planks", "birch_planks",
             "oak_planks", N, "oak_planks",
             "cherry_planks", "oak_planks", "acacia_planks"]
    colors, solved = by_file["chest.json"].check_guess(mixed)
    assert solved


# ---------- get_solution_grid ----------

def test_solution_has_nine_cells_and_real_items(by_file):
    for name in ["torch.json", "iron_axe.json", "chest.json", "golden_boots.json"]:
        recipe = by_file[name]
        solution = recipe.get_solution_grid()
        assert len(solution) == 9
        assert all(item is None or item in recipe.possible_items for item in solution)
        assert recipe.can_craft(solution) # показанное решение действительно крафтится