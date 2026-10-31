"""Verified supplied research labels; never a learned prediction or named anatomy."""
import copy
import hashlib
import json
from pathlib import Path
import re
import os
import tempfile

import numpy as np

from .imaging import sha256


def _safe_path(base, relative):
    base = Path(base).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("參考標籤與影像必須使用資料目錄內的相對路徑。")
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError("參考標籤或影像路徑超出資料目錄。")
    return path


def _meta(value):
    if not isinstance(value, dict) or value.get("schema") != "cardio-provided-reference-labels/1":
        raise ValueError("參考標籤來源資訊格式無效。")
    for field in ("sha256", "array_sha256", "source_hdf5_sha256"):
        if not isinstance(value.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
            raise ValueError("參考標籤來源 SHA256 格式無效。")
    shape = value.get("shape_zyx")
    if (not isinstance(shape, list) or len(shape) != 3 or
            any(type(n) is not int or n < 1 for n in shape) or np.prod(shape) > 32_000_000):
        raise ValueError("參考標籤尺寸資訊無效。")
    if (value.get("dtype") != "uint8" or value.get("class_values") != [0, 1, 2, 3] or
            value.get("foreground_values") != [1, 2, 3] or value.get("case_id") != "research-mri-01"):
        raise ValueError("參考標籤資料類型、類別或病例識別不符合來源。")
    alignment = value.get("image_alignment")
    slices = alignment.get("slices") if isinstance(alignment, dict) else None
    if not isinstance(alignment, dict) or alignment.get("verified") is not True or not isinstance(slices, list) or len(slices) != shape[0]:
        raise ValueError("參考標籤缺少已核對的逐片影像資訊。")
    for i, item in enumerate(slices):
        if not isinstance(item, dict) or item.get("index") != i:
            raise ValueError("參考影像順序資訊無效。")
        for field in ("file_sha256", "array_sha256"):
            if not isinstance(item.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", item[field]):
                raise ValueError("參考影像 SHA256 格式無效。")
    return value


def _read_labels(path, meta):
    if not path.is_file() or sha256(path) != meta["sha256"]:
        raise ValueError("參考標籤檔案不存在或 SHA256 不符。")
    try:
        labels = np.load(path, allow_pickle=False)
    except Exception as exc:
        raise ValueError("參考標籤不是有效的 NPY 數值陣列。") from exc
    if labels.dtype != np.uint8 or list(labels.shape) != meta["shape_zyx"]:
        raise ValueError("參考標籤尺寸或資料類型不符合來源。")
    values, counts = np.unique(labels, return_counts=True)
    actual_counts = {str(int(v)): int(c) for v, c in zip(values, counts)}
    if values.tolist() != meta["class_values"] or actual_counts != meta.get("class_counts"):
        raise ValueError("參考標籤的類別或數量不符合來源。")
    if hashlib.sha256(np.ascontiguousarray(labels).tobytes()).hexdigest() != meta["array_sha256"]:
        raise ValueError("參考標籤陣列雜湊不符。")
    return labels


def _verify_images(data, slices, meta):
    expected = meta["image_alignment"]["slices"]
    if not isinstance(slices, list) or len(slices) != len(expected):
        raise ValueError("本次 MRI 切片數與參考標籤不同。")
    for relative, item in zip(slices, expected):
        path = _safe_path(data, relative)
        if not path.is_file() or sha256(path) != item["file_sha256"]:
            raise ValueError("本次 MRI 與參考標籤的來源影像雜湊不同。")
        try:
            image = np.load(path, allow_pickle=False)
        except Exception as exc:
            raise ValueError("參考 MRI 影像無法讀取。") from exc
        if list(image.shape) != meta["shape_zyx"][1:] or image.dtype != np.float32 or not np.isfinite(image).all():
            raise ValueError("參考 MRI 影像尺寸或數值不符合來源。")
        digest = hashlib.sha256(np.ascontiguousarray(image, dtype=np.float32).tobytes()).hexdigest()
        if digest != item["array_sha256"]:
            raise ValueError("參考 MRI 像素與標籤的對齊來源不符。")


def verify_reference_assets(root):
    """Read and verify bundled labels/aligned pixels without writing a data directory."""
    root = Path(root).resolve()
    metadata_file = _safe_path(root, "assets/mri/reference_labels_meta.json")
    try:
        metadata = _meta(json.loads(metadata_file.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("原始參考標籤來源資訊無法讀取。") from exc
    source = _safe_path(root, "assets/mri/reference_labels.npy")
    _read_labels(source, metadata)
    asset_slices = ["assets/mri/" + item["asset_name"] for item in metadata["image_alignment"]["slices"]]
    _verify_images(root, asset_slices, metadata)
    return copy.deepcopy(metadata)


def prepare_reference_labels(data, root):
    root, data = Path(root).resolve(), Path(data).resolve()
    metadata = verify_reference_assets(root)
    source = _safe_path(root, "assets/mri/reference_labels.npy")
    relative = "reference/research-mri-01-labels.npy"
    destination = _safe_path(data, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _read_labels(destination, metadata)
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".labels-", suffix=".npy", delete=False) as file:
                temporary = Path(file.name)
                file.write(source.read_bytes())
                file.flush()
                os.fsync(file.fileno())
            if sha256(temporary) != metadata["sha256"]:
                raise ValueError("複製後參考標籤 SHA256 不符。")
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"provided_labels": relative, "provided_labels_meta": copy.deepcopy(metadata)}


def load_reference_mask(data, case, class_value=0):
    if not isinstance(case, dict):
        raise ValueError("請提供本次研究病例資訊。")
    if type(class_value) is not int or class_value not in (0, 1, 2, 3):
        raise ValueError("參考標籤選擇只接受 0（1–3 聯集）、1、2 或 3。")
    metadata = _meta(case.get("provided_labels_meta"))
    if case.get("id") != metadata["case_id"] or case.get("modality") != "MRI":
        raise ValueError("參考標籤與本次病例或影像類型不一致。")
    data = Path(data).resolve()
    labels = _read_labels(_safe_path(data, case.get("provided_labels")), metadata)
    _verify_images(data, case.get("slices"), metadata)
    target_values = [1, 2, 3] if class_value == 0 else [class_value]
    mask = np.isin(labels, target_values)
    return {"mask": mask, "metadata": {
        "method": "provided_research_labels", "class_value": class_value,
        "target_values": target_values, "selected_voxels": int(mask.sum()),
        "model": None, "inference_executed": False, "trained_declared": False,
        "label_mapping_verified": False, "anatomical_label": None,
        "annotation_authorship_verified": False, "accuracy_verified": False,
        "geometry_verified": False, "units": "voxel", "spacing_mm": None,
        "source_sha256": metadata["source_hdf5_sha256"],
        "source_hdf5_sha256": metadata["source_hdf5_sha256"], "label_sha256": metadata["sha256"],
        "source_dataset": "label", "source_role": "provided_research_labels",
        "image_alignment_verified": True, "shape_zyx": metadata["shape_zyx"],
        "class_counts": copy.deepcopy(metadata["class_counts"]),
        "notice": "原始研究檔案提供的參考標籤，非 AI 模型推論；類別解剖名稱、標註者及物理間距未確認。",
    }}
