from __future__ import annotations
import argparse, logging, os
from dotenv import load_dotenv
from .config import Settings
from .engine import TempestEngine

def main():
    load_dotenv()
    p=argparse.ArgumentParser()
    p.add_argument("--once",action="store_true")
    p.add_argument("--log-level",default=os.getenv("LOG_LEVEL","INFO"))
    a=p.parse_args()
    logging.basicConfig(level=getattr(logging,a.log_level.upper(),logging.INFO),format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    engine=TempestEngine(Settings())
    if a.once:
        engine.bootstrap(); engine.tick()
    else:
        engine.run()

if __name__=="__main__": main()
