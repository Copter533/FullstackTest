import json
import random
import time
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from functools import wraps, lru_cache, cache
from itertools import chain, count
from pathlib import Path
from pprint import pprint
from typing import Iterable, NamedTuple, Generator, Any, Sized, Collection, Callable

Cell = str | None
CellTag = frozenset[str] | None
Grid9 = list[Cell]
Shape = tuple[tuple[Cell, ...], ...] # прямоугольная форма рецепта без пустых краёв
ShapeTags = tuple[tuple[CellTag, ...], ...]
ReqItems = frozenset[frozenset[str]]
PosItems = frozenset[str]

GRAY, YELLOW, GREEN = 0, 1, 2

# Куда ставить небольшой рецепт при показе решения и с какого сдвига начинать сравнение
PREFERRED_OFFSET = { # (ширина, высота) -> (x, y)
    (1, 1): (1, 1),
    (1, 2): (1, 1),
    (2, 2): (0, 1),
    (2, 1): (0, 2),
    (3, 2): (0, 1),
    (2, 3): (1, 0),
}


def omit_namespace(item_id: str, force: bool = False) -> str:
    if ":" not in item_id: return item_id
    namespace, clear_id = item_id.split(":", maxsplit=1)
    return clear_id if (namespace == "minecraft" or force) else item_id


def to_rows(grid: Grid9) -> list[list[Cell]]:
    cells = [item or None for item in grid]
    return [cells[i:i + 3] for i in (0, 3, 6)]


def trim_shape(rows) -> Shape:
    """Обрезает пустые строки и столбцы по краям."""
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]

    while rows and all(c is None for c in rows[0]):
        rows.pop(0)
    while rows and all(c is None for c in rows[-1]):
        rows.pop()
    if not rows:
        return ()

    columns = [list(col) for col in zip(*rows)] # транспонирование
    while columns and all(c is None for c in columns[0]):
        columns.pop(0)
    while columns and all(c is None for c in columns[-1]):
        columns.pop()
    return tuple(zip(*columns))


def mirror(shape: Shape) -> Shape:
    return tuple(tuple(reversed(row)) for row in shape)


def safe_index(matrix, y: int, x: int, default=None):
    max_y = len(matrix)
    if not (0 <= y < max_y): return default
    max_x = len(matrix[y])
    if not (0 <= x < max_x): return default
    return matrix[y][x]


def choose_from_preferred(options: frozenset | set, preferred: Iterable):
    return min(options.intersection(preferred) or options)


def place(shape: ShapeTags, offset: tuple[int, int]) -> list[CellTag]:
    """Кладёт форму в сетку 3 на 3 со сдвигом (x, y), не мутируя саму форму"""
    x0, y0 = offset
    full: Grid9 = [None] * 9
    for y, row in enumerate(shape):
        for x, item in enumerate(row):
            full[(y + y0) * 3 + (x + x0)] = item
    return full


def interned_return(interned_dict: dict):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            return interned_dict.setdefault(result, result)
        return wrapper
    return decorator


class Recipe(ABC):
    # оптимизация кол-во объектов фрозенсетов, интернирование
    interned_ingredients: dict[frozenset[str], frozenset[str]] = {}
    items_in_recipe: int

    def __init__(self, required_items: ReqItems, result: str, filename: str):
        self.result = result
        self.filename = filename
        self.required_items: ReqItems = required_items
        pos_items = frozenset(chain.from_iterable(self.required_items))
        self.possible_items: PosItems = self.interned_ingredients.setdefault(pos_items, pos_items)

        self.sorted_requirements = sorted(self.required_items, key=len)

    @staticmethod
    def _is_full_match(colors: list[int | None], items_in_recipe: int) -> bool:
        filled = [c for c in colors if c is not None]
        return len(filled) == items_in_recipe and all(c == GREEN for c in filled)

    def can_craft(self, grid: Grid9) -> bool:
        """
        Возможно ли скрафтить этот рецепт из такой сетки в верстаке
        """
        return self._is_full_match(self.get_colors(grid), self.items_in_recipe)

    def check_guess(self, grid: Grid9) -> tuple[list[int | None], bool]:
        colors = self.get_colors(grid)  # сетка обрабатывается один раз
        return colors, self._is_full_match(colors, self.items_in_recipe)

    @abstractmethod
    def get_colors(self, grid: Grid9) -> list[int | None]:
        """
        Цвета попытки относительно этого рецепта (как ответа) и флаг победы
        :return: Плоский список цветов + флаг решено или нет
        """

    @abstractmethod
    def get_solution_grid(self, preferred: Iterable[str] = None) -> Grid9:
        """
        Показывает как этот рецепт крафтится в верстаке
        :param preferred: Если доступно сразу несколько предметов (тег) то будут выбраны сначала из prefer
        """

    def match_category(self, item: str | None) -> Generator[frozenset[str] | None]:
        if item is None:
            yield None
            return
        for group in self.sorted_requirements:
            if item in group: yield group

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.result}>"


class ShapedRecipe(Recipe):
    def __init__(self, shape: ShapeTags, result: str, filename: str):
        items = [item for row in shape for item in row if item is not None]
        super().__init__(frozenset(items), result, filename)
        self.shape = trim_shape(shape)
        self.variants: tuple[Shape] = (
            (self.shape,)
            if (mirrored := mirror(self.shape)) == self.shape else
            (self.shape, mirrored)
        )
        self.height = len(self.shape)
        self.width = len(self.shape[0]) if self.shape else 0
        self.items_in_recipe = sum(1 for e in items if e is not None)

    def can_craft(self, grid: Grid9) -> bool:
        items_in_grid = sum(1 for e in grid if e)
        if items_in_grid != self.items_in_recipe: return False
        trimmed = trim_shape(to_rows(grid))

        def check_variant(variant):
            for y, row in enumerate(variant):
                for x, item_group in enumerate(row):
                    item = safe_index(trimmed, y, x)
                    if (item is None) != (item_group is None): return False
                    if item_group and (item not in item_group): return False
            return True

        for variant in self.variants:
            if check_variant(variant): return True
        return False

    def _offsets(self) -> Generator[tuple[int, int]]:
        preferred = PREFERRED_OFFSET.get((self.width, self.height))
        if preferred: yield preferred
        for y in range(3 - self.height + 1):
            for x in range(3 - self.width + 1):
                if preferred == (x, y): continue
                yield x, y

    def _colorize_placement(self, cells: Grid9, placed: list[CellTag]) -> tuple[list[int | None], int]:
        """Цвета для одного положения рецепта. Возвращает цвета и число зелёных"""
        colors: list[int | None] = []
        greens = 0
        for item, expected in zip(cells, placed):
            if item is None:
                colors.append(None)
            elif expected is not None and item in expected:  # клетка рецепта допускает этот предмет
                colors.append(GREEN)
                greens += 1
            elif item in self.possible_items:  # предмет есть в рецепте, но не здесь (или лишний)
                colors.append(YELLOW)
            else:
                colors.append(GRAY)
        return colors, greens

    def get_colors(self, grid: Grid9):
        cells = [item or None for item in grid]

        # Перебираем все положения (сдвиги и зеркало), берём то, где больше всего зелёных.
        # При равенстве остаётся первое - с предпочтительным сдвигом.
        best_colors: list[int | None] = []
        best_greens = -1
        for variant in self.variants:
            for offset in self._offsets():
                colors, greens = self._colorize_placement(cells, place(variant, offset))
                if greens > best_greens:
                    best_colors, best_greens = colors, greens

        return best_colors

    def get_solution_grid(self, preferred: Iterable[str] = None) -> Grid9:
        placed = place(self.shape, next(self._offsets()))
        return [choose_from_preferred(group, preferred) if group else None for group in placed]


class ShapelessRecipe(Recipe):
    def __init__(self, items: list[frozenset[str]], result: str, filename: str):
        super().__init__(frozenset(items), result, filename)
        self.counter = Counter(items)
        self.slots: list[frozenset[str]] = list(self.counter.elements())  # гнездо на каждую клетку
        self.items_in_recipe = len(self.slots)

    def _match(self, items: list[str]) -> dict[int, int]:
        """
        Максимальное паросочетание: индекс предмета -> индекс гнезда,
        алгоритм Куна https://ru.algorithmica.org/cs/matching/kuhn/
        """
        slot_owner: dict[int, int] = {}          # гнездо -> предмет

        def try_place(i: int, visited: set[int]) -> bool:
            for s, allowed in enumerate(self.slots):
                if s in visited or items[i] not in allowed:
                    continue
                visited.add(s)
                # гнездо свободно, или его хозяин может пересесть
                if s not in slot_owner or try_place(slot_owner[s], visited):
                    slot_owner[s] = i
                    return True
            return False

        for i in range(len(items)):
            try_place(i, set())
        return {i: s for s, i in slot_owner.items()}

    def get_colors(self, grid: Grid9):
        cells = [i for i, item in enumerate(grid) if item]      # номера непустых клеток
        matched = self._match([grid[i] for i in cells])         # k-й предмет -> гнездо

        colors: list[int | None] = [None] * len(grid)
        for k, i in enumerate(cells):
            if k in matched:
                colors[i] = GREEN
            elif grid[i] in self.possible_items:
                colors[i] = YELLOW
            else:
                colors[i] = GRAY
        return colors

    def get_solution_grid(self, preferred: Iterable[str] = None) -> Grid9:
        # TODO фиксированный порядок некоторых рецептов (динамит, незерит, бетон)
        items = sorted(choose_from_preferred(group, preferred) for group in self.slots)   # стабильный порядок
        return items + [None] * (9 - len(items))


class RecipeParser:
    tags_dir: str | Path = "mc_resources/data/tags/item"
    failed_parsing: dict[str, dict] = {}

    @staticmethod
    def check_parsing(func):
        @wraps(func) # Сохранит docstring и имя исходной функции
        def checker(*args, **kwargs):
            self, recipe_json, filename = args
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                self.failed_parsing[filename] = {"cause": ex, "data": recipe_json}
                return None
        return checker

    @classmethod
    @interned_return(Recipe.interned_ingredients)
    def parse_ingredient(cls, value: str | list[str]) -> frozenset[str]:
        """
        Парсит ингредиент и возвращает frozenset возможных предметов
        :param value: ID или тег, либо список ID или тегов (можно смешивать)
        :return: frozenset предметов которые подходят под условия
        """
        if isinstance(value, list):
            possibilities = set()
            for entry in value:
                possibilities.update(cls.parse_ingredient(entry))
            return frozenset(possibilities)
        elif isinstance(value, dict):
            return cls.parse_ingredient(value['id'])
        elif not isinstance(value, str):
            raise TypeError(f"Неверный тип параметра для парсинга ингредиента: {value} ({type(value)})")

        if not value.startswith("#"):
            return frozenset((omit_namespace(value),))

        with open(f"mc_resources/data/tags/item/{omit_namespace(value[1:])}.json", "r", encoding="UTF-8") as f:
            tag_json = json.load(f)
        possibilities = set()
        for entry in tag_json['values']:
            possibilities.update(cls.parse_ingredient(entry))
        return frozenset(possibilities)


    @check_parsing
    def parse(self, recipe_json: dict, filename: str) -> Recipe | None:
        """Вернёт рецепт или None, если тип не поддерживается"""
        craft_type = omit_namespace(recipe_json.get("type", ""))
        result_json = recipe_json.get("result") # result - строка
        if not result_json: return None
        result = omit_namespace(result_json.get("id") if isinstance(result_json, dict) else result_json)
        if not result: raise ValueError(f"Недопустим пустой результат: {result}")

        if craft_type == "crafting_shaped":
            key: dict[str, frozenset[str] | None] = {k: self.parse_ingredient(v) for k, v in recipe_json["key"].items()}
            key[" "] = None
            rows = [[key[ch] for ch in line] for line in recipe_json["pattern"]]
            return ShapedRecipe(rows, result, filename)

        if craft_type == "crafting_shapeless":
            items = [self.parse_ingredient(v) for v in recipe_json["ingredients"]]
            return ShapelessRecipe(items, result, filename)
        return None

    @staticmethod
    def print_req_items(v: ReqItems):
        for group in v:
            print("", end=" [ ")
            for item in group:
                print(item, end=" ")
            print("", end=" ] ")
        print()


def load_recipes(recipe_dir: str | Path = "mc_resources/data/recipe") -> list[Recipe]:
    parser = RecipeParser()
    recipes: list[Recipe] = []
    items_crafted: dict[str, list[Recipe]] = defaultdict(list)
    for path in sorted(Path(recipe_dir).glob("*.json")): # sorted стабильный порядок для seed
        with open(path, "r", encoding="UTF-8") as f:
            # print(path)
            recipe = parser.parse(json.load(f), path.name)
        if recipe is not None:
            recipes.append(recipe)
            items_crafted[recipe.result].append(recipe)

    # dyes = parser.parse_ingredient("#dyes")
    # for recipe in recipes:
    #     for item in recipe.possible_items:
    #         if item not in dyes: break
    #     else:
    #         print(recipe, recipe.required_items)

    print("Passed:", len(recipes))
    print("Failed:", len(RecipeParser.failed_parsing))
    for failed, info in RecipeParser.failed_parsing.items():
        pprint(info)
    return recipes


if __name__ == '__main__':
    print("Loading recipies...")
    start_time = time.perf_counter()
    load_recipes()
    print(f"done in {(time.perf_counter() - start_time):.3f}")

