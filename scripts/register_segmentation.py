"""Register an explicitly supplied segmentation ONNX; never download weights."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cardio.imaging import sha256
from cardio.segmentation import validate_manifest, validate_model


def register(source, root, manifest):
    source, root = Path(source).resolve(), Path(root).resolve()
    manifest = validate_manifest(manifest)
    if not source.is_file() or source.suffix.lower() != ".onnx" or sha256(source) != manifest["sha256"]:
        raise ValueError("來源 ONNX 不存在或 SHA256 不符。")
    directory = root / "models"
    directory.mkdir(parents=True, exist_ok=True)
    staged_model = staged_manifest = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".onnx", prefix=".segmentation-", delete=False) as temp:
            staged_model = Path(temp.name)
        shutil.copyfile(source, staged_model)
        if sha256(staged_model) != manifest["sha256"]:
            raise ValueError("複製後的模型 SHA256 不符，來源可能已變更。")
        session = validate_model(staged_model, manifest, "CPUExecutionProvider")
        del session
        destination = directory / ("segmentation-" + manifest["sha256"] + ".onnx")
        if destination.exists():
            if sha256(destination) != manifest["sha256"]:
                raise ValueError("既有雜湊模型檔遭更動，請先人工查核。")
        else:
            os.replace(staged_model, destination)
            staged_model = None
        manifest["file"] = destination.name
        with tempfile.NamedTemporaryFile("w", dir=directory, suffix=".json", prefix=".segmentation-", encoding="utf-8", delete=False) as file:
            staged_manifest = Path(file.name)
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged_manifest, directory / "segmentation.json")
        staged_manifest = None
        return manifest
    finally:
        for path in (staged_model, staged_manifest):
            if path is not None:
                path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--name", default="Registered cardiac segmentation")
    parser.add_argument("--trained", action="store_true", help="Explicit source declaration; not clinical validation")
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--modality", choices=("CT", "MRI"), required=True)
    parser.add_argument("--input-height", type=int, required=True)
    parser.add_argument("--input-width", type=int, required=True)
    parser.add_argument("--window", type=float, nargs=2, metavar=("LOW", "HIGH"), required=True)
    parser.add_argument("--target-class", type=int, required=True)
    parser.add_argument("--class-labels", required=True, help='JSON array, e.g. ["background","myocardium"]')
    args = parser.parse_args(argv)
    try:
        manifest = {"file": "pending.onnx", "sha256": args.sha256, "name": args.name,
                    "trained": args.trained, "provenance": args.provenance, "modality": args.modality,
                    "input_size": [args.input_height, args.input_width], "window": args.window,
                    "target_class": args.target_class, "class_labels": json.loads(args.class_labels),
                    "output": "logits-nchw"}
        saved = register(args.model, ROOT, manifest)
        print(json.dumps({"registered": True, "name": saved["name"], "sha256": saved["sha256"],
                          "target_class": saved["target_class"], "anatomical_label": saved["class_labels"][saved["target_class"]],
                          "clinical_accuracy_verified": False, "weights_downloaded": False}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        parser.exit(2, "分割模型未登錄：" + str(exc)[:500] + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
