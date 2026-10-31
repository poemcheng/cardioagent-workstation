"""Inspect actual software and device availability; never infer NPU execution."""
import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio.planner import Planner
from cardio.imaging import Detector


def inspect():
    result={"platform":platform.platform(),"architecture":platform.machine(),"python":platform.python_version(),"processor":platform.processor(),"packages":{},"target_hardware_verified":False,"npu_execution_verified":False,"runtime":"local CPU baseline; optional Lemonade iGPU planner"}
    for name in ("Flask","waitress","numpy","Pillow","pydicom","onnxruntime","onnxruntime-directml"):
        try: result["packages"][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: result["packages"][name]=None
    if os.name=="nt":
        command="Get-CimInstance Win32_Processor | Select-Object Name; Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory; Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion"
        try: result["windows_devices"]=subprocess.run(["powershell.exe","-NoProfile","-Command",command],capture_output=True,text=True,timeout=15).stdout.strip()
        except Exception as exc: result["windows_devices_error"]=str(exc)
    try:
        import onnxruntime as ort
        result["vision_available_providers"]=ort.get_available_providers()
    except ImportError: result["vision_available_providers"]=[]
    config=json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
    try:
        manifest,_=Detector(ROOT,config).manifest()
        result["detector"]={"registered":True,"name":manifest["name"],"sha256":manifest["sha256"],"trained_declaration":manifest.get("trained",False)}
    except Exception as exc: result["detector"]={"registered":False,"reason":str(exc)}
    try: result["lemonade"]={"available":True,"models":Planner(config).models()}
    except Exception as exc: result["lemonade"]={"available":False,"error":str(exc)}
    result["checks"]={"python_3_11_or_3_12":sys.version_info[:2] in {(3,11),(3,12)},"x64":platform.machine().lower() in {"amd64","x86_64"},"windows_target":os.name=="nt"}
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output");args=parser.parse_args()
    result=inspect();text=json.dumps(result,ensure_ascii=False,indent=2)
    print(text)
    if args.output:
        p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding="utf-8")
