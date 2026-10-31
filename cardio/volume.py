"""Geometry-aware threshold reconstruction for research and synthetic demos.

Coordinates are voxel centres. Voxel boundary surfaces use +/- 0.5 borders.
This module never reads reference masks during segmentation or meshing, and
does not assign anatomical labels or claim a learned segmentation model.
"""
import hashlib
import itertools
import json
import math
import time
from pathlib import Path

import numpy as np


MAX_VOLUME_VOXELS = 32_000_000
MAX_SURFACE_QUADS = 40_000


def _unknown_geometry(shape, issues):
    return {"verified": False, "units": "voxel", "spacing_xyz": [1., 1., 1.],
            "spacing_mm": None, "origin_xyz": [0., 0., 0.],
            "direction_xyz": np.eye(3).tolist(), "coordinate_system": "voxel_index",
            "shape_zyx": list(shape), "sorted_by_position": False,
            "source": "unverified_image_geometry", "issues": list(issues),
            "notice": "體素索引不代表毫米或病人座標；沒有由不完整標頭推定間距。"}


def _numbers(value, size):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != size:
        raise ValueError("座標或方向的維度不符。")
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError("座標、方向與間距必須為有限數值。")
    return array


def assemble_geometry(parsed):
    """Return ordered (array, metadata) pairs and validated uniform geometry.

    DICOM IOP[0:3] is increasing image column x and IOP[3:6] is increasing
    image row y; PixelSpacing is [row y, column x]. For incomplete, mixed,
    duplicate or nonuniform geometry the input order is preserved and physical
    units are withheld. This consistency check does not verify scan coverage.
    """
    parsed = list(parsed)
    if not parsed:
        return parsed, _unknown_geometry([], ["尚未提供影像序列。"])
    try:
        shapes = [np.asarray(array).shape for array, _ in parsed]
        if any(len(shape) != 2 for shape in shapes) or len(set(shapes)) != 1:
            raise ValueError("影像序列的二維尺寸不一致。")
        shape = (len(parsed), *shapes[0])
        if len(parsed) < 2:
            raise ValueError("單張影像無法確認切片間距。")
        geometries = [metadata.get("geometry") for _, metadata in parsed]
        if any(not isinstance(g, dict) for g in geometries):
            raise ValueError("缺少完整 DICOM 位置、方向、間距與序列識別。")
        for key in ("series_hash", "study_hash", "frame_hash"):
            ids = [g.get(key) for g in geometries]
            if any(not isinstance(value, str) or len(value) != 64 or
                   any(c not in "0123456789abcdef" for c in value) for value in ids):
                raise ValueError("DICOM 序列、研究或參考座標識別不完整。")
            if len(set(ids)) != 1:
                raise ValueError("影像混有不同 DICOM 序列、研究或參考座標。")
        spacings, orientations, positions = [], [], []
        for (array, _), g in zip(parsed, geometries):
            if g.get("rows") != array.shape[0] or g.get("columns") != array.shape[1]:
                raise ValueError("DICOM 標頭尺寸與像素不一致。")
            spacing = _numbers(g.get("pixel_spacing_yx"), 2)
            if np.any(spacing <= 0) or np.any(spacing > 1000):
                raise ValueError("DICOM PixelSpacing 無效。")
            orientation = _numbers(g.get("orientation"), 6)
            xaxis, yaxis = orientation[:3], orientation[3:]
            if not np.isclose(np.linalg.norm(xaxis), 1., atol=1e-4) or not np.isclose(np.linalg.norm(yaxis), 1., atol=1e-4) or abs(float(np.dot(xaxis, yaxis))) > 1e-4:
                raise ValueError("DICOM 方向不是單位正交向量。")
            position = _numbers(g.get("position_xyz"), 3)
            if np.any(np.abs(position) > 1e7):
                raise ValueError("DICOM 位置超出支援範圍。")
            spacings.append(spacing); orientations.append(orientation); positions.append(position)
        if not all(np.allclose(s, spacings[0], rtol=1e-5, atol=1e-5) for s in spacings):
            raise ValueError("DICOM 平面間距不一致。")
        if not all(np.allclose(o, orientations[0], rtol=0, atol=1e-4) for o in orientations):
            raise ValueError("DICOM 切片方向不一致。")
        xaxis = orientations[0][:3] / np.linalg.norm(orientations[0][:3])
        yaxis = orientations[0][3:] / np.linalg.norm(orientations[0][3:])
        normal = np.cross(xaxis, yaxis)
        normal /= np.linalg.norm(normal)
        positions = np.asarray(positions)
        projections = positions @ normal
        order = np.argsort(projections, kind="stable")
        sorted_positions, locations = positions[order], projections[order]
        differences = np.diff(locations)
        if np.any(differences <= 1e-4):
            raise ValueError("DICOM 切片位置重複或間距過小。")
        distance = float(np.median(differences))
        if distance > 1000 or not np.allclose(differences, distance, rtol=1e-3, atol=1e-3):
            raise ValueError("DICOM 切片位置不等距，不能套用單一三維體素間距。")
        expected = sorted_positions[0] + (locations - locations[0])[:, None] * normal
        if not np.allclose(sorted_positions, expected, rtol=0, atol=1e-3):
            raise ValueError("DICOM 切片具有平面內偏移／剪切，本版不假設可直接正交堆疊。")
        direction = np.column_stack((xaxis, yaxis, normal))
        geometry = {"verified": True, "units": "mm",
                    "spacing_xyz": [float(spacings[0][1]), float(spacings[0][0]), distance],
                    "origin_xyz": sorted_positions[0].tolist(), "direction_xyz": direction.tolist(),
                    "coordinate_system": "DICOM_LPS", "shape_zyx": list(shape),
                    "sorted_by_position": True, "source": "consistent_dicom_headers",
                    "series_hash": geometries[0]["series_hash"],
                    "study_hash": geometries[0]["study_hash"],
                    "frame_hash": geometries[0]["frame_hash"], "issues": [],
                    "notice": "已核對標頭空間一致性；未證明影像完整掃描範圍、解剖標記或臨床適用性。"}
        geometry["geometry_sha256"] = hashlib.sha256(json.dumps(geometry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return [parsed[int(index)] for index in order], geometry
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
        # Only static descriptions are returned; never echo malformed coordinates
        # containing NaN, patient strings, or source UIDs into serializable output.
        issue = str(exc) or "影像空間資訊不完整。"
        if any(token in issue for token in ("could not convert", "invalid literal", "not supported", "NoneType")):
            issue = "DICOM 空間資訊格式無效，未建立實體座標。"
        shape = (len(parsed), *np.asarray(parsed[0][0]).shape) if parsed and isinstance(parsed[0], (tuple, list)) and len(parsed[0]) else []
        return parsed, _unknown_geometry(shape, [issue])


def _resolve_geometry(case, shape):
    candidate = case.get("geometry") if isinstance(case, dict) else None
    if not isinstance(candidate, dict) or candidate.get("verified") is not True or candidate.get("units") != "mm":
        return _unknown_geometry(shape, ["未驗證完整空間資訊，重建僅使用體素索引。"])
    spacing = _numbers(candidate.get("spacing_xyz"), 3)
    origin = _numbers(candidate.get("origin_xyz"), 3)
    direction = np.asarray(candidate.get("direction_xyz"), dtype=np.float64)
    if np.any(spacing <= 0) or np.any(spacing > 1000) or np.any(np.abs(origin) > 1e7) or direction.shape != (3, 3) or not np.isfinite(direction).all() or not np.allclose(direction.T @ direction, np.eye(3), atol=1e-4) or not np.isclose(np.linalg.det(direction), 1., atol=1e-4):
        raise ValueError("已宣告的三維幾何無效，拒絕產生毫米座標。")
    if candidate.get("shape_zyx") is not None and list(candidate["shape_zyx"]) != list(shape):
        raise ValueError("三維幾何尺寸與目前序列不符。")
    coordinate_system = candidate.get("coordinate_system")
    if coordinate_system not in {"DICOM_LPS", "synthetic_world"}:
        raise ValueError("不支援的實體座標系。")
    if coordinate_system == "synthetic_world" and case.get("synthetic") is not True:
        raise ValueError("合成座標只能用於明確標示的合成體模。")
    return {**candidate, "spacing_xyz": spacing.tolist(), "origin_xyz": origin.tolist(),
            "direction_xyz": direction.tolist(), "shape_zyx": list(shape)}


def _pool_mask(mask, step):
    padded = np.pad(mask, [(0, (-n) % s) for n, s in zip(mask.shape, step)], constant_values=False)
    nz, ny, nx = (padded.shape[i] // step[i] for i in range(3))
    return padded.reshape(nz, step[0], ny, step[1], nx, step[2]).any(axis=(1, 3, 5))


def _exposed_faces(mask):
    result = []
    for axis in range(3):
        for side in (-1, 1):
            neighbour = np.zeros_like(mask)
            target, source = [slice(None)] * 3, [slice(None)] * 3
            target[axis] = slice(1, None) if side == -1 else slice(None, -1)
            source[axis] = slice(None, -1) if side == -1 else slice(1, None)
            neighbour[tuple(target)] = mask[tuple(source)]
            result.append((axis, side, np.argwhere(mask & ~neighbour)))
    return result


def _mesh(mask, geometry, max_axis):
    shape = np.asarray(mask.shape, dtype=int)
    step = np.maximum(1, np.ceil(shape / max_axis).astype(int))
    while True:
        coarse = _pool_mask(mask, step)
        exposed = _exposed_faces(coarse)
        quads = sum(len(points) for _, _, points in exposed)
        if quads <= MAX_SURFACE_QUADS:
            break
        step = np.minimum(shape, np.maximum(step + 1, np.ceil(step * 1.25).astype(int)))
    lookup, vertices, faces = {}, [], []
    direction, spacing, origin = np.asarray(geometry["direction_xyz"]), np.asarray(geometry["spacing_xyz"]), np.asarray(geometry["origin_xyz"])
    def vertex(point):
        key = tuple(int(v) for v in point)
        if key not in lookup:
            lookup[key] = len(vertices)
            world = origin + direction @ ((np.asarray(key, dtype=float) - .5) * spacing)
            vertices.append(world.tolist())
        return lookup[key]
    for axis, side, points in exposed:
        for z, y, x in points:
            z0, y0, x0 = (np.asarray([z, y, x]) * step).tolist()
            z1, y1, x1 = np.minimum((np.asarray([z, y, x]) + 1) * step, shape).tolist()
            if axis == 2 and side == -1:
                corners = [(x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0)]
            elif axis == 2:
                corners = [(x1,y0,z0),(x1,y1,z0),(x1,y1,z1),(x1,y0,z1)]
            elif axis == 1 and side == -1:
                corners = [(x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1)]
            elif axis == 1:
                corners = [(x0,y1,z0),(x0,y1,z1),(x1,y1,z1),(x1,y1,z0)]
            elif side == -1:
                corners = [(x0,y0,z0),(x0,y1,z0),(x1,y1,z0),(x1,y0,z0)]
            else:
                corners = [(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
            a,b,c,d = [vertex(point) for point in corners]
            faces.extend(([a,b,c],[a,c,d]))
    return {"vertices": vertices, "faces": faces, "units": geometry["units"],
            "coordinate_system": geometry["coordinate_system"], "method": "exposed_voxel_boundary",
            "step_zyx": step.tolist(), "coarse_shape_zyx": list(coarse.shape),
            "surface_quads": int(quads), "triangles": len(faces),
            "decimated": bool(np.any(step > 1)), "topology_verified": False,
            "notice": "表面由分割體素邊界組成；粗化網格供顯示，非已驗證的解剖或手術導航模型。"}


def _bounds(mask, geometry):
    if not mask.any():
        return None
    limits = []
    for axis in range(3):
        indices = np.flatnonzero(mask.any(axis=tuple(i for i in range(3) if i != axis)))
        limits.append((float(indices[0]) - .5, float(indices[-1]) + .5))
    points = np.asarray([[x,y,z] for z,y,x in itertools.product(*limits)])
    world = np.asarray(geometry["origin_xyz"]) + (points * np.asarray(geometry["spacing_xyz"])) @ np.asarray(geometry["direction_xyz"]).T
    return {"min_xyz": world.min(axis=0).tolist(), "max_xyz": world.max(axis=0).tolist(), "units": geometry["units"]}


def reconstruct(arrays, case, threshold, max_axis=64, mask=None, mask_metadata=None):
    started = time.perf_counter()
    if isinstance(threshold, (bool, str)):
        raise ValueError("閾值必須為有限數值。")
    threshold = float(threshold)
    if not math.isfinite(threshold):
        raise ValueError("閾值必須為有限數值。")
    if type(max_axis) is not int or not 4 <= max_axis <= 128:
        raise ValueError("網格最大軸尺寸須為 4–128 的整數。")
    arrays = list(arrays)
    if len(arrays) < 2 or len({np.asarray(a).shape for a in arrays}) != 1 or any(np.asarray(a).ndim != 2 for a in arrays):
        raise ValueError("三維重建至少需要兩張同尺寸的二維切片。")
    shape = (len(arrays), *np.asarray(arrays[0]).shape)
    if min(shape) < 1 or math.prod(shape) > MAX_VOLUME_VOXELS:
        raise ValueError("體積超過支援範圍（3,200 萬體素）。")
    volume = np.asarray(arrays, dtype=np.float32)
    if not np.isfinite(volume).all():
        raise ValueError("體積包含無效數值。")
    geometry = _resolve_geometry(case, shape)
    supplied_mask = mask is not None
    if mask is None:
        mask = volume >= threshold
        segmentation = {"method": "intensity_threshold", "threshold": threshold,
                        "comparison": "greater_or_equal", "trained": False,
                        "anatomical_label": None, "class_label_verified": False}
    else:
        supplied = np.asarray(mask)
        if supplied.shape != volume.shape or supplied.dtype.kind != "b":
            raise ValueError("分割遮罩須為與原序列同尺寸的布林陣列。")
        if mask_metadata is None:
            mask_metadata = {}
        if not isinstance(mask_metadata, dict):
            raise ValueError("分割來源與前處理紀錄須為物件。")
        try:
            serialized = json.dumps(mask_metadata, ensure_ascii=False, allow_nan=False)
            if len(serialized.encode("utf-8")) > 65536:
                raise ValueError("分割來源紀錄過大。")
            metadata = json.loads(serialized)
        except (ValueError, TypeError) as exc:
            raise ValueError("分割來源紀錄格式無效。") from exc
        mask = supplied.copy()
        segmentation = {"method": metadata.get("method") if metadata.get("method") in {"registered_onnx", "provided_research_labels"} else "provided_segmentation_mask",
                        "threshold": None, "comparison": None, "trained": metadata.get("trained") is True,
                        "anatomical_label": None, "class_label_verified": False,
                        "declared_class_label": metadata.get("class_label"), "metadata": metadata}
    segmentation.update(mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(), reference_used_for_inference=False)
    foreground = int(np.count_nonzero(mask))
    mesh = _mesh(mask, geometry, max_axis)
    statistics = {"foreground_voxels": foreground, "total_voxels": int(mask.size),
                  "foreground_fraction": foreground / mask.size, "shape_zyx": list(shape),
                  "physical_volume_mm3": float(foreground * np.prod(geometry["spacing_xyz"])) if geometry["verified"] else None,
                  "bounds": _bounds(mask, geometry), "mesh_voxels": int(np.count_nonzero(_pool_mask(mask, np.asarray(mesh["step_zyx"])))),
                  "volume_source": "original_resolution_segmentation_mask" if supplied_mask else "original_resolution_threshold_mask",
                  "units": geometry["units"]}
    return {"schema": "cardio-volume/1", "geometry": geometry, "mesh": mesh,
            "statistics": statistics, "segmentation": segmentation,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "notice": "三維顯示為研究分割表面，不是已驗證的心臟／血管解剖還原；不得用於手術導航或手術適應症判定。"}


def _validated_mesh(mesh):
    if mesh.get("units") not in {"mm", "voxel"} or mesh.get("coordinate_system") not in {"DICOM_LPS", "voxel_index", "synthetic_world"}:
        raise ValueError("網格單位或座標系未標示。")
    vertices = np.asarray(mesh.get("vertices"), dtype=np.float64)
    faces = np.asarray(mesh.get("faces"))
    if vertices.size == 0 and faces.size == 0:
        return np.empty((0,3)), np.empty((0,3), dtype=int)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all() or faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer) or np.any(faces < 0) or np.any(faces >= len(vertices)) or len(faces) > MAX_SURFACE_QUADS * 2:
        raise ValueError("網格座標或三角形索引無效。")
    if mesh.get("units") not in {"mm", "voxel"}:
        raise ValueError("網格單位未標示。")
    return vertices, faces.astype(int)


def mesh_to_obj(mesh):
    vertices, faces = _validated_mesh(mesh)
    lines = ["# CardioAgent research segmentation surface; not anatomical/surgical navigation", f"# units: {mesh.get('units')}; coordinate system: {mesh.get('coordinate_system')}"]
    lines.extend("v " + " ".join(f"{v:.17g}" for v in point) for point in vertices)
    lines.extend("f " + " ".join(str(int(i) + 1) for i in face) for face in faces)
    return "\n".join(lines) + "\n"


def mesh_to_stl(mesh):
    vertices, faces = _validated_mesh(mesh)
    label = "CardioAgent_research_" + mesh.get("units", "unknown")
    lines = ["solid " + label]
    for face in faces:
        points = vertices[face]
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        length = float(np.linalg.norm(normal))
        if length <= 1e-12:
            raise ValueError("網格含退化三角形。")
        normal /= length
        lines.append("  facet normal " + " ".join(f"{value:.17g}" for value in normal))
        lines.append("    outer loop")
        lines.extend("      vertex " + " ".join(f"{value:.17g}" for value in point) for point in points)
        lines.extend(("    endloop", "  endfacet"))
    lines.append("endsolid " + label)
    return "\n".join(lines) + "\n"


def generate_phantom(data):
    """Create deterministic labelled synthetic geometry, never a patient model."""
    data = Path(data).resolve()
    image_dir, reference_dir = data / "images/demo-volume-phantom", data / "reference"
    image_dir.mkdir(parents=True, exist_ok=True); reference_dir.mkdir(parents=True, exist_ok=True)
    z,y,x = np.mgrid[:32,:96,:96]
    outer = ((x-48)/25)**2 + ((y-48)/30)**2 + ((z-15.5)/13)**2 <= 1
    inner = ((x-48)/16)**2 + ((y-48)/20)**2 + ((z-15.5)/9)**2 < 1
    reference = outer & ~inner
    random = np.random.default_rng(20261031)
    pixels = np.clip(random.normal(12, 2, reference.shape), 4, 20).astype(np.float32)
    pixels[inner] = random.normal(40, 2, int(inner.sum())).astype(np.float32)
    pixels[reference] = random.normal(180, 3, int(reference.sum())).astype(np.float32)
    foci = (((x-26)**2 + (y-48)**2 + ((z-15.5)*1.6)**2 < 3.6**2) |
            ((x-70)**2 + (y-48)**2 + ((z-15.5)*1.6)**2 < 3.6**2)) & reference
    pixels[foci] = 255
    reference_path = reference_dir / "demo-volume-phantom-mask.npy"
    np.save(reference_path, reference, allow_pickle=False)
    slices, hashes = [], []
    for index, image in enumerate(pixels):
        path = image_dir / f"{index:04}.npy"
        np.save(path, image, allow_pickle=False)
        slices.append(path.relative_to(data).as_posix())
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    geometry = {"verified": True, "units": "mm", "spacing_xyz": [1.25,1.25,2.0],
                "origin_xyz": [0.,0.,0.], "direction_xyz": np.eye(3).tolist(),
                "coordinate_system": "synthetic_world", "shape_zyx": [32,96,96],
                "sorted_by_position": False, "source": "known_synthetic_geometry", "issues": [],
                "notice": "毫米間距由合成體模設計指定，並非病人量測或 DICOM 臨床校正。"}
    return {"id": "demo-volume-phantom", "label": "3D 合成心臟形體模 · 幾何與重建驗證",
            "modality": "CT", "is_hu": False, "synthetic": True,
            "source": "deterministic analytic ellipsoid-shell phantom; not a patient or anatomical heart",
            "source_detail": "以外／內橢球差集建立合成殼層，加入固定亂數灰階與兩處亮區。標籤由解析幾何獨立建立，不由閾值結果產生。", 
            "slices": slices, "slice_sha256": hashes, "reference_boxes": {}, "geometry": geometry,
            "reference_mask": reference_path.relative_to(data).as_posix(),
            "reference_model": {"kind": "analytic_ellipsoid_shell", "version": "1", "seed": 20261031,
                "target": "synthetic_shell", "expected_threshold": 120,
                "reference_voxels": int(reference.sum()),
                "reference_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                "continuous_ellipsoid_difference_voxels": float(4/3*math.pi*(25*30*13-16*20*9)),
                "notice": "僅供體素尺度、重建、疊圖與評估程式驗證；Dice 結果不代表臨床分割能力。"}}
