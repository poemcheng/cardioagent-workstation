"""Register an explicitly supplied ONNX; no weights downloaded automatically."""
import argparse
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cardio.imaging import sha256

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("model",type=Path);p.add_argument("--sha256",required=True);p.add_argument("--name",default="ST-CAC-YOLO")
    p.add_argument("--trained",action="store_true",help="Declaration only, not a validation certificate")
    p.add_argument("--provenance",required=True,help="Training run ID, export settings and source record")
    args=p.parse_args();source=args.model.resolve()
    if source.suffix.lower()!=".onnx" or sha256(source).lower()!=args.sha256.lower():p.error("ONNX path or SHA256 mismatch")
    import onnxruntime as ort
    session=ort.InferenceSession(str(source),providers=["CPUExecutionProvider"])
    inp=session.get_inputs();out=session.get_outputs()
    if len(inp)!=1 or inp[0].shape!=[1,3,512,512] or inp[0].type!="tensor(float)":p.error("Expected a single float32 [1,3,512,512] input")
    if len(out)!=1 or len(out[0].shape)!=3 or out[0].shape[0]!=1 or out[0].shape[1]!=5:p.error("Expected YOLOv8 one-class raw [1,5,N] output (no embedded NMS)")
    dest=ROOT/"models"/"st_cac.onnx"
    if source!=dest:shutil.copy2(source,dest)
    manifest={"file":dest.name,"name":args.name,"sha256":sha256(dest),"input_size":512,"channels":"previous-center-next","output":"yolov8-raw-one-class","trained":args.trained,"provenance":args.provenance,"normalization":"8-bit grayscale / 255; no per-slice min-max; NCHW; previous,center,next"}
    (ROOT/"models/detector.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Registered",manifest["name"],manifest["sha256"])
