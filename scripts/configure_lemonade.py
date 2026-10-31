"""Select already-downloaded local Lemonade models; never download weights."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cardio.planner import Planner


def safe_metadata(model):
    return {key: model.get(key) for key in ("id", "recipe", "downloaded")}


def write_config(path, config):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".config-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(config, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List eligible local chat and speech models with recipe metadata.")
    parser.add_argument("--model", help="Set the exact local /models ID for chat and tool planning.")
    parser.add_argument("--speech-model", help="Set the exact local /models ID for WAV transcription.")
    args = parser.parse_args(argv)
    if not (args.list or args.model or args.speech_model):
        parser.error("Choose --list, --model MODEL_ID, or --speech-model MODEL_ID.")
    path = ROOT / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        planner = Planner(config)
        payload = planner.models()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Local /models did not return a data array.")
        models = payload["data"]
        # Validate all requested assignments before changing either setting.
        if args.model:
            planner.local_model(args.model, purpose="chat")
        if args.speech_model:
            planner.local_model(args.speech_model, purpose="speech")
        if args.list:
            print(json.dumps({
                "chat_models": [safe_metadata(model) for model in models if planner.is_local(model, "chat")],
                "speech_models": [safe_metadata(model) for model in models if planner.is_local(model, "speech")],
                "downloads_performed": False,
            }, ensure_ascii=False, indent=2))
        if args.model or args.speech_model:
            if args.model:
                config["planner_model"] = args.model
            if args.speech_model:
                config["dictation_model"] = args.speech_model
            write_config(path, config)
            print("Saved local model selection. Restart CardioAgent to apply. No weights or drivers were downloaded.")
        return 0
    except Exception as exc:
        parser.exit(2, f"Local model configuration failed: {str(exc)[:500]}\n"
                       "Start Lemonade locally and load/download the intended model before configuring it.\n")


if __name__ == "__main__":
    raise SystemExit(main())
