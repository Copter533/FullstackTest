import { createModel3d } from "./render.js"

// Остаётся на фронте, меняется через бэк и отправляется в бэк
const state = {
    grid_string: [
        null, null, null, 
        null, null, null, 
        null, null, null
    ],
    palette: [], // list[str]
    history: [], // list[list[9, list[itemId, colorId]]]
    crafted_result: null,

    crafting_cache: new Map(),
    render_data: {}, // dict
    max_attempts: 0,
    attempts_left: 0
};

const COLOR_CLASSES = ["gray", "yellow", "green"]

// Выборка в фронте
let paletteSlotSe = null

function debounce(fn, ms) {
    let timer = null
    return (...args) => {
        clearTimeout(timer)
        timer = setTimeout(() => fn(...args), ms)
    }
}

async function fetchWithRetry(url, retries = 3) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const res = await fetch(url)
        if (res.status !== 429) return res

        // Сервер может подсказать, сколько ждать (в секундах), иначе - 300, 600, 1200 мс...
        const retryAfter = Number(res.headers.get("Retry-After"))
        const wait = retryAfter ? retryAfter * 1000 : 300 * 2 ** attempt
        await new Promise(resolve => setTimeout(resolve, wait))
    }
    return null // так и не дождались
}

function createItemImg(itemId) {
    const itemRenderData = state.render_data[itemId] ?? null
    const type = itemRenderData == null ? "unknown" : itemRenderData.type
    let itemSe;
    if (type === "3d") {
        itemSe = createModel3d(itemRenderData)
    } else if (type === "flat") {
        itemSe = document.createElement("img")
        itemSe.setAttribute("src", itemRenderData.texture)
        itemSe.setAttribute("alt", itemId)
    } else throw "Неизвестный тип рендера"
    itemSe.dataset.item = itemId
    return itemSe
}

async function flipSlotsFly(sourceSe, targetPanelSe, duration) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return

    // Копия: верстак, обёрнутый в такую же рамку, как у попыток
    const gridCopySe = sourceSe.cloneNode(true)
    gridCopySe.removeAttribute("id")
    gridCopySe.querySelectorAll(".reveal").forEach(s => s.classList.remove("reveal"))

    const ghostSe = document.createElement("section")
    ghostSe.classList.add("panel", "thin")
    ghostSe.append(gridCopySe)

    // First: ставим копию так, чтобы её сетка совпала с настоящим верстаком
    const from = sourceSe.getBoundingClientRect()
    Object.assign(ghostSe.style, {
        position: "fixed", left: `${from.left}px`, top: `${from.top}px`,
        margin: "0", zIndex: "1000", pointerEvents: "none", transformOrigin: "top left",
    })
    document.body.append(ghostSe)
    const inner = gridCopySe.getBoundingClientRect()          // сетка сдвинута рамкой и отступом
    ghostSe.style.left = `${from.left - (inner.left - from.left)}px`
    ghostSe.style.top = `${from.top - (inner.top - from.top)}px`

    // Last
    const start = ghostSe.getBoundingClientRect()
    const to = targetPanelSe.getBoundingClientRect()
    const targetGrid = targetPanelSe.querySelector(".grid").getBoundingClientRect()
    const scale = targetGrid.width / from.width // считаем по сеткам, а не по панелям
    targetPanelSe.style.visibility = "hidden"

    gridCopySe.style.transformOrigin = "top left"
    const options = { duration, easing: "cubic-bezier(.2, .2, .1, 1)" }


    // Play
    const panelAnim = ghostSe.animate(
        [
            { left: `${start.left}px`, top: `${start.top}px`, width: `${start.width}px`, height: `${start.height}px` },
            { left: `${to.left}px`,    top: `${to.top}px`,    width: `${to.width}px`,    height: `${to.height}px` },
        ],
        options
    )
    const gridAnim = gridCopySe.animate(
        [{ transform: "scale(1)" }, { transform: `scale(${scale})` }],
        options
    )
    await Promise.all([panelAnim.finished, gridAnim.finished])

    ghostSe.remove()
    targetPanelSe.style.visibility = ""
}

function startGame(gameSetup) {
    state.grid_string = Array(9).fill(null)
    state.palette = gameSetup.palette
    state.history = []
    state.render_data = gameSetup.render_data
    state.crafting_cache.clear()
    paletteSlotSe = null

    state.attempts_left = gameSetup.max_attempts
    state.max_attempts  = gameSetup.max_attempts

    redrawCrafting()
    redrawPalette()
    redrawHistory()
    drawCraftResult(null)
}

async function fetchDailyGame() {
    try {
        const res = await fetch(`/api/game/new`, {
            method: "POST",
            // headers: { "Content-Type": "application/json" },
            // body: JSON.stringify({ grid: state.grid_string }),
        })
        if (!res.ok) {
            if (res.status != 400) alert(`Ошибка ${res.status}: ${await res.text()}`)
            throw new Error(`Ошибка сервера: ${res.status}`)
        }
        startGame(await res.json())
    } catch (err) {
        console.error(err)
    }
}

async function submitGuess() {
    try {
        if (state.crafted_result == null) return
        const res = await fetch("/api/guess", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ grid: state.grid_string, result: state.crafted_result }),
        })
        if (!res.ok) {
            if (res.status != 400) alert(`Ошибка ${res.status}: ${await res.text()}`)
            throw new Error(`Ошибка сервера: ${res.status}`)
        }
        const response = await res.json()
        showGuessResult(response['colors'])
        
        let waitTime = 0;
        for (const color of response['colors']) {
            if (color != null) waitTime += 30;
        }
        await new Promise(r => setTimeout(r, waitTime + 250)) // ждём, пока клетки перевернутся
        
        if (response['game_over']) {
            showEndScreen(response['solved'], response['solution'])
        }
        if (!response['solved']) {
            state.history.push(state.grid_string.map((e, i) => {
                const colorId = response['colors'][i]
                return [e, colorId == null ? null : COLOR_CLASSES[colorId]]
            }))
            redrawHistory()

            const sourceSe = document.querySelector("#craft_grid")
            const targetSe = document.querySelector("#history_container .panel:last-child")
            await flipSlotsFly(sourceSe, targetSe, 1000)
        }

    } catch (err) {
        console.error(err)
    }
}

function redrawCrafting() {
    resultShown = false

    const frag = document.createDocumentFragment();
    const gridSe = document.querySelector("#craft_grid")

    for (let i = 0; i < 9; i++) {
        const itemId = state.grid_string[i]
        let slotSe = document.createElement("div")
        slotSe.classList.add("slot")
        slotSe.dataset.index = i

        if (itemId != null) {
            slotSe.appendChild(createItemImg(itemId))
        }
        
        // dragover
        slotSe.addEventListener("click", (e) => {
            hideGuessResult()
            const oldItemId = state.grid_string[e.currentTarget.dataset.index]
            let itemId;
            if (paletteSlotSe == null) { itemId = null} 
            else { itemId = paletteSlotSe.dataset.itemId }

            if (itemId == null || oldItemId != null) { 
                e.currentTarget.textContent = ''
                itemId = null
            }
            else { 
                e.currentTarget.replaceChildren(createItemImg(itemId)) 
            }
            state.grid_string[e.currentTarget.dataset.index] = itemId
            onGridChanged()
        })

        frag.appendChild(slotSe)
    }
    gridSe.replaceChildren(frag);
}

function onGridChanged() {
    const key = state.grid_string.join(",")
    if (!state.grid_string.some(item => item)) { // если сетка пустая
        drawCraftResult(null)
        return
    } else if (state.crafting_cache.has(key)) {
        drawCraftResult(state.crafting_cache.get(key))
        return
    } else {
        fetchCraftResult()
    }
}

function drawCraftResult(itemId) {
    const slotSe = document.querySelector("#result")
    if (itemId) {
        slotSe.classList.add("ready")
        slotSe.replaceChildren(createItemImg(itemId))
        state.crafted_result = itemId
    } else {
        slotSe.classList.remove("ready")
        slotSe.replaceChildren()
        state.crafted_result = null
    }
}

const fetchCraftResult = debounce(async () => {
    const key = state.grid_string.join(",")
    const params = new URLSearchParams()
    for (const item of state.grid_string) params.append("g", item ?? "")

    const res = await fetchWithRetry(`/api/crafting?${params}`)
    if (!res || !res.ok) return

    const data = await res.json()
    console.log(data)
    state.crafting_cache.set(key, data.item_id)
    if (data.render) {
        state.render_data[data.item_id] = data.render
    }
    if (state.grid_string.join(",") === key) {
        drawCraftResult(data.item_id)
    }
}, 50)

function redrawPalette() {
    const frag = document.createDocumentFragment();
    const paletteSe = document.querySelector("#palette")

    const fullsize = Math.ceil(state.palette.length / 9) * 9
    for (let i = 0; i < fullsize; i++) {
        const slotSe = document.createElement("div")
        slotSe.classList.add("slot")

        if (i < state.palette.length) {
            const itemId = state.palette[i]

            slotSe.dataset.itemId = itemId
            slotSe.appendChild(createItemImg(itemId))

            slotSe.addEventListener("click", (e) => {
                const sameSlot = paletteSlotSe === e.currentTarget
                if (paletteSlotSe != null || sameSlot) {
                    paletteSlotSe.classList.remove("selected")
                }
                if (!sameSlot) {
                    e.currentTarget.classList.add("selected")
                    paletteSlotSe = e.currentTarget
                } else {
                    paletteSlotSe = null
                }
            })
        }

        frag.appendChild(slotSe)
    }
    paletteSe.replaceChildren(frag);
}

function redrawHistory() {
    const frag = document.createDocumentFragment();
    const historySe = document.querySelector("#history_container")

    for (const grid of state.history) {
        let panelSe = document.createElement("section")
        panelSe.classList.add("panel", "thin")
        let gridSe = document.createElement("div")
        gridSe.classList.add("grid", "small")

        for (const [itemId, color] of grid) {
            let slotSe = document.createElement("div")
            slotSe.classList.add("slot")
            if (color != null) slotSe.classList.add(color);
            if (itemId != null) slotSe.appendChild(createItemImg(itemId))
            gridSe.appendChild(slotSe)
        }
        panelSe.appendChild(gridSe)
        frag.appendChild(panelSe)
    }

    historySe.replaceChildren(frag)
}

let resultShown = false

function showGuessResult(colorsArray) {
    if (resultShown) return
    resultShown = true

    const gridSe = document.querySelector("#craft_grid")
    const slotsSe = gridSe.querySelectorAll(".slot")

    let index = 0
    for (let i = 0; i < colorsArray.length; i++) {
        if (colorsArray[i] == null) continue
        slotsSe[i].classList.add("reveal", COLOR_CLASSES[colorsArray[i]])
        slotsSe[i].style.setProperty("--i", index++)
    }
}

function hideGuessResult() {
    if (!resultShown) return
    resultShown = false

    // void gridSe.offsetWidth // чтение размера заставляет браузер применить изменения прямо сейчас
    const gridSe = document.querySelector("#craft_grid")
    const slotsSe = gridSe.querySelectorAll(".slot")

    for (let i = 0; i < slotsSe.length; i++) {
        slotsSe[i].classList.remove("reveal", ...COLOR_CLASSES)
    }
}

function showEndScreen(solved, solution) {
    const used = state.max_attempts - state.attempts_left + 1
    document.querySelector("#end_title").textContent = solved ? "Победа!" : "Не угадал"
    document.querySelector("#end_text").textContent = solved
        ? `Рецепт собран за ${used} из ${state.max_attempts} попыток`
        : "Правильный рецепт:"

    const frag = document.createDocumentFragment();
    const gridSe = document.querySelector("#end_solution")

    for (const itemId of solution) {
        let slotSe = document.createElement("div")
        slotSe.classList.add("slot")
        if (itemId != null) slotSe.appendChild(createItemImg(itemId))
        frag.appendChild(slotSe)
    }
    gridSe.replaceChildren(frag);

    document.querySelector("#end_dialog").showModal()
}

document.querySelector("#end_close_btn").addEventListener("click", () => {
    document.querySelector("#end_dialog").close()
})

document.querySelector("#result").addEventListener("click", submitGuess)
document.querySelector("#clear_btn").addEventListener("click", (e) => {
    hideGuessResult()
    state.grid_string = Array(9).fill(null)
    for (const slotSe of document.querySelectorAll("#craft_grid .slot")) {
        slotSe.replaceChildren() // убрать картинку, сам слот оставить
    }
    drawCraftResult(null)
})

const tooltip = document.getElementById("tooltip");

document.addEventListener("mouseover", (e) => {
    const itemId = e.target.closest(".slot")?.querySelector("[data-item]")?.dataset.item
    if (!itemId) return
    tooltip.textContent = state.render_data[itemId]?.display_name ?? itemId
    tooltip.hidden = false
})

document.addEventListener("mousemove", (e) => {
    if (tooltip.hidden) return;
    tooltip.style.left = `${e.clientX + 12}px`;   // чуть правее и выше курсора, как в игре
    tooltip.style.top = `${e.clientY - 24}px`;
});

document.addEventListener("mouseout", (e) => {
    if (e.target.closest(".slot")) tooltip.hidden = true
})

fetchDailyGame()