"""Benchmark registered model only; never substitute synthetic detector."""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cardio.app import create_app

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--case",required=True);p.add_argument("--repeats",type=int,default=10);p.add_argument("--output",default="evidence/target_benchmark.json");args=p.parse_args()
    if not 2<=args.repeats<=100:p.error("repeats must be 2–100")
    app=create_app(recover_interrupted=False);case=app.store.case(args.case)
    if case["modality"]!="CT" or case.get("synthetic"):p.error("Requires a real authorized CT case")
    data=Path(__import__('os').environ.get('CARDIO_DATA_DIR',ROOT/'data'))
    arrays=[np.load(data/s,allow_pickle=False) for s in case['slices']]
    times=[]
    for i in range(args.repeats+1):
        t=time.perf_counter();r=app.detector.detect(arrays,len(arrays)//2,case.get('is_hu',False));elapsed=(time.perf_counter()-t)*1000
        if i:times.append(elapsed)
        else:cold=elapsed
    result={"case_id":case['id'],"repeats":args.repeats,"cold_ms":cold,"median_ms":float(np.median(times)),"p95_ms":float(np.percentile(times,95)),"device":r['device'],"model_sha256":r['model_sha256'],"candidate_count":len(r['candidates']),"scope":"software latency, not clinical accuracy"}
    dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(result,indent=2),encoding='utf-8');print(result)
