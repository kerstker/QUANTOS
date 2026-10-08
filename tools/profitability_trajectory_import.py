"""Import a bounded pair of normalized trajectories into separate diagnostics."""
from pathlib import Path
import argparse
import hashlib
import json

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.profitability_trajectory import compare_trajectories
from tools.profitability_study import write_new


def import_trajectories(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if source==output or source in output.parents:raise ValueError('separate output required')
    output.mkdir(parents=True,exist_ok=False)
    identity=None
    write_new(output/'preregistration.json',{'stage':'P5','source_path':str(source),
              'objective':'FIXED_ACTION_QUANTITY_TIME_STATE_ERROR','economic_eligible':False})
    try:
        if not source.is_file() or source.stat().st_size>4000000:raise ValueError('bounded trajectory file required')
        raw=source.read_bytes();identity=hashlib.sha256(raw).hexdigest()
        write_new(output/'input-identity.json',{'input_sha256':identity})
        data=load_json_without_duplicate_keys(raw)
        if not isinstance(data,dict) or set(data)!={'observed','simulated'}:raise ValueError('paired normalized traces required')
        report=compare_trajectories(data['observed'],data['simulated'])
        result={'stage':'P5','input_sha256':identity,**report}
        write_new(output/'result.json',result);return result
    except Exception as exc:
        write_new(output/'failure.json',{'stage':'P5','input_sha256':identity,'reason':type(exc).__name__,
                                        'detail':str(exc),'economic_eligible':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=import_trajectories(args.input,args.output)
    print(json.dumps({'status':result['status'],'venue_receipts_verified':False}))


if __name__=='__main__':main()
