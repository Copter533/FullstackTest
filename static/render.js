// ===== Рендер моделей Minecraft по элементам (CSS 3D) =====
//
// Системы координат:
//   модель: x — восток, y — вверх, z — юг; единицы 0..16 («пиксели блока»)
//   CSS:    x — вправо, y — ВНИЗ, z — к зрителю; единица — var(--u) (один пиксель блока на экране)
// Переход: (x, y, z) модели -> (x - 8, -(y - 8), z - 8) в CSS, центр блока в центре контейнера.
// Из-за того, что ось Y смотрит в другую сторону, углы поворота вокруг X и Z меняют знак, а вокруг Y — нет.

const u = (n) => `calc(var(--u) * ${n})`

// Куда «смотрит» каждая грань и какие у неё размеры:
//   turn   — как повернуть div (изначально он смотрит на зрителя, +z), чтобы он смотрел наружу этой гранью
//   size   — ширина и высота грани из размеров элемента [dx, dy, dz]
//   depth  — насколько отступить от центра элемента вдоль нормали (половина третьего размера)
//   normal — нормаль грани в координатах модели (для освещения)
const FACES = {
    up:    { turn: "rotateX(90deg)",  size: ([dx, dy, dz]) => [dx, dz], depth: ([dx, dy, dz]) => dy, normal: [0, 1, 0] },
    down:  { turn: "rotateX(-90deg)", size: ([dx, dy, dz]) => [dx, dz], depth: ([dx, dy, dz]) => dy, normal: [0, -1, 0] },
    south: { turn: "",                size: ([dx, dy, dz]) => [dx, dy], depth: ([dx, dy, dz]) => dz, normal: [0, 0, 1] },
    north: { turn: "rotateY(180deg)", size: ([dx, dy, dz]) => [dx, dy], depth: ([dx, dy, dz]) => dz, normal: [0, 0, -1] },
    east:  { turn: "rotateY(90deg)",  size: ([dx, dy, dz]) => [dz, dy], depth: ([dx, dy, dz]) => dx, normal: [1, 0, 0] },
    west:  { turn: "rotateY(-90deg)", size: ([dx, dy, dz]) => [dz, dy], depth: ([dx, dy, dz]) => dx, normal: [-1, 0, 0] },
}

const DEFAULT_GUI = { rotation: [0, 0, 0], translation: [0, 0, 0], scale: [1, 1, 1] }
const DEFAULT_TINT = "#7cbd6b"   // цвет травы; позже можно брать tints из определения предмета

// Повороты вектора в координатах модели (правая система, y вверх), порядок как в игре: сначала Z, потом Y, потом X
function rotateVec([x, y, z], [rx, ry, rz]) {
    const r = (d) => d * Math.PI / 180
    let c = Math.cos(r(rz)), s = Math.sin(r(rz));
    [x, y] = [x * c - y * s, x * s + y * c]
    c = Math.cos(r(ry)); s = Math.sin(r(ry));
    [x, z] = [x * c + z * s, -x * s + z * c]
    c = Math.cos(r(rx)); s = Math.sin(r(rx));
    [y, z] = [y * c - z * s, y * s + z * c]
    return [x, y, z]
}

// Освещение: свет сверху-слева-спереди, как в инвентаре: верх светлее всего, левый бок средний, правый тёмный
const LIGHT = (() => { const v = [-0.6, 1, 1], l = Math.hypot(...v); return v.map(a => a / l) })()
function brightness(normal, gui) {
    const n = rotateVec(normal, gui.rotation)
    const d = n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]
    return 0.6 + 0.4 * Math.max(0, d)
}

// Поворот самого элемента (факелы, растения-«крестики»): вокруг точки origin по одной оси
function elementRotation(rotation) {
    if (!rotation) return ""
    const [ox, oy, oz] = rotation.origin
    const o = [ox - 8, -(oy - 8), oz - 8]
    const angle = rotation.axis === "y" ? rotation.angle : -rotation.angle   // знак — из-за оси Y вниз
    return `translate3d(${o.map(u).join(",")}) rotate${rotation.axis.toUpperCase()}(${angle}deg) ` +
           `translate3d(${o.map(v => u(-v)).join(",")})`
}

// Текстура на грани: кусок uv, поворот на 0/90/180/270, зеркало (если u1 > u2 или v1 > v2)
function createFaceTexture(face, width, height) {
    const [u1, v1, u2, v2] = face.uv
    const du = Math.abs(u2 - u1) || 16, dv = Math.abs(v2 - v1) || 16
    const quarter = (face.rotation / 90) % 2 === 1                 // на 90 и 270 ширина и высота меняются местами
    const [w, h] = quarter ? [height, width] : [width, height]
    const SEAM_OVERLAP = 0.5
    const grow = (size) => size > 0 ? (size + SEAM_OVERLAP) / size : 1

    const tex = document.createElement("div")
    tex.className = "tex"
    // Вся текстура 16 единиц в ширину и 16*aspect в высоту (у анимированных — лента кадров, видим первый)
    const size = `${u(16 / du * w)} ${u(16 * face.aspect / dv * h)}`
    const pos = `${u(-Math.min(u1, u2) / du * w)} ${u(-Math.min(v1, v2) / dv * h)}`
    Object.assign(tex.style, {
        width: u(w), height: u(h),
        backgroundImage: `url(${face.texture})`, backgroundSize: size, backgroundPosition: pos,
        transform: `translate(-50%, -50%) rotate(${face.rotation}deg) ` +
                   `scale(${(u1 > u2 ? -1 : 1) * grow(w)}, ${(v1 > v2 ? -1 : 1) * grow(h)})`,
    })
    if (face.tintindex != null) {
        // Окраска: умножаем цвет на текстуру, а маска по той же текстуре прячет цвет на прозрачных пикселях
        Object.assign(tex.style, {
            backgroundColor: DEFAULT_TINT, backgroundBlendMode: "multiply",
            maskImage: `url(${face.texture})`, maskSize: size, maskPosition: pos, maskRepeat: "no-repeat",
            webkitMaskImage: `url(${face.texture})`, webkitMaskSize: size, webkitMaskPosition: pos, webkitMaskRepeat: "no-repeat",
        })
    }
    return tex
}

export function createModel3d(render) {
    console.log(render)
    const gui = render.transform ?? DEFAULT_GUI
    const [rx, ry, rz] = gui.rotation, [tx, ty, tz] = gui.translation, [sx, sy, sz] = gui.scale

    const model = document.createElement("div")
    model.className = "model3d"
    // Как в игре: сдвиг, поворот, масштаб (в CSS читается справа налево: сначала масштаб)
    model.style.transform = `translate3d(${u(tx)}, ${u(-ty)}, ${u(tz)}) ` +
        `rotateX(${-rx}deg) rotateY(${ry}deg) rotateZ(${-rz}deg) scale3d(${sx}, ${sy}, ${sz})`

    render.elements.forEach((element, index) => {
        const [fx, fy, fz] = element.from, [tx2, ty2, tz2] = element.to
        const dims = [tx2 - fx, ty2 - fy, tz2 - fz]
        const center = [(fx + tx2) / 2 - 8, -((fy + ty2) / 2 - 8), (fz + tz2) / 2 - 8]
        const rotate = elementRotation(element.rotation)

        for (const [name, face] of Object.entries(element.faces)) {
            const info = FACES[name]
            const [w, h] = info.size(dims)
            const faceSe = document.createElement("div")
            faceSe.className = "face3d"
            // Повернуть элемент -> перенести в центр элемента -> развернуть грань наружу -> шагнуть вдоль нормали.
            // index * 0.01 — чуть выдвигаем грани следующих элементов, чтобы совпадающие плоскости не мерцали
            // (у травы поверх бока лежит отдельный элемент с окрашенной полоской)
            Object.assign(faceSe.style, {
                width: u(w), height: u(h), marginLeft: u(-w / 2), marginTop: u(-h / 2),
                transform: `${rotate} translate3d(${center.map(u).join(",")}) ${info.turn} ` +
                           `translateZ(${u(info.depth(dims) / 2 + index * 0.01)})`,
                filter: `brightness(${brightness(info.normal, gui).toFixed(2)})`,
            })
            faceSe.append(createFaceTexture(face, w, h))
            model.append(faceSe)
        }
    })
    return model
}