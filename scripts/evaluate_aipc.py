"""Evaluate fixed source splits. Gold labels are read only after inference."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio.aipc import AIPCDemo,SECTIONS
from cardio.planner import Planner
from cardio.store import Store


def quantile(values,fraction):
    if not values:return None
    values=sorted(values);index=(len(values)-1)*fraction
    lo=int(index);hi=math.ceil(index)
    return round(values[lo]+(values[hi]-values[lo])*(index-lo),2)


def score(records):
    gold=[r for r in records if r['expected_section'] is not None]
    successes=[r for r in records if r['success']]
    correct=sum(r['success'] and r['predicted_section']==r['expected_section'] for r in gold)
    # Failed or invalid outputs count against accuracy and as false negatives.
    by_class={}
    for label in SECTIONS:
        tp=sum(r['expected_section']==label and r['predicted_section']==label for r in gold)
        fp=sum(r['expected_section']!=label and r['predicted_section']==label for r in gold)
        fn=sum(r['expected_section']==label and r['predicted_section']!=label for r in gold)
        support=sum(r['expected_section']==label for r in gold)
        by_class[label]={'support':support,'tp':tp,'fp':fp,'fn':fn,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
    supported=[v['f1'] for v in by_class.values() if v['support']]
    duration=[r['elapsed_ms'] for r in successes]
    fixtures=[r for r in records if r['expected_missing'] is not None]
    return {'attempted':len(records),'accepted_outputs':len(successes),'failed_outputs':len(records)-len(successes),
            'section_labeled':len(gold),'section_correct':correct,'section_accuracy':correct/len(gold) if gold else None,
            'macro_f1_supported_classes':statistics.mean(supported) if supported else None,'per_class':by_class,
            'exact_full_turn_validation_passed':sum(r['quote_integrity_passed'] for r in successes),
            'latency_ms':{'p50':quantile(duration,.5),'p95':quantile(duration,.95),'population':'accepted outputs; includes extraction/validation, excludes file persistence'},
            'synthetic_fixture_count':len(fixtures),'synthetic_missing_field_matches':sum(r['success'] and r['missing_fields']==r['expected_missing'] for r in fixtures),
            'clinical_correctness_measured':False,'ttft_measured':False,'npu_execution_verified':False}


def evaluate(mode='baseline', model=None, split='acceptance', limit=None):
    if mode not in {'baseline','llm'}:raise ValueError('Unknown evaluation mode.')
    if mode=='llm' and not model:raise ValueError('A local model ID is required.')
    if limit is not None and (isinstance(limit,bool) or limit<1):raise ValueError('limit must be positive')
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    records=[]
    with tempfile.TemporaryDirectory() as temp:
        service=AIPCDemo(ROOT,temp,Store(Path(temp)/'evaluation.sqlite3'),Planner(config))
        cases=[c for c in service.cases().values() if c['split']==split or (split=='acceptance' and c['split'] in {'test_1','test_2','synthetic_demo'})]
        if limit:cases=cases[:limit]
        if not cases:raise ValueError('No cases selected; an empty run is not a successful evaluation.')
        for case in cases:
            record={'case_id':case['id'],'split':case['split'],'success':False,'predicted_section':None,'missing_fields':None,'quote_integrity_passed':False}
            try:
                result=service.generate(case['id'],mode,model,'evaluation-script')
                record.update(success=True,predicted_section=result['predicted_section'],missing_fields=sorted(result['intake_check']['missing_fields']),
                              quote_integrity_passed=all(e['quote_verified'] for e in result['evidence']),elapsed_ms=result['engine']['elapsed_ms'],engine=result['engine'])
            except Exception as exc:record['error']=str(exc)[:300]
            # Ground truth is intentionally accessed after model generation.
            record['expected_section']=case['gold'].get('section')
            record['expected_missing']=sorted(case['gold']['expected_missing']) if 'expected_missing' in case['gold'] else None
            records.append(record)
    engines=Counter(r['engine']['source'] for r in records if r.get('success'))
    output={'schema':'cardio-aipc-evaluation/1','created_at':datetime.now(timezone.utc).isoformat(),'mode':mode,'requested_model':model,
            'split':split,'limit':limit,'host':{'os':platform.system(),'architecture':platform.machine()},
            'dataset_sha256':hashlib.sha256((ROOT/'datasets/mts_dialog/cases.jsonl').read_bytes()).hexdigest(),
            'summary':score(records),'actual_engine_counts':dict(engines),'local_model_outputs':engines['lemonade_local_model'],'records':records,
            'interpretation':'Research section classification and quote-integrity checks only. This is not a clinical accuracy, workflow time-saving or NPU speedup measurement. Different models/quantizations are configuration comparisons.'}
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('baseline','llm'),default='baseline')
    parser.add_argument('--model');parser.add_argument('--split',choices=('test_1','test_2','synthetic_demo','acceptance'),default='acceptance')
    parser.add_argument('--limit',type=int);parser.add_argument('--output',type=Path,default=ROOT/'evidence/aipc_baseline_results.json')
    args=parser.parse_args(argv)
    if args.mode=='llm' and not args.model:parser.error('--model is required with --mode llm')
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    output=evaluate(args.mode,args.model,args.split,args.limit)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output['summary'],ensure_ascii=False,indent=2))
    return 1 if output['summary']['failed_outputs'] else 0


if __name__=='__main__':raise SystemExit(main())
