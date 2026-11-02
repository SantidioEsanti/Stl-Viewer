import webview
import trimesh
import numpy as np
import base64
import os
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


LIBS_DIR = os.path.join(get_app_dir(), 'libs')
MAX_FACES_FOR_VOLUME = 500_000


def _find_asset(*names):
    """Ищет ассет (логотип/иконку) в папке проекта и libs."""
    candidates = [
        get_app_dir(),
        LIBS_DIR,
        os.path.dirname(os.path.abspath(__file__)),
        os.getcwd(),
    ]
    for name in names:
        for base in candidates:
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return None


LOGO_PATH = _find_asset('logo.png', 'icon.png', 'logo.jpg')
ICON_PATH = _find_asset('icon.ico', 'logo.ico', 'app.ico')


class ViewerAPI:
    def __init__(self):
        self.current_path = None

    def open_file_dialog(self):
        try:
            win = webview.windows[0]
            result = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    '3D модели (*.stl;*.3mf;*.obj;*.ply;*.glb)',
                    'Все файлы (*.*)',
                )
            )
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception as e:
            print(f"[dialog] {e}")
            return None

    def load_mesh(self, path):
        try:
            self.current_path = path
            mesh = trimesh.load(path, force='mesh')

            if isinstance(mesh, trimesh.Scene):
                if len(mesh.geometry) == 0:
                    return {'error': 'Файл не содержит геометрии'}
                mesh = trimesh.util.concatenate(
                    list(mesh.geometry.values()))

            if not hasattr(mesh, 'vertices') or len(mesh.vertices) == 0:
                return {'error': 'Пустая модель'}

            n_faces = len(mesh.faces)
            verts = mesh.vertices.astype(np.float32)
            faces = mesh.faces.astype(np.uint32)

            # Нормали — считаем в Python, чтобы не блокировать UI
            normals_b64 = None
            try:
                normals = np.asarray(
                    mesh.vertex_normals, dtype=np.float32)
                normals_b64 = base64.b64encode(
                    normals.tobytes()).decode('ascii')
            except Exception as e:
                print(f"[normals] {e}")

            verts_b64 = base64.b64encode(verts.tobytes()).decode('ascii')
            faces_b64 = base64.b64encode(faces.tobytes()).decode('ascii')

            winding_ok = bool(mesh.is_winding_consistent)

            volume = None
            if n_faces < MAX_FACES_FOR_VOLUME:
                try:
                    if mesh.is_volume:
                        volume = round(float(mesh.volume), 2)
                except Exception:
                    pass

            extents = mesh.extents

            warnings = []
            if not winding_ok:
                warnings.append(
                    'Непоследовательные нормали — возможны артефакты при '
                    'печати')

            min_size = float(min(extents))
            max_size = float(max(extents))
            if min_size < 0.1:
                warnings.append(
                    f'Очень маленькая модель ({min_size:.3f} мм) — '
                    f'проверьте единицы (возможно, метры вместо мм)')
            if max_size > 300:
                warnings.append(
                    f'Модель больше типичного стола печати '
                    f'({max_size:.1f} мм)')

            if n_faces > 2_000_000:
                warnings.append(
                    f'Очень много треугольников ({n_faces:,}) — '
                    f'загрузка и печать могут быть медленными')

            diag = {
                'file_name': os.path.basename(path),
                'file_size_kb': round(os.path.getsize(path) / 1024, 1),
                'vertices': int(len(verts)),
                'triangles': int(n_faces),
                'winding_consistent': winding_ok,
                'volume_mm3': volume,
                'dimensions': [round(float(x), 2) for x in extents],
                'warnings': warnings,
            }

            return {
                'vertices': verts_b64,
                'normals': normals_b64,
                'faces': faces_b64,
                'vertex_count': int(len(verts)),
                'face_count': int(n_faces),
                'diagnostics': diag,
            }
        except Exception as e:
            return {'error': f'{type(e).__name__}: {e}'}


api = ViewerAPI()


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 100%; height: 100vh;
    background: #1f1f1f; color: #fff;
    font-family: "Segoe UI", sans-serif;
    overflow: hidden; user-select: none;
  }
  #app {
    display: grid;
    grid-template-columns: 280px 1fr;
    grid-template-rows: 60px 1fr;
    grid-template-areas:
      "topbar topbar"
      "side   viewer";
    height: 100vh;
  }
  .topbar {
    grid-area: topbar;
    display: flex; align-items: center; gap: 12px;
    padding: 0 16px;
    background: #1f1f1f; border-bottom: 1px solid #333;
  }
  .btn-oval {
    padding: 10px 22px; border-radius: 999px; border: none;
    background: #FFC107; color: #2a1f00;
    font-weight: 700; font-size: 13px; cursor: pointer;
    transition: background 0.12s, transform 0.08s;
    font-family: inherit; white-space: nowrap;
  }
  .btn-oval:hover { background: #FFD54F; }
  .btn-oval:active { background: #FFB300; transform: scale(0.97); }
  .btn-oval.secondary {
    background: transparent; color: #ddd;
    border: 1px solid #555; font-weight: 600;
  }
  .btn-oval.secondary:hover {
    background: #333; color: #fff; border-color: #888;
  }
  .file-name {
    color: #aaa; font-size: 13px; flex: 1;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .logo-img {
    height: 38px; width: 38px;
    border-radius: 50%;
    flex-shrink: 0;
    object-fit: contain;
    filter: drop-shadow(0 0 4px rgba(255,193,7,0.25));
  }
  .empty-state .logo-big {
    width: 120px; height: 120px;
    opacity: 0.85;
    border-radius: 50%;
    filter: drop-shadow(0 0 24px rgba(255,193,7,0.2));
  }

  .side {
    grid-area: side;
    background: #1a1a1a; border-right: 1px solid #333;
    overflow-y: auto; padding: 16px;
  }
  .side::-webkit-scrollbar { width: 8px; }
  .side::-webkit-scrollbar-track { background: #1a1a1a; }
  .side::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
  .side::-webkit-scrollbar-thumb:hover { background: #666; }

  .section-title {
    font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.6px; color: #888;
    margin-bottom: 10px; font-weight: 700;
  }
  .section-title:not(:first-child) {
    margin-top: 20px; padding-top: 16px;
    border-top: 1px solid #2a2a2a;
  }

  .stat-row {
    display: flex; justify-content: space-between;
    padding: 7px 0; border-bottom: 1px solid #222;
    font-size: 12px; gap: 8px;
  }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: #888; white-space: nowrap; }
  .stat-value {
    color: #fff; font-weight: 600; text-align: right;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .stat-value.warn { color: #FFC107; }
  .stat-value.ok   { color: #66bb6a; }

  .warn-list { display: flex; flex-direction: column; gap: 8px; }
  .warn-item {
    display: flex; gap: 10px; padding: 9px 11px;
    background: rgba(255, 193, 7, 0.08);
    border-left: 3px solid #FFC107;
    border-radius: 4px; font-size: 12px;
    color: #FFD54F; line-height: 1.45;
  }
  .warn-item.ok {
    background: rgba(76, 175, 80, 0.08);
    border-left-color: #4caf50; color: #81c784;
  }
  .warn-item .icon { flex-shrink: 0; font-size: 13px; }

  .btn-group { display: flex; flex-wrap: wrap; gap: 8px; }
  .btn-round {
    width: 42px; height: 42px; border-radius: 50%;
    border: none; background: #3a3a3a; color: #fff;
    font-size: 16px; font-weight: 700; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.12s, transform 0.08s, box-shadow 0.12s;
    flex-shrink: 0; font-family: inherit;
  }
  .btn-round:hover { background: #555; }
  .btn-round:active { background: #777; transform: scale(0.95); }
  .btn-round.active {
    background: #FFC107; color: #2a1f00;
    box-shadow: 0 0 8px rgba(255, 193, 7, 0.4);
  }
  .btn-round:disabled {
    opacity: 0.35; cursor: default; background: #2a2a2a;
  }
  .btn-round:disabled:hover { background: #2a2a2a; transform: none; }

  .btn-axis-x { background: #c0392b !important; color: #fff !important; }
  .btn-axis-x:hover { background: #e74c3c !important; }
  .btn-axis-x:active { background: #a93226 !important; }
  .btn-axis-x:disabled { background: #4a1a15 !important; opacity: 0.4; }

  .btn-axis-y { background: #27ae60 !important; color: #fff !important; }
  .btn-axis-y:hover { background: #2ecc71 !important; }
  .btn-axis-y:active { background: #1e8449 !important; }
  .btn-axis-y:disabled { background: #1a3d28 !important; opacity: 0.4; }

  .btn-axis-z { background: #2471a3 !important; color: #fff !important; }
  .btn-axis-z:hover { background: #3498db !important; }
  .btn-axis-z:active { background: #1a5276 !important; }
  .btn-axis-z:disabled { background: #1a2f3d !important; opacity: 0.4; }

  .slider-row {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 12px;
  }
  .slider-row label {
    width: 76px; font-size: 12px; color: #aaa;
    flex-shrink: 0;
  }
  .slider-row input[type=range] {
    flex: 1; -webkit-appearance: none; appearance: none;
    height: 4px; background: #333; border-radius: 2px; outline: none;
  }
  .slider-row input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 16px; height: 16px; background: #FFC107;
    border-radius: 50%; cursor: pointer;
    box-shadow: 0 0 4px rgba(255, 193, 7, 0.5);
  }
  .slider-row input[type=range]::-moz-range-thumb {
    width: 16px; height: 16px; background: #FFC107;
    border-radius: 50%; cursor: pointer; border: none;
  }
  .slider-value {
    width: 30px; text-align: right;
    font-size: 11px; color: #FFC107; font-weight: 700;
    flex-shrink: 0;
  }

  .viewer-wrap {
    grid-area: viewer; position: relative;
    background: #141414; overflow: hidden;
  }
  #canvas { width: 100%; height: 100%; display: block; }
  .empty-state {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 14px;
    color: #666; font-size: 14px;
    pointer-events: none; text-align: center; padding: 20px;
  }
  .empty-state .icon { font-size: 72px; opacity: 0.25; }
  .loading {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    background: rgba(20, 20, 20, 0.85);
    color: #FFC107; font-size: 14px; font-weight: 600;
    pointer-events: none; z-index: 10;
  }
</style>
</head>
<body>
<div id="app">
  <div class="topbar">
    <img src="logo.png" alt="Logo" class="logo-img"
         onerror="this.style.display='none'">
    <button class="btn-oval" id="btnOpen">📂 Открыть файл</button>
    <div class="file-name" id="fileName">Файл не загружен</div>
    <button class="btn-oval secondary" id="btnInfo">ℹ Справка</button>
  </div>

  <div class="side">
    <div class="section-title">Информация</div>
    <div id="stats">
      <div style="color:#666;font-size:12px;line-height:1.5;">
        Загрузите STL, 3MF, OBJ или GLB —<br>
        появится статистика.
      </div>
    </div>

    <div class="section-title">Ориентация</div>
    <div class="btn-group">
      <button class="btn-round btn-axis-x" id="btnRotX" disabled title="Поворот X +90°">X</button>
      <button class="btn-round btn-axis-y" id="btnRotY" disabled title="Поворот Y +90°">Y</button>
      <button class="btn-round btn-axis-z" id="btnRotZ" disabled title="Поворот Z +90°">Z</button>
      <button class="btn-round" id="btnResetRot" disabled title="Сброс (270° X)">↺</button>
    </div>

    <div class="section-title">Освещение</div>
    <div class="slider-row">
      <label>Резкость</label>
      <input type="range" id="sharpnessSlider" min="0" max="100" value="75">
      <span class="slider-value" id="sharpnessVal">75</span>
    </div>
    <div class="slider-row">
      <label>Контраст</label>
      <input type="range" id="contrastSlider" min="0" max="100" value="35">
      <span class="slider-value" id="contrastVal">35</span>
    </div>

    <div class="section-title">Вид</div>
    <div class="btn-group">
      <button class="btn-round" id="btnFit" disabled title="Вписать в экран">⤢</button>
      <button class="btn-round" id="btnWireframe" disabled title="Каркас">⬡</button>
      <button class="btn-round" id="btnRotate" disabled title="Авто-вращение">⟳</button>
      <button class="btn-round" id="btnGrid" disabled title="Сетка">▦</button>
      <button class="btn-round" id="btnScreenshot" disabled title="Скриншот">📷</button>
    </div>

    <div class="section-title">Диагностика</div>
    <div id="warnings" class="warn-list"></div>
  </div>

  <div class="viewer-wrap">
    <canvas id="canvas"></canvas>
    <div class="empty-state" id="emptyState">
      <img src="logo.png" alt="" class="logo-big"
           onerror="this.style.display='none'">
      <div>Нажмите «Открыть файл» наверху,<br>чтобы загрузить модель</div>
    </div>
    <div class="loading" id="loading" style="display:none;">⏳ Загрузка…</div>
  </div>
</div>

<script type="importmap">
{
  "imports": {
    "three": "./three.module.js",
    "three/addons/": "./addons/"
  }
}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ============================================================
// Renderer / Scene / Camera
// ============================================================
const canvas = document.getElementById('canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x141414);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
camera.position.set(150, 150, 150);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.09;
controls.screenSpacePanning = true;

// ============================================================
// Освещение
// ============================================================
const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
scene.add(ambientLight);

const dir1 = new THREE.DirectionalLight(0xffffff, 1.2);
dir1.position.set(1, 2, 1);
scene.add(dir1);

const dir2 = new THREE.DirectionalLight(0x88aaff, 0.5);
dir2.position.set(-1.5, 1, -1);
scene.add(dir2);

const dir3 = new THREE.DirectionalLight(0xffaa66, 0.3);
dir3.position.set(0, -1, 0.5);
scene.add(dir3);

let sharpness = 75;
let contrast = 35;
let lightingDirty = true;

function updateLighting() {
  const t = sharpness / 100;
  ambientLight.intensity = 0.8 - 0.6 * t;
  dir1.intensity = 0.5 + 1.5 * t;
  dir2.intensity = 0.2 + 0.6 * t;
  dir3.intensity = 0.1 + 0.35 * t;

  const c = contrast / 100;
  renderer.toneMappingExposure = 0.7 + c * 0.8;
}

// ============================================================
// Группы
// ============================================================
const modelGroup = new THREE.Group();
const axesGroup = new THREE.Group();
scene.add(modelGroup);
scene.add(axesGroup);

let grid = null;
let currentMesh = null;
let wireframeOn = false;
let autoRotate = false;
let gridEnabled = true;

// ============================================================
// Resize + render
// ============================================================
function resize() {
  const parent = canvas.parentElement;
  const w = parent.clientWidth;
  const h = parent.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);

function animate() {
  requestAnimationFrame(animate);
  if (autoRotate && modelGroup) {
    modelGroup.rotation.y += 0.005;
  }
  if (lightingDirty) {
    updateLighting();
    lightingDirty = false;
  }
  controls.update();
  renderer.render(scene, camera);
}

// ============================================================
// Утилиты
// ============================================================
function b64ToUint8(b64) {
  const bin = atob(b64);
  const len = bin.length;
  const arr = new Uint8Array(len);
  for (let i = 0; i < len; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

function disposeGroup(group) {
  while (group.children.length > 0) {
    const c = group.children[0];
    group.remove(c);
    if (c.geometry) c.geometry.dispose();
    if (c.material) {
      if (Array.isArray(c.material)) {
        c.material.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
      } else {
        if (c.material.map) c.material.map.dispose();
        c.material.dispose();
      }
    }
  }
}

function getGridParams(maxDim) {
  if (maxDim <= 30)  return { size: 40,  step: 2  };
  if (maxDim <= 80)  return { size: 100, step: 10 };
  if (maxDim <= 200) return { size: 300, step: 20 };
  return { size: 600, step: 50 };
}

// ============================================================
// ЦЕНТРОВКА: X/Z = 0, нижняя плоскость → Y = 0
// Геометрия локально центрирована, вращение вокруг (0,0,0)
// сохраняет центр. Достаточно скорректировать Y.
// ============================================================
function recenterModel() {
  if (!modelGroup || !currentMesh) return;
  modelGroup.position.set(0, 0, 0);
  modelGroup.updateMatrixWorld(true);
  const bbox = new THREE.Box3().setFromObject(modelGroup);
  if (bbox.isEmpty()) return;
  modelGroup.position.y = -bbox.min.y;
  modelGroup.updateMatrixWorld(true);
}

// Обновление controls.target (камера остаётся на месте)
function syncCameraTarget() {
  if (!currentMesh) return;
  const bbox = new THREE.Box3().setFromObject(modelGroup);
  if (bbox.isEmpty()) return;
  const center = new THREE.Vector3();
  bbox.getCenter(center);
  controls.target.copy(center);
  controls.update();
}

// ============================================================
// Сетка
// ============================================================
function setupGrid(maxDim) {
  if (grid) {
    scene.remove(grid);
    grid.geometry.dispose();
    grid.material.dispose();
    grid = null;
  }
  const { size, step } = getGridParams(maxDim);
  grid = new THREE.GridHelper(size, size / step, 0x666666, 0x2a2a2a);
  grid.material.transparent = true;
  grid.material.opacity = 0.85;
  grid.position.y = 0;
  grid.visible = gridEnabled;
  scene.add(grid);
}

function setupAxes(maxDim) {
  disposeGroup(axesGroup);
  const { size } = getGridParams(maxDim);
  const axisSize = Math.max(maxDim * 0.4, 5);
  const axesHelper = new THREE.AxesHelper(axisSize);
  axesHelper.material.depthTest = false;
  axesHelper.renderOrder = 999;
  axesGroup.add(axesHelper);
  axesGroup.position.set(-size / 2, 0.02, -size / 2);
}

// ============================================================
// Камера
// ============================================================
function fitCamera() {
  if (!currentMesh) return;
  const bbox = new THREE.Box3().setFromObject(modelGroup);
  if (bbox.isEmpty()) return;
  const size = bbox.getSize(new THREE.Vector3());
  const center = bbox.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const fov = camera.fov * (Math.PI / 180);
  const dist = (maxDim / (2 * Math.tan(fov / 2))) * 1.5;

  camera.position.set(
    center.x + dist * 0.8,
    center.y + dist * 0.7,
    center.z + dist * 0.8
  );
  camera.near = Math.max(0.01, maxDim / 1000);
  camera.far = maxDim * 100;
  camera.updateProjectionMatrix();
  camera.lookAt(center);

  controls.target.copy(center);
  controls.update();
}

// ============================================================
// Загрузка
// ============================================================
async function openFile() {
  const path = await window.pywebview.api.open_file_dialog();
  if (!path) return;
  await loadPath(path);
}

async function loadPath(path) {
  const t0 = performance.now();
  document.getElementById('loading').style.display = 'flex';
  try {
    const result = await window.pywebview.api.load_mesh(path);
    const tServer = performance.now();
    if (result.error) {
      alert('Не удалось загрузить модель:\n\n' + result.error);
      if (!currentMesh) {
        document.getElementById('emptyState').style.display = 'flex';
      }
      return;
    }
    displayMesh(result, tServer - t0);
  } catch (e) {
    alert('Ошибка: ' + e);
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
}

function displayMesh(data, serverMs) {
  const tRender0 = performance.now();

  disposeGroup(modelGroup);
  modelGroup.position.set(0, 0, 0);
  modelGroup.rotation.set(0, 0, 0);

  const vertsU8 = b64ToUint8(data.vertices);
  const facesU8 = b64ToUint8(data.faces);
  const verts = new Float32Array(vertsU8.buffer);
  const faces = new Uint32Array(facesU8.buffer);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
  geo.setIndex(new THREE.BufferAttribute(faces, 1));

  // Нормали из Python (если переданы), иначе — считаем сами
  if (data.normals) {
    try {
      const normalsU8 = b64ToUint8(data.normals);
      geo.setAttribute('normal',
        new THREE.BufferAttribute(new Float32Array(normalsU8.buffer), 3));
    } catch (e) {
      geo.computeVertexNormals();
    }
  } else {
    geo.computeVertexNormals();
  }

  geo.computeBoundingBox();
  const center = new THREE.Vector3();
  geo.boundingBox.getCenter(center);
  geo.translate(-center.x, -center.y, -center.z);

  const mat = new THREE.MeshStandardMaterial({
    color: 0xf0f0f0,
    metalness: 0.05,
    roughness: 0.65,
    wireframe: wireframeOn,
    flatShading: false,
  });

  const mesh = new THREE.Mesh(geo, mat);
  modelGroup.add(mesh);
  currentMesh = mesh;

  // Исходный поворот: 270° по X
  modelGroup.rotation.set(3 * Math.PI / 2, 0, 0);
  recenterModel();

  const bbox = new THREE.Box3().setFromObject(modelGroup);
  const sizeV = bbox.getSize(new THREE.Vector3());
  const maxDim = Math.max(sizeV.x, sizeV.y, sizeV.z) || 1;
  setupGrid(maxDim);
  setupAxes(maxDim);

  document.getElementById('emptyState').style.display = 'none';
  enableButtons(true);
  fitCamera();

  const renderMs = performance.now() - tRender0;
  updateStats(data.diagnostics, serverMs, renderMs);
}

// ============================================================
// Диагностика
// ============================================================
function truncate(s, n) {
  return s.length > n ? s.substring(0, n - 1) + '…' : s;
}

function fmtMs(ms) {
  if (ms < 1000) return Math.round(ms) + ' мс';
  return (ms / 1000).toFixed(2) + ' с';
}

function updateStats(diag, serverMs, renderMs) {
  document.getElementById('fileName').textContent = diag.file_name;
  const dims = diag.dimensions;
  const dimStr = `${dims[0]} × ${dims[1]} × ${dims[2]} мм`;

  let html = '';
  html += `<div class="stat-row"><span class="stat-label">Файл</span><span class="stat-value" title="${diag.file_name}">${truncate(diag.file_name, 24)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Размер</span><span class="stat-value">${diag.file_size_kb} КБ</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Загрузка</span><span class="stat-value">${fmtMs(serverMs + renderMs)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">└ Python</span><span class="stat-value" style="color:#aaa;font-weight:400;">${fmtMs(serverMs)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">└ Рендер</span><span class="stat-value" style="color:#aaa;font-weight:400;">${fmtMs(renderMs)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Треугольников</span><span class="stat-value">${diag.triangles.toLocaleString('ru-RU')}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Вершин</span><span class="stat-value">${diag.vertices.toLocaleString('ru-RU')}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Габариты</span><span class="stat-value">${dimStr}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Нормали</span><span class="stat-value ${diag.winding_consistent ? 'ok' : 'warn'}">${diag.winding_consistent ? '✓ ОК' : '✗ Проблема'}</span></div>`;
  if (diag.volume_mm3 !== null && diag.volume_mm3 !== undefined) {
    html += `<div class="stat-row"><span class="stat-label">Объём</span><span class="stat-value">${diag.volume_mm3.toLocaleString('ru-RU')} мм³</span></div>`;
  }
  document.getElementById('stats').innerHTML = html;

  const warnings = diag.warnings || [];
  const warnEl = document.getElementById('warnings');
  if (warnings.length === 0) {
    warnEl.innerHTML =
      '<div class="warn-item ok"><span class="icon">✓</span>' +
      '<span>Проблем не обнаружено</span></div>';
  } else {
    warnEl.innerHTML = warnings.map(w =>
      `<div class="warn-item"><span class="icon">⚠</span><span>${w}</span></div>`
    ).join('');
  }
}

// ============================================================
// Кнопки
// ============================================================
function enableButtons(on) {
  ['btnWireframe', 'btnRotate', 'btnFit', 'btnGrid', 'btnScreenshot',
   'btnRotX', 'btnRotY', 'btnRotZ', 'btnResetRot'].forEach(id => {
    document.getElementById(id).disabled = !on;
  });
}

document.getElementById('btnOpen').onclick = openFile;

document.getElementById('btnInfo').onclick = () => {
  alert(
    'STL / 3MF Viewer\n\n' +
    'Поддерживаемые форматы: STL, 3MF, OBJ, PLY, GLB.\n\n' +
    'Управление:\n' +
    '  • ЛКМ — вращение\n' +
    '  • ПКМ — панорама\n' +
    '  • Колесо — масштаб\n\n' +
    'После поворота модель центрируется:\n' +
    '  • X = 0, Z = 0\n' +
    '  • Y = 0 (низ на сетке)\n\n' +
    'Камера сохраняет положение — для перецентрирования нажмите ⤢'
  );
};

function rotateByAxis(axis) {
  if (!modelGroup) return;
  modelGroup.rotation[axis] =
    (modelGroup.rotation[axis] + Math.PI / 2) % (Math.PI * 2);
  recenterModel();
  syncCameraTarget();
}

document.getElementById('btnRotX').onclick = () => rotateByAxis('x');
document.getElementById('btnRotY').onclick = () => rotateByAxis('y');
document.getElementById('btnRotZ').onclick = () => rotateByAxis('z');
document.getElementById('btnResetRot').onclick = () => {
  if (!modelGroup) return;
  modelGroup.rotation.set(3 * Math.PI / 2, 0, 0);
  recenterModel();
  syncCameraTarget();
};

// Свет (throttle через rAF-флаг)
document.getElementById('sharpnessSlider').oninput = (e) => {
  sharpness = +e.target.value;
  document.getElementById('sharpnessVal').textContent = sharpness;
  lightingDirty = true;
};
document.getElementById('contrastSlider').oninput = (e) => {
  contrast = +e.target.value;
  document.getElementById('contrastVal').textContent = contrast;
  lightingDirty = true;
};

// Вид
document.getElementById('btnWireframe').onclick = () => {
  if (!currentMesh) return;
  wireframeOn = !wireframeOn;
  currentMesh.material.wireframe = wireframeOn;
  document.getElementById('btnWireframe').classList.toggle('active', wireframeOn);
};

document.getElementById('btnRotate').onclick = () => {
  autoRotate = !autoRotate;
  document.getElementById('btnRotate').classList.toggle('active', autoRotate);
};

document.getElementById('btnFit').onclick = fitCamera;

document.getElementById('btnGrid').onclick = () => {
  gridEnabled = !gridEnabled;
  if (grid) grid.visible = gridEnabled;
  document.getElementById('btnGrid').classList.toggle('active', !gridEnabled);
};

document.getElementById('btnScreenshot').onclick = () => {
  renderer.render(scene, camera);
  const url = canvas.toDataURL('image/png');
  const a = document.createElement('a');
  a.href = url;
  a.download = 'model_screenshot.png';
  a.click();
};

// Init
lightingDirty = true;
resize();
animate();
</script>
</body>
</html>
"""


class QuietHTTPHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_http_server(directory):
    handler = partial(QuietHTTPHandler, directory=directory)
    httpd = AppHTTPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    three_js = os.path.join(LIBS_DIR, 'three.module.js')
    orbit_js = os.path.join(LIBS_DIR, 'addons', 'controls', 'OrbitControls.js')

    if not os.path.exists(three_js) or not os.path.exists(orbit_js):
        missing = []
        if not os.path.exists(three_js):
            missing.append(f'  • {three_js}')
        if not os.path.exists(orbit_js):
            missing.append(f'  • {orbit_js}')
        msg = (
            "Не найдены файлы Three.js:\n\n" +
            "\n".join(missing) +
            "\n\nСкачайте их в папку libs/ (см. инструкцию в шапке "
            "комментария к viewer.py)."
        )
        print(msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror('Ошибка', msg)
            r.destroy()
        except Exception:
            pass
        return

    os.makedirs(LIBS_DIR, exist_ok=True)

    # Копируем логотип/иконку в libs/, чтобы HTML мог их запросить
    # как /logo.png и /icon.ico
    def _copy_asset(src, dst_name):
        if not src:
            return None
        dst = os.path.join(LIBS_DIR, dst_name)
        try:
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)
        except Exception as e:
            print(f"[asset] {dst_name}: {e}")
        return dst

    if not LOGO_PATH:
        print("[warn] logo.png не найден в папке проекта")
    if not ICON_PATH:
        print("[warn] icon.ico не найден — иконка окна не будет установлена")

    _copy_asset(LOGO_PATH, 'logo.png')
    _copy_asset(ICON_PATH, 'icon.ico')

    html_path = os.path.join(LIBS_DIR, 'viewer.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(HTML)

    port = start_http_server(LIBS_DIR)
    print(f"[server] http://127.0.0.1:{port}/viewer.html")

    webview.create_window(
        'STL / 3MF Viewer',
        url=f'http://127.0.0.1:{port}/viewer.html',
        width=1400,
        height=900,
        min_size=(900, 600),
        resizable=True,
        maximized=True,
        js_api=api,
    )
    def _set_icon_when_ready():
        """Ждёт окно и ставит иконку через WM_SETICON (Windows)."""
        import time
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except Exception:
            return

        hwnd = 0
        for _ in range(30):              # до ~15 сек
            time.sleep(0.5)
            hwnd = user32.FindWindowW(None, 'STL / 3MF Viewer')
            if hwnd:
                break
        if not hwnd:
            return

        ico = ICON_PATH or os.path.join(LIBS_DIR, 'icon.ico')
        if not os.path.exists(ico):
            return

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        hicon = user32.LoadImageW(
            0, ico, IMAGE_ICON, 0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if not hicon:
            return

        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        print("[icon] window icon set")

    threading.Thread(target=_set_icon_when_ready, daemon=True).start()
    webview.start(debug=False)


if __name__ == '__main__':
    main()