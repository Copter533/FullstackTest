"""Прототип генерации головоломок для Craftle.

Идея: ответ выбирается первым, палитра растёт целыми рецептами-приманками,
каждое добавление проверяется симуляцией двух игроков:
  * «умный» (solve_steps) — знает все рецепты и выбирает самую информативную попытку.
    Гарантирует, что игра вообще честная;
  * «обычный» (play_odds) — пробует случайный рецепт из тех, что ещё не противоречат подсказкам.
    Даёт вероятность победы и то, в какие ветки игрок будет заходить. По ней и настраивается сложность.

Требует от recipes.py:
    load_recipes(), Recipe.possible_items, Recipe.required_items, Recipe.result, Recipe.filename,
    Recipe.items_in_recipe, Recipe.get_colors(grid) -> list, Recipe.get_solution_grid(preferred=...)
"""
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from recipes import Recipe, load_recipes

COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray",
          "cyan", "purple", "blue", "brown", "green", "red", "black"]
WOODTYPES = ["dark_oak", "pale_oak", "oak", "spruce", "birch", "jungle", "acacia", "crimson", "warped",
             "mangrove", "bamboo", "cherry", "poplar"]
COPPER_STAGES = ["waxed", "exposed", "weathered", "oxidized"]

# Длинные раньше коротких: иначе "oak_" срежется у "dark_oak_planks" раньше, чем "dark_oak_"
_VARIANT_PREFIXES = sorted(
    [f"{c}_" for c in COLORS] + [f"{w}_" for w in WOODTYPES] + [f"{s}_" for s in COPPER_STAGES] + ["stripped_"],
    key=len, reverse=True,
)


# ---------- Граф крафта (поверх словарей, без своих узлов) ----------

class CraftGraph:
    def __init__(self, recipes: list[Recipe]):
        self.recipes = recipes
        self._uses: dict[str, list[Recipe]] = defaultdict(list)      # предмет -> рецепты, где он нужен
        self._made_by: dict[str, list[Recipe]] = defaultdict(list)   # предмет -> рецепты, которые его дают
        for recipe in recipes:
            self._made_by[recipe.result].append(recipe)
            for item in recipe.possible_items:
                self._uses[item].append(recipe)

    def uses(self, item: str) -> list[Recipe]:
        return self._uses.get(item, [])

    def made_by(self, item: str) -> list[Recipe]:
        return self._made_by.get(item, [])

    def craftable(self, palette: frozenset[str]) -> list[Recipe]:
        """Все рецепты, которые можно собрать из палитры за один шаг — пространство гипотез игрока."""
        candidates = {id(r): r for item in palette for r in self.uses(item)}
        return sorted(
            (r for r in candidates.values() if all(group & palette for group in r.required_items)),
            key=lambda r: r.filename,                      # стабильный порядок для seed
        )


# ---------- Что считать «нишевым» рецептом ----------

def is_dye(item: str) -> bool:
    return item.endswith("_dye")


def is_niche(recipe: Recipe) -> bool:
    """Рецепты, которые обычный игрок вряд ли помнит. Пока — смешивание красителей между собой."""
    return is_dye(recipe.result) and all(all(is_dye(i) for i in group) for group in recipe.required_items)


def family(item: str) -> str:
    """
    Подвиды одного предмета -> общее имя: orange_wool_slab -> wool_slab, birch_planks -> planks,
    weathered_cut_copper -> cut_copper, stripped_oak_log -> log, red_dye -> dye.
    Срезаем префиксы цвета, породы дерева и стадии окисления, пока они есть (waxed_weathered_ — два подряд).
    """
    stripped = True
    while stripped:
        stripped = False
        for prefix in _VARIANT_PREFIXES:
            if item.startswith(prefix) and len(item) > len(prefix):
                item = item[len(prefix):]
                stripped = True
                break
    return item


# ---------- Оракул: цвета с кэшем ----------

class Oracle:
    """
    Цвета попытки относительно ответа, сразу в виде номера картинки (одинаковые цвета — одинаковый номер).
    Кэш по (номер сетки попытки, id ответа): ключ из двух чисел хешируется намного быстрее кортежа из 9 строк.
    Сетка однозначно задаёт попытку, поэтому номер сетки годится в ключ.
    """

    def __init__(self):
        self._grid_ids: dict[tuple, int] = {}
        self._color_ids: dict[tuple, int] = {}
        self._cache: dict[tuple[int, int], int] = {}

    def grid_id(self, grid: tuple) -> int:
        return self._grid_ids.setdefault(grid, len(self._grid_ids))

    def color_id(self, grid: tuple, grid_id: int, answer: Recipe) -> int:
        key = (grid_id, id(answer))
        result = self._cache.get(key)
        if result is None:
            colors = tuple(answer.get_colors(list(grid)))
            result = self._cache[key] = self._color_ids.setdefault(colors, len(self._color_ids))
        return result


def guess_grid(recipe: Recipe, palette: frozenset[str]) -> tuple:
    """Как игрок соберёт этот рецепт из своей палитры."""
    return tuple(recipe.get_solution_grid(preferred=palette))


def color_matrix(candidates: list[Recipe], palette: frozenset[str], oracle: Oracle) -> list[list[int]]:
    """matrix[g][a] — номер цветовой картинки, которую увидит игрок, если попробует g, а загадан a."""
    cache = oracle._cache                     # горячий цикл: n² обращений, обходимся без вызова метода на попадании
    answer_ids = [id(a) for a in candidates]
    matrix = []
    for g in candidates:
        grid = guess_grid(g, palette)
        gid = oracle.grid_id(grid)
        row = [cache.get((gid, aid)) for aid in answer_ids]
        for i, value in enumerate(row):
            if value is None:
                row[i] = oracle.color_id(grid, gid, candidates[i])
        matrix.append(row)
    return matrix


# ---------- «Умный» игрок: честна ли игра ----------

def solve_steps(matrix: list[list[int]], answer: int, limit: int = 12) -> int:
    """
    Сколько попыток нужно жадному «умному игроку», если загадан answer (индекс в матрице).
    Каждый ход: попытка, после которой самая большая группа оставшихся кандидатов минимальна.
    """
    n = len(matrix)
    alive = list(range(n))
    for step in range(1, limit + 1):
        if len(alive) == 1:
            return step                                   # остался один — его и называем

        def worst_bucket(g: int) -> tuple:
            row = matrix[g]
            buckets: dict[int, int] = defaultdict(int)
            for a in alive:
                buckets[row[a]] += 1
            # при равенстве лучше попытка, которая сама может оказаться ответом
            return max(buckets.values()), g not in alive

        g = min(range(n), key=worst_bucket)
        if g == answer:
            return step
        seen = matrix[g][answer]
        alive = [a for a in alive if a != g and matrix[g][a] == seen]
    return limit + 1


# ---------- «Обычный» игрок: вероятность победы и ветки ----------

@dataclass
class Odds:
    win: float                                  # вероятность угадать за отведённые попытки
    by_turn: list[float]                        # вероятность угадать именно на k-й попытке
    visits: list[float]                         # visits[i] — вероятность, что игрок попробует кандидата i
    expected_turns: float                       # среднее число попыток среди побед


def play_odds(matrix: list[list[int]], answer: int, attempts: int) -> Odds:
    """
    Модель игрока: каждый ход пробует случайный рецепт из тех, что ещё согласуются со всеми подсказками.
    Считается точно, а не Монте-Карло: «вероятностная масса» течёт по состояниям
    (множество оставшихся кандидатов), одинаковые состояния склеиваются.
    """
    n = len(matrix)
    # После попытки g останутся те a, что дали бы те же цвета, что и настоящий ответ.
    # Множества храним битовыми масками (бит i = кандидат i жив): пересечение — одна операция &.
    consistent = [0] * n
    for g in range(n):
        row, seen = matrix[g], matrix[g][answer]
        for a in range(n):
            if a != g and row[a] == seen:
                consistent[g] |= 1 << a

    states: dict[int, float] = {(1 << n) - 1: 1.0}
    by_turn = []
    visits = [0.0] * n

    for _ in range(attempts):
        next_states: dict[int, float] = defaultdict(float)
        won = 0.0
        for alive, mass in states.items():
            share = mass / alive.bit_count()
            rest_bits = alive
            while rest_bits:                                # перебор установленных битов
                low = rest_bits & -rest_bits
                rest_bits ^= low
                g = low.bit_length() - 1
                visits[g] += share
                if g == answer:
                    won += share
                else:
                    next_states[alive & consistent[g]] += share
        by_turn.append(won)
        states = next_states
        if not states:
            break

    win = sum(by_turn)
    expected = sum((k + 1) * p for k, p in enumerate(by_turn)) / win if win else float("inf")
    return Odds(win, by_turn, visits, expected)


# ---------- Генератор ----------

@dataclass
class Difficulty:
    attempts: int
    palette_min: int
    palette_cap: int
    win_chance: tuple[float, float]     # целевая вероятность победы «обычного» игрока: (от, до)
    max_variants: int                   # сколько подвидов одного предмета допустимо в палитре (досок, красителей...)
    allow_niche: bool                   # знает ли игрок нишевые рецепты (смешивание красителей)
    allow_1x1_craft: bool = False

    def knows(self, recipe: Recipe) -> bool:
        return self.allow_niche or not is_niche(recipe)


DIFFICULTIES = {
    "easy":    Difficulty(attempts=8, palette_min=4, palette_cap=6,  win_chance=(0.95, 1.00),
                         max_variants=1, allow_niche=False),
    "medium":  Difficulty(attempts=6, palette_min=6, palette_cap=9,  win_chance=(0.75, 0.95),
                         max_variants=2, allow_niche=False),
    "hard":    Difficulty(attempts=6, palette_min=8, palette_cap=12, win_chance=(0.45, 0.75),
                         max_variants=3, allow_niche=True),
    "extreme": Difficulty(attempts=6, palette_min=8, palette_cap=20, win_chance=(0.10, 0.30),
                         max_variants=4, allow_niche=True),
}


@dataclass
class GeneratedGame:
    answer: Recipe
    palette: list[str]
    odds: Odds
    smart_steps: int
    decoys: list[Recipe]
    candidates: list[Recipe]   # что игрок может собрать из палитры (из тех рецептов, что знает)
    tries: int                 # с какой попытки получилась игра


@dataclass
class _Evaluation:
    palette: frozenset[str]
    odds: Odds
    smart_steps: int
    candidates: list[Recipe] = field(repr=False)


def pick_items(recipe: Recipe, palette: frozenset[str], rng: random.Random) -> frozenset[str]:
    """По предмету из каждой группы рецепта, предпочитая те, что уже есть в палитре."""
    chosen = set()
    for group in sorted(recipe.required_items, key=sorted):
        present = group & palette
        chosen.add(min(present) if present else rng.choice(sorted(group)))
    return frozenset(chosen)


class GameGenerator:
    def __init__(self, recipes: list[Recipe]):
        self.graph = CraftGraph(recipes)
        # Ответом может быть рецепт, все предметы которого встречаются ещё где-то — иначе его нечем прикрыть
        answers = (
            r for r in recipes
            if all(any(o.result != r.result for o in self.graph.uses(i)) for i in r.possible_items)
        )
        self.families: dict[str, list[Recipe]] = defaultdict(list)
        for r in sorted(answers, key=lambda r: r.filename):
            self.families[family(r.result)].append(r)
        self.oracle = Oracle()

    # --- проверки ---

    def enables_alternative(self, answer: Recipe, palette: frozenset[str]) -> bool:
        """Можно ли собрать тот же предмет другим рецептом — тогда верный по сути ответ засчитается неверным."""
        return any(
            r is not answer and all(group & palette for group in r.required_items)
            for r in self.graph.made_by(answer.result)
        )

    def evaluate(self, answer: Recipe, palette: frozenset[str], difficulty: Difficulty) -> _Evaluation | None:
        """Оценка палитры или None, если она недопустима."""
        if len(palette) > difficulty.palette_cap:
            return None
        if max(Counter(family(i) for i in palette).values()) > difficulty.max_variants:
            return None                                     # слишком много досок/красителей/шерсти разных видов
        if self.enables_alternative(answer, palette):
            return None
        candidates = [r for r in self.graph.craftable(palette) if difficulty.knows(r)]
        matrix = color_matrix(candidates, palette, self.oracle)
        answer_index = candidates.index(answer)
        smart = solve_steps(matrix, answer_index)
        if smart > difficulty.attempts - 1:                 # даже умному игроку нужен запас
            return None
        odds = play_odds(matrix, answer_index, difficulty.attempts)
        return _Evaluation(palette, odds, smart, candidates)

    # --- сборка ---

    def choose_answer(self, difficulty: Difficulty, rng: random.Random) -> Recipe:
        """Сначала семейство, потом рецепт в нём: 16 цветов шерсти не задавят всё остальное."""
        while True:
            members = self.families[rng.choice(sorted(self.families))]
            allowed = [
                r for r in members
                if (difficulty.allow_1x1_craft or r.items_in_recipe > 1) and difficulty.knows(r)
            ]
            if allowed:
                return rng.choice(allowed)

    def build_palette(self, answer: Recipe, difficulty: Difficulty, rng: random.Random,
                      candidates_per_step: int = 8):
        low, high = difficulty.win_chance
        target = (low + high) / 2
        current = self.evaluate(answer, pick_items(answer, frozenset(), rng), difficulty)
        if current is None:
            return None
        decoys: list[Recipe] = []

        while True:
            enough = len(current.palette) >= difficulty.palette_min
            if enough and low <= current.odds.win <= high:
                return current, decoys                      # попали в цель — дальше не раздуваем
            if len(current.palette) >= difficulty.palette_cap:
                return None

            craftable_now = {id(r) for r in current.candidates}
            pool = {
                id(r): r
                for item in sorted(current.palette) for r in self.graph.uses(item)
                if r.result != answer.result and id(r) not in craftable_now and difficulty.knows(r)
            }
            pool = sorted(pool.values(), key=lambda r: r.filename)
            rng.shuffle(pool)

            best = None
            for decoy in pool[:candidates_per_step]:
                evaluation = self.evaluate(answer, current.palette | pick_items(decoy, current.palette, rng), difficulty)
                if evaluation is None or evaluation.odds.win < low:
                    continue                                # слишком сложно — такую приманку не берём
                new_items = len(evaluation.palette - current.palette)
                # Пока палитра мала — тянемся к центру цели; при равенстве дешевле та, что добавляет меньше предметов
                score = (abs(evaluation.odds.win - target), new_items)
                if best is None or score < best[0]:
                    best = (score, decoy, evaluation)
            if best is None:
                return None
            _, decoy, current = best
            decoys.append(decoy)

    def build(self, difficulty: Difficulty, seed: str, max_tries: int = 30) -> GeneratedGame:
        rng = random.Random(seed)
        self.oracle = Oracle()                              # кэш живёт одну генерацию, иначе растёт бесконечно
        for attempt in range(1, max_tries + 1):
            answer = self.choose_answer(difficulty, rng)
            built = self.build_palette(answer, difficulty, rng)
            if built is None:
                continue
            evaluation, decoys = built
            assert all(group & evaluation.palette for group in answer.required_items), "игра нерешаема"
            items = sorted(evaluation.palette)
            rng.shuffle(items)
            return GeneratedGame(answer, items, evaluation.odds, evaluation.smart_steps, decoys,
                                 evaluation.candidates, attempt)
        raise RuntimeError(f"Не удалось собрать игру за {max_tries} попыток (seed={seed})")


# ---------- Замеры ----------

def histogram(values, edges) -> str:
    counts = [0] * (len(edges) - 1)
    for v in values:
        for i in range(len(edges) - 1):
            if edges[i] <= v < edges[i + 1] or (i == len(edges) - 2 and v == edges[-1]):
                counts[i] += 1
                break
    return "  ".join(f"{edges[i]:.2f}-{edges[i + 1]:.2f}: {c}" for i, c in enumerate(counts) if c)


if __name__ == "__main__":
    t = time.perf_counter()
    recipes = load_recipes()
    generator = GameGenerator(recipes)
    print(f"Рецептов: {len(recipes)}, семейств ответов: {len(generator.families)}, "
          f"загрузка {time.perf_counter() - t:.2f} c\n")

    for name, difficulty in DIFFICULTIES.items():
        times, games, fails = [], [], 0
        for n in range(100):
            t = time.perf_counter()
            try:
                games.append(generator.build(difficulty, seed=f"{name}:{n}"))
            except RuntimeError:
                fails += 1
                continue
            times.append(time.perf_counter() - t)

        times.sort()
        sizes = defaultdict(int)
        for g in games:
            sizes[len(g.palette)] += 1
        dyes = [sum(is_dye(i) for i in g.palette) for g in games]
        variants = [max(Counter(family(i) for i in g.palette).values()) for g in games]
        print(f"=== {name} ===")
        print(f"  не удалось: {fails}/100, перезапусков в среднем: {sum(g.tries for g in games) / len(games):.1f}")
        print(f"  время: медиана {times[len(times) // 2] * 1000:.0f} мс, "
              f"95% {times[int(len(times) * 0.95)] * 1000:.0f} мс, макс {times[-1] * 1000:.0f} мс")
        print(f"  размер палитры: {dict(sorted(sizes.items()))}")
        print(f"  красителей: в среднем {sum(dyes) / len(dyes):.1f}, максимум {max(dyes)}; "
              f"подвидов одного предмета максимум: {dict(sorted(Counter(variants).items()))}")
        print(f"  шанс победы обычного игрока: {histogram([g.odds.win for g in games], [0, .45, .6, .75, .85, .95, 1])}")
        print(f"  попыток умного игрока: {dict(sorted(defaultdict(int, {s: sum(g.smart_steps == s for g in games) for s in range(1, 9)}).items()))}")
        for g in games[:4]:
            print(f"\n    ответ {g.answer.result}: шанс {g.odds.win:.0%}, в среднем за {g.odds.expected_turns:.1f} попытки, "
                  f"умный за {g.smart_steps}, гипотез {len(g.candidates)}")
            print(f"      палитра: {g.palette}")
            print(f"      по попыткам: {[f'{p:.0%}' for p in g.odds.by_turn]}")
            top = sorted(((r, p) for r, p in zip(g.candidates, g.odds.visits) if r is not g.answer),
                         key=lambda kv: -kv[1])[:5]
            print(f"      куда чаще заходят: {[(r.result, f'{p:.0%}') for r, p in top]}")
        print()