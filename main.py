import json
import os
import random
import secrets
import struct
import time
from collections import defaultdict, Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from fnmatch import fnmatch
from functools import lru_cache
from itertools import chain
from typing import Annotated, Callable

from fastapi import FastAPI, Path, HTTPException, Depends, Request, Response, Cookie, Query
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.staticfiles import StaticFiles
from pathlib import Path as FilePath

from game_gen import GameGenerator, Difficulty, DIFFICULTIES, GeneratedGame
from recipes import omit_namespace, RecipeParser, Recipe

# def connect():
#     conn = sqlite3.connect("data.db")
#     conn.row_factory = sqlite3.Row # строки можно читать как row["name"]
#     conn.execute("PRAGMA foreign_keys = ON") # в SQLite проверка внешних ключей по умолчанию выключена
#     return conn
#
# def get_db():
#     conn = connect()
#     try:
#         yield conn
#     finally:
#         conn.close() # выполнится после ответа

# DB = Annotated[sqlite3.Connection, Depends(get_db)]

parser = RecipeParser()
game_generator: GameGenerator | None = None

all_recipes: list[Recipe] = []
items_crafted: dict[str, list[Recipe]] = defaultdict(list)

mentioned_items = {"red_nether", "purpur"}
all_ingredients: set[str] = set()
popularity = defaultdict(int)

recipes_by_item: dict[str, list[Recipe]] = defaultdict(list)

with open(f"mc_resources/assets/lang/ru_ru.json", "r", encoding="UTF-8") as _f:
    translations_RU: dict[str, str] = json.load(_f)


app = FastAPI()


class RecipesContainer:
    WOODTYPES = ["dark_oak", "pale_oak", "oak", "spruce", "birch", "jungle", "acacia", "crimson", "warped", "mangrove", "bamboo", "cherry", "poplar"]
    TOOLTYPES = ["sword", "pickaxe", "axe", "hoe", "shovel", "spear"]
    COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black"]
    BLOCKVARIANTS = ["_slab", "_stairs", "chiseled_", "_pillar", "_wall", "_fence", "_bricks", "_brick_slab", "_brick_stairs", "_brick_wall", "_bars", "polished_", "waxed_", "weathered_", "oxidized_", "exposed"]

    _dyes = [f"{col}_dye" for col in COLORS]
    # Крашеные предметы - те которые в своём крафте имеют другой крашеный предмет или же сам краситель
    groups: dict[str, list[Recipe]] = defaultdict(list)

    _tag_cache: dict[str, list] = defaultdict(list)
    _tag_cache.update({
        "fishing_rod":        ["tool_other",],
        "flint_and_steel":    ["tool_other",],
        "turtle_shell":       ["tool_other",],
        "chiseled_bookshelf": ["other"]
    })
    # TODO: костыль

    for color in COLORS:
        for base_item_type in ("wool", "concrete", "terracotta", "stained_glass"):
            _tag_cache[f"{color}_{base_item_type}"].append(f"dyed_{color}")

    @classmethod
    def classify(cls, item_id: str, visited: frozenset[str] = frozenset()) -> list[str]:
        if item_id in cls._tag_cache:
            return cls._tag_cache[item_id]
        if item_id in visited:  # пришли по кругу — дальше не углубляемся
            # print("Круговая рекурсия", visited)
            return ["other"]

        tag = list(cls._compute_tag(item_id, visited | {item_id})) or ["other"]
        cls._tag_cache[item_id] = tag
        return tag

    @classmethod
    def _compute_tag(cls, item_id: str, visited: frozenset[str]):
        recipes = items_crafted.get(item_id, [])

        if any(item_id in r.possible_items for r in recipes):
            yield "duplicatable"
            return

        if item_id.endswith("_dye"):
            yield f"dyed_{item_id[:-4]}"
            return

        for tooltype in cls.TOOLTYPES:
            if item_id.endswith(f"_{tooltype}"):
                yield f"tool_{item_id[:len(item_id)-len(tooltype)-1]}"
                return

        for woodtype in cls.WOODTYPES:
            if (f"{woodtype}_" in item_id) or (f"_{woodtype}" in item_id):
                yield f"wooden_{woodtype}"
                break

        def get_color_tag():
            #   блок из слаймов
            for recipe in recipes:
                # 9 слаймов         слайм
                if len(recipe.possible_items) == 1 and recipe.items_in_recipe == 9:
                    crafted_from = tuple(recipe.possible_items)[0]
                    for other_recipe in items_crafted[crafted_from]:
                        #    слаймы из блока                               только блок
                        if other_recipe.items_in_recipe == 1 and tuple(other_recipe.possible_items)[0] == recipe.result:
                            yield "compacting"
                            cls.groups["uncompacting"].append(other_recipe)
                            cls._tag_cache[recipe.result].append("uncompacting")
                            try:
                                cls._tag_cache[recipe.result].remove("other")
                            except ValueError: pass

                for ingredient in recipe.possible_items:
                    for color in cls.COLORS:
                        if ingredient != f"{color}_dye": continue
                        yield f"dyed_{color}"
                        return
                    sub_tags = cls.classify(ingredient, visited)
                    for sub_tag in sub_tags:
                        if not sub_tag.startswith("dyed_"): continue
                        yield sub_tag
                        return

        if color_tag := tuple(get_color_tag()):
            yield from color_tag

        for variant in cls.BLOCKVARIANTS:
            if not (item_id.endswith(variant) or item_id.startswith(variant)): continue
            main_type = item_id.replace(variant, "")
            if main_type not in mentioned_items: continue
            yield f"{variant.strip('_')}_{main_type}"
            break

    @classmethod
    def expand_groups(cls, *patterns: str) -> list[str]:
        """'slab_*' → ['slab_oak', 'slab_stone', ...]; обычное имя группы тоже работает."""
        names = []
        for pattern in patterns:
            for name in cls.groups:
                if fnmatch(name, pattern) and name not in names:
                    names.append(name)
        return names

    @classmethod
    def get_pool(cls, *patterns: str, predicate: Callable[[Recipe], bool] | None = None) -> list[Recipe]:
        pool = [
            recipe
            for recipe in chain.from_iterable(cls.groups[name] for name in cls.expand_groups(*patterns))
            if predicate is None or predicate(recipe)
        ]
        if not pool:
            raise ValueError(f"Нет подходящих рецептов в группах {patterns}")
        return pool


    @classmethod
    def choose_from_groups(cls, *patterns: str,
                           rng: random.Random | None = None,
                           predicate: Callable[[Recipe], bool] | None = None) -> Recipe:
        pool = cls.get_pool(*patterns, predicate=predicate)
        if not pool:
            raise ValueError(f"Нет подходящих рецептов в группах {patterns}")
        return (rng or random).choice(pool)


# TODO: shapeless
def load_recipes():
    recipe_dir = "mc_resources/data/recipe"
    
    for path in sorted(FilePath(recipe_dir).glob("*.json")):  # sorted стабильный порядок для seed
        with open(path, "r", encoding="UTF-8") as f:
            # print(path)
            recipe = parser.parse(json.load(f), path.name)
            if recipe is None: continue
            
            all_recipes.append(recipe)
            mentioned_items.update(recipe.possible_items)
            mentioned_items.add(recipe.result)
            all_ingredients.update(recipe.possible_items)
            items_crafted[recipe.result].append(recipe)
            for item in recipe.possible_items:
                recipes_by_item[item].append(recipe)

    for item_grid in all_recipes:
        tags = RecipesContainer.classify(item_grid.result)
        for tag in tags:
            RecipesContainer.groups[tag].append(item_grid)

    for grid in all_recipes:
        for item_type in grid.required_items:
            popularity[item_type] += 1

    global game_generator
    game_generator = GameGenerator(all_recipes)


SALT = os.environ.get("CRAFTLE_SALT", "CRAFTLE_SALT")


@dataclass
class Game:
    answer: Recipe
    palette: list[str]
    max_attempts: int
    attempts_left: int

    def get_setup(self):
        return GameSetup(palette=self.palette, render_data={item_id: item_render_info(item_id) for item_id in self.palette}, max_attempts=self.max_attempts)


class GameSetup(BaseModel):
    palette: list[str]
    render_data: dict[str, dict]
    max_attempts: int


class Guess(BaseModel):
    grid: list[str | None]
    result: str


class GuessResult(BaseModel):
    colors: list[int | None]
    solved: bool
    game_over: bool
    attempts_left: int
    solution: list[str | None] | None


games: dict[str, Game] = {}
sessions = {}
SET_SEED = None


properties = {
    "display_context": "gui",
    "context_dimension": "overworld",
    "main_hand": "right",
    "charge_type": "none",
}
ranges = {
    "compass": 0,
    "cooldown": 0,
    "count": 1,
    "crossbow_pull": 0
}
def parse_items_definition(render) -> str | None:
    if render is None: return None
    render_type = omit_namespace(render.get("type", "none"))
    render_property = omit_namespace(render.get("property", "none"))
    match render_type:
        case "model": # Предопределённая модель
            return render["model"]

        case "select": # Проверка свойства
            test_property = properties.get(render_property)
            if test_property is None: return parse_items_definition(render.get("fallback"))
            for render_case in render["cases"]:
                case_when = render_case["when"]
                if test_property in (case_when if isinstance(case_when, list) else (case_when,)):
                    return parse_items_definition(render_case["model"])
            return parse_items_definition(render.get("fallback"))

        case "condition": # Проверка условия
            test_property = properties.get(render_property)
            if isinstance(test_property, bool) and test_property:
                return parse_items_definition(render["on_true"])
            return parse_items_definition(render["on_false"])

        case "composite": # TODO частичная поддержка
            return parse_items_definition(render["models"][0])

        case "range_dispatch": # Выбор в диапазоне
            if render_property not in ranges: return parse_items_definition(render.get("fallback"))
            test_value = ranges.get(render_property, 0) * render.get("scale", 1)

            candidate = None
            for render_entry in sorted(render.get("entries"), key=lambda x: x.get("threshold", 0)):
                passed = render_entry["threshold"] <= test_value
                if passed: candidate = render_entry["model"]
            return parse_items_definition(render.get("fallback") if candidate is None else candidate)

        case "special":
            return render.get("base")

        case _:
            return None


@lru_cache
def load_model_json(path: str) -> dict:
    with open(f"mc_resources/assets/models/{path}.json", encoding="UTF-8") as f:
        loaded: dict = json.load(f)
        loaded["display_gui"] = loaded.get("display", {}).get("gui", {})
        return loaded


@lru_cache
def texture_aspect(tex: str) -> float:
    """Высота / ширина У анимированных текстур больше 1"""
    with open(f"mc_resources/assets/textures/{tex}.png", "rb") as f:
        w, h = struct.unpack(">II", f.read(24)[16:24])   # ширина и высота лежат в байтах 16..24
    return h / w


def resolve_model(path: str) -> dict:
    """Модель со всей цепочкой родителей: textures слиты, elements и display - от ближайшего"""
    path = omit_namespace(path)
    if path.startswith("builtin/"): # builtin/generated, builtin/entity это конец цепочки
        return {"builtin": path, "textures": {}, "elements": None, "display_gui": {}}

    own = load_model_json(path)
    parent = resolve_model(own["parent"]) if "parent" in own else \
             {"builtin": None, "textures": {}, "elements": None, "display_gui": {}}

    return {
        "builtin":  parent["builtin"],
        "textures": {**parent["textures"], **own.get("textures", {})},   # ребёнок перекрывает родителя
        "elements": own.get("elements", parent["elements"]),              # не сливаются, берём ближайшие
        "display_gui": own.get("display_gui") or parent["display_gui"], # уже выбранный gui
    }


def default_uv(face: str, p_from: list, p_to: list) -> list:
    (x1, y1, z1), (x2, y2, z2) = p_from, p_to
    return {
        "up":    [x1, z1, x2, z2],
        "down":  [x1, 16 - z2, x2, 16 - z1],
        "north": [16 - x2, 16 - y2, 16 - x1, 16 - y1],
        "south": [x1, 16 - y2, x2, 16 - y1],
        "west":  [z1, 16 - y2, z2, 16 - y1],
        "east":  [16 - z2, 16 - y2, 16 - z1, 16 - y1],
    }[face]


@lru_cache
def item_render_info(item_id: str) -> dict:
    display_name = translations_RU.get(f"item.minecraft.{item_id}", translations_RU.get(f"block.minecraft.{item_id}", item_id))
    try:
        with open(f"mc_resources/assets/items/{item_id}.json", "r", encoding="UTF-8") as f:
            item_model_data: dict = json.load(f)

        model_filepath = omit_namespace(parse_items_definition(item_model_data.get("model", {})) or "item/barrier")
        model_data = resolve_model(model_filepath)
        _textures = model_data.get("textures", {})

        def texture(ref: str, depth: int = 0) -> str | None:
            """'#side' -> 'block/oak_log' depth защищает от циклов в кривых моделях"""
            if isinstance(ref, dict):  # новый формат: {"sprite": ..., "force_translucent": ...}
                ref = ref["sprite"]
            if not ref.startswith("#"):
                return omit_namespace(ref)
            if depth > 10 or ref[1:] not in _textures:
                return None
            return texture(_textures[ref[1:]], depth + 1)

        match model_data.get("builtin"):
            case "builtin/generated":
                return {
                    "type": "flat",
                    "display_name": display_name,
                    f"texture": f"assets/textures/{texture("#layer0")}.png"
                }
            case None: # 3d рендер по элементам
                elements_out = []
                for element in model_data["elements"] or []:
                    faces_out = {}
                    for face_name, face in element["faces"].items():
                        tex = texture(face["texture"])
                        if tex is None: continue
                        faces_out[face_name] = {
                            "texture": f"assets/textures/{tex}.png",
                            "aspect": texture_aspect(tex),
                            "uv": face.get("uv") or default_uv(face_name, element["from"], element["to"]),
                            "rotation": face.get("rotation", 0),  # поворот текстуры на грани: 0/90/180/270
                            "tintindex": face.get("tintindex"),
                        }
                    elements_out.append({
                        "from": element["from"],
                        "to": element["to"],
                        "rotation": element.get("rotation"),  # поворот самого элемента (факелы, растения)
                        "faces": faces_out,
                    })
                return {
                    "type": "3d",
                    "display_name": display_name,
                    "elements": elements_out,
                    "transform": model_data["display_gui"]
                }
            case _: raise KeyError("Не подошёл тип")
    except (FileNotFoundError, KeyError) as ex:
        return {
            "type": "item",
            "display_name": display_name,
            "texture": "assets/textures/item/barrier.png"
        }


def get_player(response: Response, player_id: Annotated[str | None, Cookie()] = None) -> str:
    if player_id is None:
        player_id = secrets.token_urlsafe(16)
        response.set_cookie("player_id", player_id, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365)
    return player_id


def player_or_ip(request: Request) -> str:
    return request.cookies.get("player_id") or get_remote_address(request)


PlayerID = Annotated[str, Depends(get_player)]
limiter = Limiter(key_func=player_or_ip, headers_enabled=True)


# Стартует рандомную игру и отменяет предыдущую
@app.post("/api/game/new")
def start_new_game(player_id: PlayerID, difficulty: Difficulty = DIFFICULTIES['medium']) -> GameSetup:
    game = build_game(difficulty)
    games[player_id] = game
    return game.get_setup()


@app.post("/api/game/daily")
def start_daily_game(player_id: PlayerID) -> GameSetup:
    game = build_game(DIFFICULTIES['medium'], seed=f"{SALT}:{date.today().isoformat()}")
    games[player_id] = game
    return game.get_setup()


def build_game(difficulty: Difficulty, seed: str | None = None) -> Game:
    if seed is None:
        seed = SET_SEED or str(random.randint(1, 10**14))

    game = game_generator.build(difficulty, seed)
    # game = GeneratedGame(items_crafted["smoker"][0], [
    #     "mangrove_slab", "golden_apple"
    # ], None, None, None, None, None)
    # game = GeneratedGame(items_crafted["smoker"][0], sorted(items_crafted.keys()), None, None, None, None, None)
    # game = GeneratedGame(items_crafted["smoker"][0], sorted(e.result for e in RecipesContainer.get_pool("slab_*")), None, None, None, None, None)
    # assert all(group & set(game.palette) for group in game.answer.required_items)
    return Game(
        game.answer,
        game.palette,
        difficulty.attempts,
        difficulty.attempts
    )


@app.get("/api/crafting")
@limiter.limit("10/second")
def get_crafting_result(player_id: PlayerID, grid_string: Annotated[list[str], Query(alias="g")],
                        request: Request, response: Response):
    if player_id not in games: raise HTTPException(404, "Игра не начата")

    if len(grid_string) != 9: raise HTTPException(400, "Сетка должна быть из 9 клеток")

    grid_nulled_string = [e or None for e in grid_string]
    if all(e is None for e in grid_nulled_string): raise HTTPException(400, "Нельзя отправить пустой запрос")

    candidates = min(
        (recipes_by_item.get(item, []) for item in grid_nulled_string if item is not None),
        key=len,
    )
    for recipe in candidates:
        if not recipe.can_craft(grid_nulled_string): continue
        return {"render": item_render_info(recipe.result), "item_id": recipe.result}
    return {"render": None, "item_id": None}


@app.post("/api/guess")
def guess(player_id: PlayerID, data: Guess) -> GuessResult:
    game = games.get(player_id)
    if game is None: raise HTTPException(404, "Игра не начата")

    if len(data.grid) != 9: raise HTTPException(400, "Сетка должна быть из 9 клеток")

    if all(e is None for e in data.grid): raise HTTPException(400, "Нельзя отправить пустой запрос")

    for recipe in items_crafted.get(data.result, []):
        if recipe.can_craft(data.grid): break
    else:
        raise HTTPException(400, "Несуществующий рецепт!")

    colors, solved = game.answer.check_guess(data.grid)
    if not solved:
        game.attempts_left -= 1

    game_over = solved or game.attempts_left <= 0
    if game_over: del games[player_id]

    return GuessResult(
        colors=colors,
        solved=solved,
        game_over=game_over,
        attempts_left=game.attempts_left,
        solution=game.answer.get_solution_grid(
            preferred={e for e in data.grid if e}
            if solved else
            game.palette
        ) if game_over else None
    )


# fastapi dev main.py --port 5500
app.mount("/assets", StaticFiles(directory="mc_resources/assets/"))
app.mount("/resources", StaticFiles(directory="resources/"))
app.mount("/font", StaticFiles(directory="font/"))
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    timestamp = time.perf_counter()
    load_recipes()
    print(f"Loaded recipies in {(time.perf_counter() - timestamp):.3f}")
    # print(item_info("piston"))
    # SET_SEED = 374743
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5500)