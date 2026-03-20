"""
scheduler.py - entry point called by Render.com cron every 6 hours
"""
import logging, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from agent import run_agent

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    logging.info("Cron triggered - starting lead agent run")
    result = run_agent(leads_per_run=50)
    logging.info(f"Run complete. {result['total']} leads added to GHL.")
