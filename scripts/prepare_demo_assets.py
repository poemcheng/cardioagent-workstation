"""Restore the source-checkout demo arrays from the verified public bundle."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio.bundled_assets import ensure_demo_assets
if __name__=='__main__':
    restored=ensure_demo_assets(ROOT)
    print('Verified demo arrays restored.' if restored else 'Demo arrays already present.')
