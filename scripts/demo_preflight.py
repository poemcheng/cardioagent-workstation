"""Read-only, loopback-only competition readiness report; no model downloads."""
import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CORE_PACKAGES = ("Flask", "waitress", "numpy", "Pillow", "pydicom")


def inspect(probe_service=True):
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "packages": {},
        "core_import_errors": {},
        "local_only": True,
        "npu_execution_verified": False,
        "amd_hardware_verified": False,
        "clinical_validation_completed": False,
        "weights_bundled": False,
        "downloads_performed": False,
    }
    for name in CORE_PACKAGES + ("onnxruntime", "onnxruntime-directml", "onnxruntime-genai"):
        try:
            report["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][name] = None
    for name in ("flask", "waitress", "numpy", "PIL", "pydicom"):
        try:
            importlib.import_module(name)
        except Exception as exc:
            report["core_import_errors"][name] = str(exc)[:300]
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    report["configured"] = {
        "lemonade_url": config.get("lemonade_url"),
        "planner_model": config.get("planner_model", ""),
        "vision_provider": config.get("vision_provider", "CPUExecutionProvider"),
    }
    try:
        ort = importlib.import_module("onnxruntime")
        report["available_onnx_providers"] = ort.get_available_providers()
    except Exception as exc:
        report["available_onnx_providers"] = []
        report["onnx_provider_probe_error"] = str(exc)[:300]
    try:
        from cardio.imaging import Detector
        manifest, _ = Detector(ROOT, config).manifest()
        report["detector"] = {"registered": True, "name": manifest["name"],
                              "sha256": manifest["sha256"], "trained_declaration": manifest.get("trained", False)}
    except Exception as exc:
        report["detector"] = {"registered": False, "reason": str(exc)[:300]}
    try:
        from cardio.segmentation import Segmenter
        manifest, _ = Segmenter(ROOT, config).manifest()
        report["segmentation"] = {"registered": True, "name": manifest["name"],
                                  "sha256": manifest["sha256"], "clinical_accuracy_verified": False}
    except Exception as exc:
        report["segmentation"] = {"registered": False, "reason": str(exc)[:300]}
    try:
        from cardio.reference_labels import verify_reference_assets
        metadata = verify_reference_assets(ROOT)
        report["provided_mri_reference"] = {"verified_assets": True, "sha256": metadata["sha256"],
            "shape_zyx": metadata["shape_zyx"], "class_counts": metadata["class_counts"],
            "model_inference": False, "physical_spacing_verified": False}
    except Exception as exc:
        report["provided_mri_reference"] = {"verified_assets": False, "reason": str(exc)[:300]}
    report["lemonade"] = {"available": False, "probed": probe_service, "downloaded_local_models": []}
    if probe_service:
        try:
            from cardio.planner import Planner
            planner = Planner(config)
            entries = planner.models().get("data", [])
            local = [{"id": model.get("id"), "recipe": model.get("recipe"), "downloaded": True,
                      "purpose": purpose}
                     for model in entries for purpose in ("chat", "speech") if planner.is_local(model, purpose)]
            report["lemonade"].update(available=True, downloaded_local_models=local)
            if config.get("planner_model"):
                try:
                    planner.local_model(config["planner_model"])
                    report["lemonade"]["configured_planner_accepted"] = True
                except Exception as exc:
                    report["lemonade"].update(configured_planner_accepted=False, planner_reason=str(exc)[:300])
        except Exception as exc:
            report["lemonade"]["reason"] = str(exc)[:300]
    report["checks"] = {
        "core_dependencies_installed": all(report["packages"][p] for p in CORE_PACKAGES)
                                       and not report["core_import_errors"],
        "windows_wheel_target": os.name == "nt" and sys.version_info[:2] == (3, 11)
                                and platform.machine().lower() in {"amd64", "x86_64"},
        "configured_vision_provider_available": config.get("vision_provider", "CPUExecutionProvider")
                                                in report["available_onnx_providers"],
    }
    report["checks"]["bundled_mri_assets_verified"] = report["provided_mri_reference"]["verified_assets"]
    report["core_demo_ready"] = report["checks"]["core_dependencies_installed"]
    report["workflow_demo_ready"] = report["core_demo_ready"] and report["checks"]["bundled_mri_assets_verified"]
    report["notes"] = [
        "A missing trained CT model or Lemonade service does not prevent the disclosed synthetic workflow demo.",
        "Available providers/model recipes show configuration, not proof that a model executed on the NPU.",
        "Capture runtime offload logs, device context, latency, peak RAM and output checks on the actual AMD PC.",
    ]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-services", action="store_true", help="Skip the loopback Lemonade availability check.")
    args = parser.parse_args(argv)
    result = inspect(probe_service=not args.no_services)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if result["workflow_demo_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
