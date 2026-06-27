import requests
import json
import time

ARENA_BASE = "https://arena.dev.fun/api/arena"
COMPETITION_ID = "cmquozxxm2vmlt6mn6j7gzgdf"

def decide(table):
    can_check = table.get("canCheck", False)
    can_call = table.get("canCall", False)
    can_bet = table.get("canBet", False)
    can_raise = table.get("canRaise", False)
    can_allin = table.get("canAllIn", False)
    min_bet = table.get("minBet") or table.get("minRaiseTo") or 0
    max_commit = table.get("maxCommit") or 0
    call_amount = table.get("callAmount") or table.get("callChips") or 0
    pot = table.get("pot") or 0
    street = table.get("street") or "preflop"

    print(f"Street:{street} pot:{pot} callAmount:{call_amount} minBet:{min_bet}")

    # AGGRESSION: Always bet or raise when possible
    # Only fold if call is more than 30% of pot (pot odds bad)
    
    if can_raise and min_bet:
        amt = min(min_bet * 3, max_commit) if max_commit else min_bet * 3
        return {"action":"raise","amount":amt,"reasoning":"Aggressive raise, applying pressure.","message":"Rimuru applies pressure."}
    
    if can_bet and min_bet:
        amt = min(int(pot * 0.75), max_commit) if max_commit else int(pot * 0.75)
        amt = max(amt, min_bet)
        return {"action":"bet","amount":amt,"reasoning":"Aggressive bet for value.","message":"Rimuru bets."}
    
    if can_check:
        return {"action":"check","reasoning":"Checking to see next card.","message":"Rimuru checks."}
    
    if can_call:
        # Only fold if call is ridiculously expensive (more than pot size)
        if call_amount > pot * 1.5 and pot > 0:
            return {"action":"fold","reasoning":"Call too expensive relative to pot.","message":"Rimuru folds."}
        return {"action":"call","reasoning":"Calling, staying in the hand.","message":"Rimuru stays in."}
    
    return {"action":"fold","reasoning":"No other option.","message":"Rimuru folds."}

def play_loop(headers):
    print("Rimuru — AGGRESSIVE mode active...")
    while True:
        try:
            r = requests.get(
                f"{ARENA_BASE}/texas/pending-actions?competitionId={COMPETITION_ID}",
                headers=headers, timeout=10
            )
            raw = r.json()
            tables = raw if isinstance(raw, list) else raw.get("tables") or raw.get("activeTables") or []
            acted = False
            for table in tables:
                can_act = (table.get("canFold") or table.get("canCall") or 
                          table.get("canCheck") or table.get("canBet") or table.get("canRaise"))
                if not can_act:
                    continue
                table_id = table.get("tableId") or table.get("id")
                print(f"Our turn at {table_id}!")
                action = decide(table)
                print(f"Sending: {action}")
                resp = requests.post(
                    f"{ARENA_BASE}/texas/action",
                    headers=headers,
                    json={"tableId": table_id, "competitionId": COMPETITION_ID, **action},
                    timeout=10
                )
                print("Result:", resp.json())
                acted = True
            if not acted:
                print("Waiting...", end="\r")
            time.sleep(0.5)
        except Exception as e:
            print("Error:", e)
            time.sleep(3)

creds = json.load(open(".arena-credentials"))
headers = {"x-arena-api-key": creds["apiKey"]}
me = requests.get(f"{ARENA_BASE}/agent/me", headers=headers).json()
print(f"Agent: {me.get('name')} — AGGRESSIVE mode")
play_loop(headers)
